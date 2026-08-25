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


@pytest.fixture(autouse=True)
def _isolated_keychain(monkeypatch):
    """An in-memory keychain for every test.

    The real backend here is the OS credential store: on this machine
    WinVaultKeyring, on a Mac the login keychain. Letting the suite write
    there would leave a developer's own store full of test keys, and would
    make tests share an identity through a channel FLANNER_HOME does not
    isolate.

    A fake rather than disabling the keychain outright, so the keychain path
    is the one actually exercised. Disabling it would leave the code that
    matters covered only by the tests that opt back in.
    """
    try:
        import keyring
        from keyring.backend import KeyringBackend
    except ImportError:
        # No keychain library, so nothing to protect: `identity` will take
        # its file fallback. Erroring every test over a missing isolation
        # fixture would be worse than the pollution it guards against.
        yield
        return

    class Memory(KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def __init__(self) -> None:
            self._held: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self._held.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self._held[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self._held.pop((service, username), None)

    previous = keyring.get_keyring()
    keyring.set_keyring(Memory())
    yield
    keyring.set_keyring(previous)
