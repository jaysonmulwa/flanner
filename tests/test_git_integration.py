"""Unit tests for flanner.git_integration."""

import subprocess

import flanner.git_integration as gi
from flanner.git_integration import (
    _check_gitignore_manually,
    find_git_root,
    get_git_status,
    get_gitignore_path,
    is_path_ignored,
    is_pattern_in_gitignore,
    read_gitignore,
    remove_from_gitignore,
    update_gitignore,
    update_plan_directory_in_gitignore,
    validate_git_repo,
)


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def test_find_git_root(tmp_path, outside_any_repo):
    repo = _git_repo(tmp_path)
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    found = find_git_root(str(nested))
    assert found is not None
    assert found.replace("\\", "/").endswith("repo")

    # The other half. It used to read `tmp_path` under a comment saying temp
    # dirs live outside any checkout, which is not true of every machine.
    assert find_git_root(str(outside_any_repo)) is None


def test_find_git_root_defaults_to_cwd(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    monkeypatch.chdir(repo)
    found = find_git_root()
    assert found is not None


def test_validate_git_repo(tmp_path):
    repo = _git_repo(tmp_path)
    assert validate_git_repo(str(repo)) is True
    assert validate_git_repo(str(tmp_path)) is False


def test_get_gitignore_path(tmp_path):
    path = get_gitignore_path(str(tmp_path))
    assert path.replace("\\", "/").endswith(".gitignore")


def test_read_gitignore_missing(tmp_path):
    assert read_gitignore(str(tmp_path)) == []


def test_update_gitignore_add_and_idempotent(tmp_path):
    repo = _git_repo(tmp_path)
    assert update_gitignore(str(repo), ".plans/") is True
    content = (repo / ".gitignore").read_text()
    assert ".plans/" in content
    assert "# MCP Plan Manager - Auto-generated" in content

    # Second add is a no-op
    assert update_gitignore(str(repo), ".plans/") is False
    # Trailing-slash-insensitive match
    assert update_gitignore(str(repo), ".plans") is False


def test_update_gitignore_custom_comment_and_existing_content(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".gitignore").write_text("*.log")  # no trailing newline
    assert update_gitignore(str(repo), ".plans/", comment="Custom") is True
    content = (repo / ".gitignore").read_text()
    assert "*.log\n" in content
    assert "# Custom" in content


def test_is_pattern_in_gitignore_skips_comments(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".gitignore").write_text("# .plans/\n\n.plans/\n")
    assert is_pattern_in_gitignore(str(repo), ".plans/") is True
    assert is_pattern_in_gitignore(str(repo), "other/") is False


def test_remove_from_gitignore(tmp_path):
    repo = _git_repo(tmp_path)
    update_gitignore(str(repo), ".plans/")
    assert remove_from_gitignore(str(repo), ".plans/") is True
    assert is_pattern_in_gitignore(str(repo), ".plans/") is False
    # Removing again finds nothing
    assert remove_from_gitignore(str(repo), ".plans/") is False


def test_remove_from_gitignore_missing_file(tmp_path):
    assert remove_from_gitignore(str(tmp_path), ".plans/") is False


def test_is_path_ignored_with_git(tmp_path):
    repo = _git_repo(tmp_path)
    update_gitignore(str(repo), ".plans/")
    (repo / ".plans").mkdir()
    (repo / ".plans" / "a.md").write_text("x")
    assert is_path_ignored(str(repo), ".plans/a.md") is True
    assert is_path_ignored(str(repo), "README.md") is False


def test_is_path_ignored_fallback_when_git_missing(tmp_path, monkeypatch):
    repo = tmp_path / "norepo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".plans/\n")

    def boom(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(gi.subprocess, "run", boom)
    assert is_path_ignored(str(repo), ".plans/a.md") is True
    assert is_path_ignored(str(repo), "src/main.py") is False


def test_check_gitignore_manually(tmp_path):
    (tmp_path / ".gitignore").write_text("# comment\n\nbuild\n.plans/\nexact.md\n")
    assert _check_gitignore_manually(str(tmp_path), "exact.md") is True
    assert _check_gitignore_manually(str(tmp_path), ".plans/nested.md") is True
    assert _check_gitignore_manually(str(tmp_path), "build/out.o") is True
    assert _check_gitignore_manually(str(tmp_path), "src/app.py") is False


def test_get_git_status(monkeypatch):
    # Success path: run inside a real repo (subprocess uses process cwd)
    status = get_git_status()
    assert status is None or isinstance(status, str)

    def boom(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(gi.subprocess, "run", boom)
    assert get_git_status() is None


def test_update_plan_directory_in_gitignore(tmp_path):
    repo = _git_repo(tmp_path)
    update_gitignore(str(repo), ".plans/")
    assert update_plan_directory_in_gitignore(str(repo), ".plans/", "docs/plans/") is True
    assert is_pattern_in_gitignore(str(repo), ".plans/") is False
    assert is_pattern_in_gitignore(str(repo), "docs/plans/") is True

    # No old pattern present: just adds the new one
    assert update_plan_directory_in_gitignore(str(repo), "missing/", "other/") is True
    assert is_pattern_in_gitignore(str(repo), "other/") is True
