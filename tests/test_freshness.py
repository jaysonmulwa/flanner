"""Tests for the freshness evidence engine."""

import os
import subprocess
from datetime import datetime, timedelta, timezone

from flanner.freshness import compute_freshness, extract_refs, resolve_anchor


def _commit(repo, message="change", when=None):
    env = dict(os.environ)
    if when is not None:
        stamp = when.isoformat()
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message],
        cwd=repo,
        check=True,
        env=env,
    )


def _setup_code(git_repo, when=None):
    src = git_repo / "src"
    src.mkdir()
    (src / "auth.py").write_text("def rotate_tokens():\n    pass\n")
    _commit(git_repo, "add auth", when=when)
    return git_repo


def test_extract_refs_paths_and_symbols():
    body = (
        "Plan cites `src/auth.py` and the `rotate_tokens` helper.\n"
        "```bash\ncat src/auth.py\n```\n"
        "Ignore `https://example.com/a.py` and plain `word`.\n"
    )
    paths, symbols = extract_refs(body)
    assert paths == ["src/auth.py"]
    assert symbols == ["rotate_tokens"]


def test_fresh_plan(git_repo):
    _setup_code(git_repo)
    body = "Uses `src/auth.py` and `rotate_tokens`."
    evidence = compute_freshness(str(git_repo), body, datetime.now(timezone.utc))
    assert evidence["status"] == "fresh"
    assert evidence["invalid_refs"] == []
    assert evidence["churn_scope"] == "paths"


def _add_then_remove_legacy(git_repo):
    """Something that really was here and really is gone."""
    (git_repo / "src" / "legacy.py").write_text("def gone_function():\n    pass\n")
    _commit(git_repo, "add legacy")
    (git_repo / "src" / "legacy.py").unlink()
    _commit(git_repo, "drop legacy")


def test_dead_refs_mark_stale(git_repo):
    """Drift: the citation resolved once and does not now.

    Previously this test cited names that had never existed, which is the
    bug it was meant to guard against rather than an example of it.
    """
    _setup_code(git_repo)
    _add_then_remove_legacy(git_repo)

    body = "Uses `src/auth.py`, `gone_function` and `src/legacy.py`."
    evidence = compute_freshness(str(git_repo), body, datetime.now(timezone.utc))

    assert evidence["status"] == "stale"
    assert "gone_function" in evidence["invalid_refs"]
    assert "src/legacy.py" in evidence["invalid_refs"]
    assert "no longer exist" in evidence["reasons"][0]


def test_a_citation_that_was_never_here_is_not_drift(git_repo):
    """Env vars, external constants and another repo's files are not drift.

    Found by running the engine over this project's own plans: 21 of 21
    invalid refs were of this kind, and none had ever appeared in history,
    so every stale verdict in the corpus was a false alarm. One invalid ref
    returns stale before churn or age is consulted, so the discrimination
    has to happen before the verdict rather than after it.
    """
    _setup_code(git_repo)
    body = (
        "Set `MESH_PROVIDER_TOKEN`, deploy per `iroh-relay/src/main.rs`, "
        "and sign with `ED25519_SHA_512`. Our code is `src/auth.py`."
    )

    evidence = compute_freshness(str(git_repo), body, datetime.now(timezone.utc))

    assert evidence["invalid_refs"] == []
    assert evidence["status"] != "stale"


def test_the_history_check_is_bounded_and_fails_open(git_repo):
    """A pickaxe walks all history exactly when the term was never there.

    Past the cap a citation is left unflagged rather than assumed dead,
    which is the same fail-open direction the rest of this module takes:
    the one that cannot invent an alarm.
    """
    from flanner import freshness
    from flanner.freshness import _HistoryBudget

    _setup_code(git_repo)
    _add_then_remove_legacy(git_repo)
    freshness.clear_cache()

    # Two symbols that were both really here and are both really gone, so
    # either would answer True if the budget allowed the walk. Asking the
    # same one twice would not prove the cap any more, because an answer
    # already known is now returned without spending a lookup on it — see
    # the memo test below.
    (git_repo / "src" / "other.py").write_text("def second_gone():\n    pass\n")
    _commit(git_repo, "add other")
    (git_repo / "src" / "other.py").unlink()
    _commit(git_repo, "drop other")

    budget = _HistoryBudget(str(git_repo), limit=1)
    assert budget.ever_had_symbol("gone_function") is True
    assert budget.ever_had_symbol("second_gone") is False  # budget spent


