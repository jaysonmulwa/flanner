"""
Plan freshness: evidence-based drift detection.

A plan is born true and decays as the code moves on. This module computes,
on demand, the evidence for how far a plan may have drifted:

- referenced paths and symbols, extracted from the plan's code spans
- invalid refs: citations that no longer exist in the repo
- an anchor commit (latest commit at/before the version's authored time)
- churn: commits since the anchor, scoped to the files the plan cites
  when possible, whole repo otherwise

Nothing is stored; every field is derived from the plan text plus git, so
every status can show its reasons. All git access is read-only and fails
open: no git means the status degrades to age-only, never an exception.
"""

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any

from .utils import utcnow

# Thresholds behind each status. ponytail: opinionated starting values,
# still untuned against real corpus data before exposing as configuration.
SUSPECT_CHURN_PATHS = 20  # commits touching cited files
SUSPECT_CHURN_REPO = 60  # commits anywhere (only when scope is unknown)
AGING_CHURN = 5
AGING_DAYS = 45
MAX_REFS = 40  # cap per kind; keeps git grep loops bounded
# A pickaxe search walks all history when the term was never present, which
# is the common case here. Measured at ~0.6s over 133 commits, so the count
# is bounded rather than the timeout relied on.
# ponytail: a flat cap, and refs beyond it are simply not flagged. A cache
# keyed on (repo head, term) is the upgrade if plans start citing more
# unresolvable names than this.
MAX_HISTORY_CHECKS = 12

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_FENCE_RE = re.compile(r"^(?:```|~~~)[^\n]*\n(.*?)^(?:```|~~~)\s*$", re.M | re.S)
# a path: contains a slash, ends in a short extension, no URL scheme
_PATH_RE = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)+\.[A-Za-z0-9]{1,8}$")
# a symbol: snake_case identifier with at least one underscore
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+$")


def _git(project_root: str, *args: str) -> str | None:
    """Run a read-only git command; None on any failure (fail open)."""
    try:
        # Fixed argument list, no shell, and git is resolved from PATH by design.
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no untrusted input
            ["git", *args],  # noqa: S607 - git resolved from PATH on purpose; fixed argv, no shell
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def extract_refs(body: str) -> tuple[list[str], list[str]]:
    """Pull cited paths and symbols out of a plan's code spans.

    Inline spans yield paths and symbols; fenced blocks yield paths only
    (prose and shell output inside fences make symbol matching too noisy).
    """
    paths: list[str] = []
    symbols: list[str] = []

    def _consider(token: str, allow_symbols: bool) -> None:
        token = token.strip().strip(",;:()[]{}\"'").lstrip("./")
        if not token or "://" in token:
            return
        if _PATH_RE.match(token):
            if token not in paths and len(paths) < MAX_REFS:
                paths.append(token)
        elif allow_symbols and _SYMBOL_RE.match(token):
            if token not in symbols and len(symbols) < MAX_REFS:
                symbols.append(token)

    for span in _CODE_SPAN_RE.findall(body):
        for token in span.split():
            _consider(token, allow_symbols=True)
    for block in _FENCE_RE.findall(body):
        for token in block.split():
            _consider(token, allow_symbols=False)
    return paths, symbols


def resolve_anchor(project_root: str, authored_at: datetime | None) -> str | None:
    """Latest commit at or before the plan version's authored time.

    The timestamp is made explicitly UTC first. Version timestamps are
    stored naive-UTC to match the SQLite columns, and git reads a bare
    timestamp as *local* time, so passing one through unqualified shifts
    the cutoff by the machine's offset: east of UTC the anchor is too old
    and plans look staler than they are, while west of UTC commits made
    after the plan was written fall inside the window and a stale plan
    looks fresh.
    """
    if authored_at is None:
        return None
    stamp = authored_at if authored_at.tzinfo else authored_at.replace(tzinfo=timezone.utc)
    return _git(project_root, "rev-list", "-1", f"--before={stamp.isoformat()}", "HEAD")


class _HistoryBudget:
    """Whether a missing citation was ever part of this repository.

    This is the difference between drift and a reference to the outside
    world, and without it the two are indistinguishable. A symbol that used
    to be here and is gone is exactly the signal this module exists to
    raise. One that was never here is an environment variable, an external
    API constant, or a file in another repository, and reporting it as "no
    longer exists" is a false alarm.

    It matters because the verdict is unforgiving: a single invalid ref
    returns `stale` before churn or age is consulted. Measured on this
    project's own plans, every invalid ref was of the second kind — 21 of
    21, none of which had ever appeared in history — so every stale verdict
    in the corpus was wrong.

    A budget rather than a bare function because the check is a pickaxe
    search, which walks all of history precisely when the term was never
    present. Past the cap, refs are left unflagged: the same fail-open
    direction the rest of this module takes, and the direction that cannot
    invent an alarm.
    """

    def __init__(self, project_root: str, limit: int = MAX_HISTORY_CHECKS) -> None:
        self._root = project_root
        self._left = limit

    def ever_had_path(self, path: str) -> bool:
        """A pathspec lookup: did any commit ever touch this file?"""
        if not self._spend():
            return False
        return bool(_git(self._root, "log", "--all", "--oneline", "--max-count=1", "--", path))

    def ever_had_symbol(self, symbol: str) -> bool:
        """A pickaxe: did any commit ever add or remove this text?

        Deliberately not the same call as ``ever_had_path``. ``-S`` searches
        file *contents*, so it never finds a deleted file by its name, and a
        pathspec never finds a deleted identifier. Using one for both silently
        stops flagging half the drift this module exists to report.
        """
        if not self._spend():
            return False
        return bool(_git(self._root, "log", "--all", "--oneline", "--max-count=1", "-S", symbol))

    def _spend(self) -> bool:
        if self._left <= 0:
            return False
        self._left -= 1
        return True


