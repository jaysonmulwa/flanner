"""Tests for device identity and signing (PRD §11.2)."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flanner import identity


def _keyring_installed() -> bool:
    try:
        import keyring  # noqa: F401
    except ImportError:
        return False
    return True


#: The library, not a working backend: conftest supplies an in-memory one.
#: Testing for a usable backend instead would skip these on headless CI,
#: where the fake makes them perfectly runnable.
needs_keychain = pytest.mark.skipif(
    not _keyring_installed(),
    reason="keyring is not installed here; identity falls back to its key file",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_key_is_created_once_and_reused(home):
    first = identity.load_or_create_device_key()
    second = identity.load_or_create_device_key()
    assert identity.public_key_b64(first.public_key()) == identity.public_key_b64(
        second.public_key()
    )


def test_device_id_is_stable_and_derived_from_the_public_key(home):
    first = identity.device_id()
    assert first.startswith(identity.DEVICE_ID_PREFIX)
    assert identity.device_id() == first
    # Derivation is pure: the same key always yields the same id.
    key = identity.load_or_create_device_key()
    assert identity.device_id_for(key.public_key()) == first


def test_different_devices_get_different_ids(tmp_path, monkeypatch):
    ids = set()
    for name in ("a", "b"):
        monkeypatch.setenv("FLANNER_HOME", str(tmp_path / name))
        ids.add(identity.device_id())
    assert len(ids) == 2


def test_sign_and_verify_roundtrip(home):
    payload = b"an artifact envelope"
    signature = identity.sign(payload)
    assert identity.verify(identity.device_public_key_b64(), payload, signature) is True


def test_tampered_payload_fails_verification(home):
    signature = identity.sign(b"original")
    assert identity.verify(identity.device_public_key_b64(), b"altered", signature) is False


def test_another_devices_key_fails_verification(home, tmp_path, monkeypatch):
    payload = b"mine"
    signature = identity.sign(payload)
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "other"))
    other_public = identity.device_public_key_b64()
    assert identity.verify(other_public, payload, signature) is False


@pytest.mark.parametrize(
    "public_key, signature",
    [
        ("not base64!", "aaaa"),
        (base64.b64encode(b"too short").decode(), "aaaa"),
        ("", ""),
    ],
)
def test_malformed_input_is_false_not_an_exception(home, public_key, signature):
    # A hostile peer must not be able to raise out of a verification check.
    assert identity.verify(public_key, b"payload", signature) is False


def test_signing_with_an_explicit_key(home):
    key = Ed25519PrivateKey.generate()
    signature = identity.sign(b"payload", key)
    assert identity.verify(identity.public_key_b64(key.public_key()), b"payload", signature)
    # ...and this device's own key must not validate it.
    assert not identity.verify(identity.device_public_key_b64(), b"payload", signature)


def test_public_key_roundtrips_through_its_encoding(home):
    encoded = identity.device_public_key_b64()
    restored = identity.load_public_key(encoded)
    assert identity.public_key_b64(restored) == encoded


def test_corrupt_key_file_is_reported(home):
    identity.device_key_path().parent.mkdir(parents=True, exist_ok=True)
    identity.device_key_path().write_text("not a key", encoding="utf-8")
    with pytest.raises(ValueError):
        identity.load_or_create_device_key()


@needs_keychain
def test_a_new_key_goes_to_the_keychain_and_not_to_disk(home):
    """The whole point of the move: no key material at rest in the home."""
    identity.load_or_create_device_key()

    assert not identity.device_key_path().exists()


@needs_keychain
def test_an_existing_key_file_is_adopted_and_then_removed(home):
    """An install predating the keychain must not wake up as a new device.

    The id is derived from the key, so generating a fresh one would make
    this machine unrecognisable to every peer and to the control plane.
    """
    identity.device_key_path().parent.mkdir(parents=True, exist_ok=True)
    original = Ed25519PrivateKey.generate()
    identity._write_private_key(identity.device_key_path(), original)
    before = identity.device_id()

    adopted = identity.load_or_create_device_key()

    assert identity.public_key_b64(adopted.public_key()) == identity.public_key_b64(
        original.public_key()
    )
    assert identity.device_id() == before
    assert not identity.device_key_path().exists()


def test_a_keychain_that_cannot_hold_the_key_keeps_the_file(home, monkeypatch):
    """A backend that accepts a write and loses it would cost this device
    its identity, so the file is only removed once a read-back proves the
    keychain really has it."""
    monkeypatch.setattr(identity, "_store_keychain", lambda store, key: False)

    identity.load_or_create_device_key()

    assert identity.device_key_path().exists()


def test_no_keychain_falls_back_to_the_file(home, monkeypatch):
    """Headless Linux, containers and CI. Not a degraded mode."""
    monkeypatch.setenv("FLANNER_NO_KEYCHAIN", "1")

    first = identity.load_or_create_device_key()

    assert identity.device_key_path().exists()
    assert identity.public_key_b64(first.public_key()) == identity.public_key_b64(
        identity.load_or_create_device_key().public_key()
    )
