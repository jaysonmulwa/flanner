import subprocess

import pytest

from flanner.database import init_database


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
