"""Fetching and renewing an entitlement (PRD §11.4).

Everything in this module can reach the network; nothing else below the
composition roots may import it. ``session`` holds the cache and knows
nothing of HTTP, so a command that only reads authorization cannot
accidentally acquire a network call by depending on the wrong module.

Two behaviours are deliberate.

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
from datetime import datetime
from typing import Any

from . import device_auth, identity
from . import session as cache
from .entitlements import VALID
from .session import DEFAULT_ENDPOINT, Session

REQUEST_TIMEOUT = 15.0


class SessionError(Exception):
    """Enrolment or renewal was refused, or could not be attempted."""


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
    session = _session_from(endpoint, body)
    cache.save(session)
    return session


def refresh(session: Session | None = None) -> Session:
    """Renew the entitlement by proving possession of the device key."""
    current = session or cache.load()
    if current is None:
        raise SessionError("this device is not logged in")
    request = device_auth.sign_request({}, device_id=current.device_id)
    body = _post(current.endpoint, "/v1/entitlements", request.to_dict())
    renewed = _session_from(current.endpoint, body)
    cache.save(renewed)
    return renewed


def ensure_fresh(*, now: datetime | None = None) -> Session | None:
    """The best entitlement available, renewing only when one is needed.

    A valid entitlement is used as-is. Anything else is worth a renewal
    attempt, and a failed attempt falls back to what is cached rather than
    raising: an unreachable control plane should cost team features at the
    end of the grace window, not this operation.
    """
    current = cache.load()
    if current is None or current.status(now=now).status == VALID:
        return current
    try:
        return refresh(current)
    except SessionError:
        return current


def _session_from(endpoint: str, body: dict[str, Any]) -> Session:
    try:
        return Session(
            endpoint=endpoint,
            device_id=str(body["device_id"]),
            organization_id=str(body["organization_id"]),
            user_id=str(body["user_id"]),
            entitlement=str(body["entitlement"]),
            keyring=dict(body.get("keyring") or {}),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SessionError(f"the control plane returned something unusable: {e}") from None


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
