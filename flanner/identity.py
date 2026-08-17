"""Device identity.

Every installation owns an Ed25519 key pair (PRD §11.2). It signs the
artifacts this device produces, so a peer can verify who wrote a plan
version without trusting the transport that carried it, and without asking
Flanner Cloud. The private key never leaves the machine.

The device id is derived from the public key rather than assigned, so an
offline installation has a stable identity from first use and the control
plane can later map it to an account without ever minting it.

Nothing here talks to the network or the database: this module is the
foundation the artifact layer signs with.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

KEY_FILENAME = "device_key"
DEVICE_ID_PREFIX = "dev_"
_DEVICE_ID_HEX_CHARS = 16


def flanner_home() -> Path:
    """Flanner data directory (override with FLANNER_HOME)."""
    return Path(os.environ.get("FLANNER_HOME", Path.home() / ".flanner"))


def device_key_path() -> Path:
    return flanner_home() / KEY_FILENAME


def _write_private_key(path: Path, key: Ed25519PrivateKey) -> None:
    """Write the key owner-readable only, without a world-readable window."""
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the outset; opening then chmod-ing would leave the
    # key briefly readable by others. ponytail: a file with tight permissions,
    # not the OS keychain the PRD ultimately wants. Move to `keyring` when
    # there is a reason to add the dependency; the callers here do not change.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)


def load_or_create_device_key() -> Ed25519PrivateKey:
    """This device's signing key, generating it on first use."""
    path = device_key_path()
    if path.exists():
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise TypeError(f"{path} does not hold an Ed25519 private key")
        return loaded
    key = Ed25519PrivateKey.generate()
    _write_private_key(path, key)
    return key


def public_key_b64(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def load_public_key(encoded: str) -> Ed25519PublicKey:
    """Rebuild a public key from its base64 form; raises ValueError if malformed."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as e:
        raise ValueError(f"Malformed public key: {e}") from None
    if len(raw) != 32:
        raise ValueError(f"Ed25519 public keys are 32 bytes, got {len(raw)}")
    return Ed25519PublicKey.from_public_bytes(raw)


def device_id_for(public_key: Ed25519PublicKey) -> str:
    """Stable id derived from a public key, so identity needs no server."""
    digest = hashes.Hash(hashes.SHA256())
    digest.update(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    return DEVICE_ID_PREFIX + digest.finalize().hex()[:_DEVICE_ID_HEX_CHARS]


def device_id() -> str:
    """This device's id."""
    return device_id_for(load_or_create_device_key().public_key())


def device_public_key_b64() -> str:
    """This device's public key, in the form the control plane records."""
    return public_key_b64(load_or_create_device_key().public_key())


def sign(payload: bytes, key: Ed25519PrivateKey | None = None) -> str:
    """Sign bytes with this device's key; returns a base64 signature."""
    signer = key if key is not None else load_or_create_device_key()
    return base64.b64encode(signer.sign(payload)).decode("ascii")


def verify(public_key_encoded: str, payload: bytes, signature_b64: str) -> bool:
    """True when the signature is this key's over exactly these bytes.

    Returns False for every failure, including malformed input, so callers
    can treat verification as a single predicate and never have to guess
    which exception a hostile peer might provoke.
    """
    try:
        key = load_public_key(public_key_encoded)
        key.verify(base64.b64decode(signature_b64, validate=True), payload)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