def _check_symbol(project_root: str, symbol: str) -> list[str]:
    """Tracked files containing the symbol; None result folds to []."""
    out = _git(project_root, "grep", "-l", "-F", "-e", symbol, "--", ".")
    return out.splitlines() if out else []


def _bad_refs(
    project_root: str, paths: list[str], symbols: list[str]
) -> tuple[list[str], list[str]]:
    """References the plan makes that the repository no longer honours.

    A path or symbol that is simply absent proves nothing — the plan may
    describe work not started. It counts as invalid only if git history
    shows it once existed, which is what distinguishes "not yet" from
    "gone", and is why this needs a history budget rather than a stat call.

    Returns the invalid references, and the files the live symbols were
    found in (which widen the churn scope below).
    """
    invalid: list[str] = []
    found_files: list[str] = []
    budget = _HistoryBudget(project_root)

    for path in paths:
        if not os.path.exists(os.path.join(project_root, path)) and budget.ever_had_path(path):
            invalid.append(path)

    for symbol in symbols:
        found_in = _check_symbol(project_root, symbol)
        if found_in:
            found_files.extend(f for f in found_in if f not in found_files)
        elif budget.ever_had_symbol(symbol):
            invalid.append(symbol)

    return invalid, found_files


def _churn_since(
    project_root: str, anchor: str | None, scope_paths: list[str]
) -> tuple[int | None, str]:
    """How many commits landed since the plan was anchored.

    Narrowed to the files the plan actually names where it names any, because
    a repository-wide count says a plan is stale whenever anybody committed
    anything. The scope is reported alongside the number so a reader can tell
    which question was answered.
    """
    args = ["rev-list", "--count", f"{anchor}..HEAD" if anchor else "HEAD"]
    if scope_paths:
        out = _git(project_root, *args, "--", *scope_paths)
        scope = "paths"
    else:
        out = _git(project_root, *args)
        scope = "repo"
    return (int(out) if out and out.isdigit() else None), scope


def _age_days(authored_at: datetime | None) -> int | None:
    """How long ago the version was written, in whole days."""
    if authored_at is None:
        return None
    # utcnow() is naive (matches the SQLite columns); tool callers may pass
    # aware datetimes, so normalize to whichever authored_at uses.
    now = utcnow()
    if authored_at.tzinfo is not None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, (now - authored_at).days)


def compute_freshness(
    project_root: str, body: str, authored_at: datetime | None
) -> dict[str, Any]:
    """Compute the full evidence record for one plan version."""
    paths, symbols = extract_refs(body)
    git_available = _git(project_root, "rev-parse", "HEAD") is not None

    invalid_refs, symbol_files = (
        _bad_refs(project_root, paths, symbols) if git_available else ([], [])
    )
    anchor = resolve_anchor(project_root, authored_at) if git_available else None
    scope_paths = [p for p in paths if p not in invalid_refs] + symbol_files
    commits_since, churn_scope = (
        _churn_since(project_root, anchor, scope_paths) if git_available else (None, None)
    )
    age_days = _age_days(authored_at)

    status, reasons = _status(
        invalid_refs, commits_since, churn_scope, age_days, anchor, git_available
    )
    return {
        "status": status,
        "reasons": reasons,
        "git_available": git_available,
        "anchored_at_commit": anchor[:12] if anchor else None,
        "referenced_paths": paths,
        "referenced_symbols": symbols,
        "invalid_refs": invalid_refs,
        "commits_since_anchor": commits_since,
        "churn_scope": churn_scope,
        "age_days": age_days,
    }


def _status(
    invalid_refs: list[str],
    commits_since: int | None,
    churn_scope: str | None,
    age_days: int | None,
    anchor: str | None,
    git_available: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not git_available:
        reasons.append("git unavailable, judged on age only")
        if age_days is not None and age_days > AGING_DAYS:
            reasons.append(f"authored {age_days} days ago")
            return "aging", reasons
        return "fresh", reasons

    if invalid_refs:
        shown = ", ".join(invalid_refs[:3])
        more = f" (+{len(invalid_refs) - 3} more)" if len(invalid_refs) > 3 else ""
        noun = (
            "reference that no longer exists"
            if len(invalid_refs) == 1
            else "references that no longer exist"
        )
        reasons.append(f"cites {len(invalid_refs)} {noun}: {shown}{more}")
        return "stale", reasons

    if commits_since is not None:
        where = "cited files" if churn_scope == "paths" else "the repo"
        since = f"since anchor {anchor[:7]}" if anchor else "in history"
        reasons.append(f"{commits_since} commits touched {where} {since}")
        threshold = SUSPECT_CHURN_PATHS if churn_scope == "paths" else SUSPECT_CHURN_REPO
        if commits_since >= threshold:
            return "suspect", reasons
        if commits_since >= AGING_CHURN:
            return "aging", reasons

    if age_days is not None:
        reasons.append(f"authored {age_days} days ago")
        if age_days > AGING_DAYS:
            return "aging", reasons

    return "fresh", reasons
