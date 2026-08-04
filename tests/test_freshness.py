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


def test_dead_refs_mark_stale(git_repo):
    _setup_code(git_repo)
    body = "Uses `src/auth.py`, `gone_function` and `src/missing.py`."
    evidence = compute_freshness(str(git_repo), body, datetime.now(timezone.utc))
    assert evidence["status"] == "stale"
    assert "gone_function" in evidence["invalid_refs"]
    assert "src/missing.py" in evidence["invalid_refs"]
    assert "no longer exist" in evidence["reasons"][0]


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
