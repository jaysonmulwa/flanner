"""What happens when the keychain cannot be read.

The device id is derived from the signing key, so a new key is a new
machine: peers reject its signatures, its entitlement names somebody else,
and every plan version it authored is stranded under an id nothing can
produce again.

Once the key moves to the keychain the file is deleted. From that moment a
locked keychain, a removed entry, or a different backend than the one
written to all look exactly like a machine that has never run flanner — and
the answer to that was to generate a new key. Silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flanner import identity
from flanner.exceptions import IdentityUnavailableError


class FakeKeychain:
    """A keychain that can be locked, emptied, or swapped out underneath."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}
        self.locked = False

    def get_password(self, service: str, account: str) -> str | None:
        if self.locked:
            raise RuntimeError("the keychain is locked")
        return self.entries.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        if self.locked:
            raise RuntimeError("the keychain is locked")
        self.entries[(service, account)] = value


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("FLANNER_NO_KEYCHAIN", raising=False)
    return tmp_path / "home"


@pytest.fixture
def keychain(monkeypatch) -> FakeKeychain:
    store = FakeKeychain()
    monkeypatch.setattr(identity, "_keychain", lambda: store)
    return store


def test_a_locked_keychain_does_not_mint_a_second_identity(home, keychain) -> None:
    """The defect, in the order it actually happens.

    Enrol on Monday; the key goes to the keychain and the file is deleted.
    Reboot on Tuesday and open a terminal before unlocking the login
    keyring. Every flanner command from then on speaks as a device nobody
    has ever heard of.
    """
    first = identity.device_id_for(identity.load_or_create_device_key().public_key())
    assert not identity.device_key_path().exists(), "the key never moved to the keychain"

    keychain.locked = True

    with pytest.raises(IdentityUnavailableError) as refused:
        identity.load_or_create_device_key()

    assert first in str(refused.value), "the refusal does not say which device this is"
    assert "keychain" in str(refused.value).lower()

    keychain.locked = False
    again = identity.device_id_for(identity.load_or_create_device_key().public_key())
    assert again == first, "unlocking did not give the machine its own identity back"


def test_an_entry_that_vanished_does_not_mint_either(home, keychain) -> None:
    """No exception here: the keychain answers, and says it has nothing.

    A backend that was swapped, an entry somebody cleared, a profile
    restored without it. Indistinguishable from a first run by the keychain
    alone, which is why the record of having one is kept outside it.
    """
    identity.load_or_create_device_key()
    keychain.entries.clear()

    with pytest.raises(IdentityUnavailableError):
        identity.load_or_create_device_key()


def test_a_machine_that_has_never_run_flanner_still_gets_a_key(home, keychain) -> None:
    """The refusal must not cost a first run its identity."""
    key = identity.load_or_create_device_key()

    assert identity.device_id_for(key.public_key()).startswith("dev_")
    assert identity.keychain_marker_path().exists()


def test_no_keychain_at_all_keeps_working_from_the_file(home, monkeypatch) -> None:
    """The supported configuration, and it must stay silent.

    A file at 0600 is a choice, not a consolation prize, and nothing here
    should refuse on a machine that never had a keychain to begin with.
    """
    monkeypatch.setattr(identity, "_keychain", lambda: None)

    first = identity.device_id_for(identity.load_or_create_device_key().public_key())
    again = identity.device_id_for(identity.load_or_create_device_key().public_key())

    assert first == again
    assert identity.device_key_path().exists()
    assert not identity.keychain_marker_path().exists(), "marked as in a keychain it never reached"


def test_migrating_an_existing_file_records_that_it_moved(home, keychain) -> None:
    """The install that predates the keychain, which must keep its id."""
    monkeypatch_free_key = identity.Ed25519PrivateKey.generate()
    identity._write_private_key(identity.device_key_path(), monkeypatch_free_key)
    expected = identity.device_id_for(monkeypatch_free_key.public_key())

    moved = identity.load_or_create_device_key()

    assert identity.device_id_for(moved.public_key()) == expected, "the machine changed identity"
    assert not identity.device_key_path().exists(), "the file was left behind after the move"
    assert identity.keychain_marker_path().read_text(encoding="utf-8").strip() == expected


def test_a_keychain_that_refuses_the_write_keeps_the_file(home, keychain) -> None:
    """A write that does not stick must not delete the only other copy."""
    keychain.locked = True
    monkeypatch_free_key = identity.Ed25519PrivateKey.generate()
    identity._write_private_key(identity.device_key_path(), monkeypatch_free_key)
    expected = identity.device_id_for(monkeypatch_free_key.public_key())

    loaded = identity.load_or_create_device_key()

    assert identity.device_id_for(loaded.public_key()) == expected
    assert identity.device_key_path().exists(), "the file went while the keychain held nothing"
    assert not identity.keychain_marker_path().exists()


def test_an_install_that_migrated_before_this_existed_is_protected(home, keychain) -> None:
    """The gap the marker would otherwise leave open.

    Anyone whose key moved to the keychain under an earlier version has no
    marker and no file, so the protection would not apply to exactly the
    people who have already been exposed to the defect. The first read that
    succeeds writes it.
    """
    first = identity.device_id_for(identity.load_or_create_device_key().public_key())
    identity.keychain_marker_path().unlink()  # as an older version left it

    identity.load_or_create_device_key()
    assert identity.keychain_marker_path().exists(), "the older install was never caught up"

    keychain.locked = True
    with pytest.raises(IdentityUnavailableError) as refused:
        identity.load_or_create_device_key()
    assert first in str(refused.value)
