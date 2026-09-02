"""Proving a device is itself, without a bearer token (PRD §21.1).

A device already holds an Ed25519 key, so it can authenticate by signing
each request rather than presenting a secret. That removes the usual
problem with API tokens: there is nothing to steal from disk, a captured
request cannot be reused, and the control plane stores only public keys, so
a breach there cannot impersonate anyone.

Both sides need the same rules, so they live in the public package: the
client builds requests here, and the control plane verifies them with this
same code. A format only one side could compute would be a format only one
side could get right.

Freshness is enforced by a bounded timestamp plus a nonce. Both callers now
spend the nonce — the control plane in `flanner_cloud.replay`, a peer in
`flanner.replay` — so the timestamp is not the thing stopping a replay. It
bounds how long a nonce must be remembered, and gives clocks room to be
wrong.

That separation is what sets the size. While the window was the only
defence, every second of tolerance for a drifting clock was a second of
tolerance for a replay, and two minutes was already generous. It can now be
sized for the problem people actually have: machines whose clocks are minutes
apart, which on Windows is the default state, since the time service ships
stopped.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import identity
from .artifacts import canonical_bytes

# How far apart the two clocks may be. Sized for drift, not for replay:
# uniqueness of the nonce is what refuses a second use, so this only has to
# be short enough to keep the nonce table small.
MAX_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class SignedRequest:
    """A request body, and proof the holder of a device key produced it."""

    device_id: str
    issued_at: str
    nonce: str
    body: dict[str, Any]
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "issued_at": self.issued_at,
            "nonce": self.nonce,
            "body": self.body,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignedRequest:
        try:
            body = data.get("body") or {}
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            return cls(
                device_id=str(data["device_id"]),
                issued_at=str(data["issued_at"]),
                nonce=str(data["nonce"]),
                body=body,
                signature=str(data["signature"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Malformed signed request: {e}") from None


def _payload(device_id: str, issued_at: str, nonce: str, body: dict[str, Any]) -> bytes:
    """Exactly what gets signed: identity, freshness, and the whole body.

    The body is covered too, so an intercepted request cannot be replayed
    with different arguments.
    """
    return canonical_bytes(
        {"device_id": device_id, "issued_at": issued_at, "nonce": nonce, "body": body}
    )


def sign_request(
    body: dict[str, Any],
    *,
    device_id: str | None = None,
    signing_key: Any = None,
    now: datetime | None = None,
) -> SignedRequest:
    """Build a request signed by this device's key."""
    key = signing_key if signing_key is not None else identity.load_or_create_device_key()
    who = device_id or identity.device_id_for(key.public_key())
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = moment.isoformat().replace("+00:00", "Z")
    nonce = secrets.token_hex(16)
    return SignedRequest(
        device_id=who,
        issued_at=issued_at,
        nonce=nonce,
        body=body,
        signature=identity.sign(_payload(who, issued_at, nonce, body), key),
    )


@dataclass(frozen=True)
class AuthResult:
    """Whether a request may be acted on, and why not when it may not."""

    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def verify_request(
    request: SignedRequest,
    public_key: str,
    *,
    now: datetime | None = None,
    max_skew: timedelta = MAX_SKEW,
) -> AuthResult:
    """Check a request really came from the holder of that device key.

    Never raises. This runs on an unauthenticated endpoint, so hostile input
    is expected and must produce a refusal rather than a stack trace.
    """
    try:
        issued = datetime.fromisoformat(request.issued_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return AuthResult(False, "issued_at is not a timestamp")
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=timezone.utc)

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    drift = abs((moment - issued).total_seconds())
    if drift > max_skew.total_seconds():
        # Naming the cause, not just the measurement. This refusal is almost
        # never an attack and almost always two machines disagreeing about
        # the time, but "request is 246s out of date" reads like a bug in
        # flanner to the person holding both machines.
        allowed = int(max_skew.total_seconds())
        return AuthResult(
            False,
            f"request is {int(drift)}s out of date: the two machines' clocks "
            f"disagree by more than {allowed}s. Sync the clock on both and retry.",
        )

    if not request.nonce:
        return AuthResult(False, "request has no nonce")

    payload = _payload(request.device_id, request.issued_at, request.nonce, request.body)
    if not identity.verify(public_key, payload, request.signature):
        return AuthResult(False, "signature does not match this device key")
    return AuthResult(True)
