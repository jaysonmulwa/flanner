"""Getting an entitlement onto this device, and keeping it (PRD §11.4).

``entitlements`` can verify a token but has no way to obtain one. This is
the other half: enrolling once with a code, then renewing by signing with
the device key, and caching the result so that work already authorized
survives a control-plane outage.

Three things are deliberate.

**Nothing here is a secret worth stealing.** The cached entitlement is a
signed statement about this device, bound to a device id no other machine
can sign for. Copying it to another laptop gains nothing, because that
laptop cannot produce the signature the next refresh needs.

**Refresh failure is not an error.** A cached entitlement stays usable
through its grace window, so being unable to reach the control plane
degrades team features later rather than interrupting work now. Local plan
work never depends on any of this (§18.4).

**The transport is stdlib.** Two JSON POSTs do not justify a dependency in
an MIT client that most users install only for local use.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import device_auth, identity
from .entitlements import VALID, EntitlementStore, Verdict

SESSION_FILENAME = "session.json"
DEFAULT_ENDPOINT = "https://api.flanner.io"
REQUEST_TIMEOUT = 15.0


class SessionError(Exception):
    """Enrolment or renewal was refused, or could not be attempted."""


@dataclass
class Session:
    """What this device knows about its own account."""

    endpoint: str
    device_id: str
    organization_id: str
    user_id: str
    entitlement: str
    keyring: dict[str, str] = field(default_factory=dict)

    def store(self) -> EntitlementStore:
        return EntitlementStore(token=self.entitlement, keyring=self.keyring)

    def status(self, *, now: datetime | None = None) -> Verdict:
        return self.store().current(now=now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "device_id": self.device_id,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "entitlement": self.entitlement,
            "keyring": self.keyring,
        }

    @classmethod
    def from_response(cls, endpoint: str, body: dict[str, Any]) -> Session:
        try:
            return cls(
                endpoint=endpoint,
                device_id=str(body["device_id"]),
                organization_id=str(body["organization_id"]),
                user_id=str(body["user_id"]),
                entitlement=str(body["entitlement"]),
                keyring=dict(body.get("keyring") or {}),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise SessionError(f"the control plane returned something unusable: {e}") from None


def session_path() -> Path:
    return identity.flanner_home() / SESSION_FILENAME


def load() -> Session | None:
    """The cached session, or None if this device has never logged in."""
    path = session_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session(
            endpoint=str(data.get("endpoint") or DEFAULT_ENDPOINT),
            device_id=str(data["device_id"]),
            organization_id=str(data["organization_id"]),
            user_id=str(data["user_id"]),
            entitlement=str(data["entitlement"]),
            keyring=dict(data.get("keyring") or {}),
        )
    except (OSError, ValueError, KeyError, TypeError):
        # A corrupt cache is indistinguishable from never having logged in,
        # and both are fixed the same way. Refusing to start would be worse.
        return None


def save(session: Session) -> Path:
    """Cache a session owner-readable only, atomically."""
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(session.to_dict(), f, indent=2, sort_keys=True)
    os.replace(temp, path)
    return path


def clear() -> bool:
    """Forget this device's session. The device key is left alone.

    Logging out is not un-enrolling: the key is this device's identity, and
    discarding it would make the same machine look like a new one. Removing
    the device's access is the control plane's job, not the client's.
    """
    path = session_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def login(code: str, *, endpoint: str = DEFAULT_ENDPOINT, label: str | None = None) -> Session:
    """Redeem an enrolment code and cache the entitlement it returns."""
    if not code.strip():
        raise SessionError("an enrolment code is required")
    body = _post(
        endpoint,
        "/v1/devices/enroll",
        {
            "enrollment_code": code.strip(),
            "public_key": identity.device_public_key_b64(),
            "label": label or _default_label(),
            "platform": os.name,
        },
    )
    session = Session.from_response(endpoint, body)
    save(session)
    return session


def refresh(session: Session | None = None) -> Session:
    """Renew the entitlement by proving possession of the device key."""
    current = session or load()
    if current is None:
        raise SessionError("this device is not logged in")
    request = device_auth.sign_request({}, device_id=current.device_id)
    body = _post(current.endpoint, "/v1/entitlements", request.to_dict())
    renewed = Session.from_response(current.endpoint, body)
    save(renewed)
    return renewed


def ensure_fresh(*, now: datetime | None = None) -> Session | None:
    """The best entitlement available, renewing only when one is needed.

    A valid entitlement is used as-is. Anything else is worth a renewal
    attempt, and a failed attempt falls back to what is cached rather than
    raising: an unreachable control plane should cost team features at the
    end of the grace window, not this operation.
    """
    current = load()
    if current is None or current.status(now=now).status == VALID:
        return current
    try:
        return refresh(current)
    except SessionError:
        return current


def _default_label() -> str:
    import socket

    try:
        return socket.gethostname()
    except OSError:
        return "unnamed device"


def _post(endpoint: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = endpoint.rstrip("/") + path
    if not url.startswith(("http://", "https://")):
        raise SessionError(f"{endpoint} is not an http endpoint")
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        raise SessionError(_detail(e)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SessionError(f"could not reach {endpoint}: {e}") from None
    except ValueError as e:
        raise SessionError(f"the control plane returned something unusable: {e}") from None


def _detail(error: urllib.error.HTTPError) -> str:
    """The server's own explanation, when it gave one worth repeating."""
    try:
        detail = json.loads(error.read().decode("utf-8")).get("detail")
    except (ValueError, OSError):
        detail = None
    return str(detail) if detail else f"the control plane refused this request ({error.code})"