def test_a_symbol_already_looked_up_costs_nothing(git_repo):
    """The pickaxe is the most expensive call this module makes.

    `git log --all -S` walks every ref and measured ~370ms a call against a
    real store; twenty of them were most of a nine-second page. Its answer
    cannot change while HEAD does not, and two plans citing the same
    identifier is the ordinary case in one repository.
    """
    from flanner import freshness
    from flanner.freshness import _HistoryBudget

    _setup_code(git_repo)
    _add_then_remove_legacy(git_repo)
    freshness.clear_cache()

    calls = []
    real = freshness._git

    def counted(root, *args):
        calls.append(args[0])
        return real(root, *args)

    freshness._git = counted
    try:
        first = _HistoryBudget(str(git_repo), limit=4)
        assert first.ever_had_symbol("gone_function") is True
        pickaxes = calls.count("log")

        # A second plan in the same repository asks the same question. It
        # gets the same answer and spends neither a git process nor its own
        # budget, which is what leaves that budget for symbols nobody has
        # looked up yet.
        second = _HistoryBudget(str(git_repo), limit=4)
        assert second.ever_had_symbol("gone_function") is True
        assert calls.count("log") == pickaxes, "the pickaxe ran twice for one answer"
    finally:
        freshness._git = real


def test_churn_marks_suspect(git_repo):
    _setup_code(git_repo, when=datetime.now(timezone.utc) - timedelta(days=10))
    authored = datetime.now(timezone.utc) - timedelta(days=5)
    churn_day = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(21):
        (git_repo / "src" / "auth.py").write_text(f"def rotate_tokens():\n    return {i}\n")
        _commit(git_repo, f"change {i}", when=churn_day)
    evidence = compute_freshness(str(git_repo), "Uses `src/auth.py`.", authored)
    assert evidence["status"] == "suspect"
    assert evidence["commits_since_anchor"] >= 20


def test_old_plan_is_aging(git_repo):
    _setup_code(git_repo)
    authored = datetime.now(timezone.utc) - timedelta(days=90)
    evidence = compute_freshness(str(git_repo), "No code refs here.", authored)
    assert evidence["status"] in ("aging", "suspect")
    assert evidence["age_days"] >= 89


def test_no_git_fails_open(tmp_path):
    evidence = compute_freshness(str(tmp_path), "Uses `src/auth.py`.", datetime.now(timezone.utc))
    assert evidence["git_available"] is False
    assert evidence["status"] == "fresh"
    assert "git unavailable" in evidence["reasons"][0]


def test_anchor_predates_history(git_repo):
    _setup_code(git_repo)
    old = datetime.now(timezone.utc) - timedelta(days=3650)
    assert resolve_anchor(str(git_repo), old) is None
    evidence = compute_freshness(str(git_repo), "Uses `src/auth.py`.", old)
    assert evidence["anchored_at_commit"] is None
    assert evidence["status"] in ("aging", "suspect")


def test_anchor_is_resolved_in_utc_not_local_time(git_repo):
    """A naive timestamp must not be handed to git as local time.

    Version timestamps are stored naive-UTC to match the SQLite columns.
    Passing one through unqualified shifts the cutoff by the machine's
    offset, which silently mis-anchors churn for everyone not on UTC, and
    west of UTC makes a stale plan look fresh. Regression only reproduces
    on a machine whose local time is not UTC.
    """
    _setup_code(git_repo)
    naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert resolve_anchor(str(git_repo), naive_utc) is not None


def test_aware_and_naive_timestamps_anchor_identically(git_repo):
    _setup_code(git_repo)
    aware = datetime.now(timezone.utc)
    assert resolve_anchor(str(git_repo), aware) == resolve_anchor(
        str(git_repo), aware.replace(tzinfo=None)
    )
