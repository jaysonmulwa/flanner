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
# tune against dogfood data before exposing as configuration.
SUSPECT_CHURN_PATHS = 20  # commits touching cited files
SUSPECT_CHURN_REPO = 60  # commits anywhere (only when scope is unknown)
AGING_CHURN = 5
AGING_DAYS = 45
MAX_REFS = 40  # cap per kind; keeps git grep loops bounded

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_FENCE_RE = re.compile(r"^(?:```|~~~)[^\n]*\n(.*?)^(?:```|~~~)\s*$", re.M | re.S)
# a path: contains a slash, ends in a short extension, no URL scheme
_PATH_RE = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)+\.[A-Za-z0-9]{1,8}$")
# a symbol: snake_case identifier with at least one underscore
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+$")


def _git(project_root: str, *args: str) -> str | None:
    """Run a read-only git command; None on any failure (fail open)."""
    try:
        result = subprocess.run(
            ["git", *args],
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
    """Latest commit at or before the plan version's authored time."""
    if authored_at is None:
        return None
    return _git(project_root, "rev-list", "-1", f"--before={authored_at.isoformat()}", "HEAD")


def _check_symbol(project_root: str, symbol: str) -> list[str]:
    """Tracked files containing the symbol; None result folds to []."""
    out = _git(project_root, "grep", "-l", "-F", "-e", symbol, "--", ".")
    return out.splitlines() if out else []


def compute_freshness(
    project_root: str, body: str, authored_at: datetime | None
) -> dict[str, Any]:
    """Compute the full evidence record for one plan version."""
    paths, symbols = extract_refs(body)
    git_available = _git(project_root, "rev-parse", "HEAD") is not None

    invalid_refs: list[str] = []
    symbol_files: list[str] = []
    if git_available:
        for path in paths:
            if not os.path.exists(os.path.join(project_root, path)):
                invalid_refs.append(path)
        for symbol in symbols:
            found_in = _check_symbol(project_root, symbol)
            if found_in:
                symbol_files.extend(f for f in found_in if f not in symbol_files)
            else:
                invalid_refs.append(symbol)

    anchor = resolve_anchor(project_root, authored_at) if git_available else None
    scope_paths = [p for p in paths if p not in invalid_refs] + symbol_files
    commits_since = None
    churn_scope = None
    if git_available:
        rev_range = f"{anchor}..HEAD" if anchor else "HEAD"
        args = ["rev-list", "--count", rev_range]
        if scope_paths:
            churn_scope = "paths"
            out = _git(project_root, *args, "--", *scope_paths)
        else:
            churn_scope = "repo"
            out = _git(project_root, *args)
        commits_since = int(out) if out and out.isdigit() else None

    age_days = None
    if authored_at is not None:
        # utcnow() is naive (matches the SQLite columns); tool callers may
        # pass aware datetimes, so normalize to whichever authored_at uses.
        now = utcnow()
        if authored_at.tzinfo is not None:
            now = now.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - authored_at).days)

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
        noun = "reference that no longer exists" if len(invalid_refs) == 1 else "references that no longer exist"
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
