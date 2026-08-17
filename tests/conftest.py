import subprocess

import pytest

from flanner.database import init_database


@pytest.fixture(autouse=True)
def _isolated_flanner_home(tmp_path, monkeypatch):
    """Point FLANNER_HOME at a temp dir for every test.

    Prevents tests from seeing the developer's real ~/.flanner — in
    particular a live daemon.json, which would make MCP write tools forward
    to a running `flanner web` instead of executing in-process.
    """
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "flanner-home"))


@pytest.fixture
def db(tmp_path):
    """Fresh database per test, in a temp directory."""
    db_path = tmp_path / "data.db"
    init_database(str(db_path))
    return db_path


@pytest.fixture
def git_repo(tmp_path):
    """Fresh git-initialized project directory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo
