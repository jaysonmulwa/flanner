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
import platform
import urllib.error
import urllib.request
from datetime import datetime, timezone
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
            "platform": platform.system() or os.name,
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
    # A renewal answers about this device, not the others. Dropping the
    # cached peer keys here would silently break artifact verification
    # until the next explicit fetch.
    renewed.device_keys = current.device_keys
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
    message = str(detail) if detail else f"the control plane refused this request ({error.code})"

    # A refusal that says when to come back is far more useful than one that
    # does not, and the server already worked the number out.
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        message += f" (try again in {retry_after}s)"
    return message


# --- the console: what a member can do for themselves ----------------------
# Each of these signs the request with this device's key, so the machine the
# member already trusts is what authorises the next one. No operator, no
# password, no API token to leak.


def accept_invitation(
    token: str, *, user_id: str, endpoint: str = DEFAULT_ENDPOINT, label: str | None = None
) -> Session:
    """Join an organization and enrol this machine in one step.

    The invitation is spent here. There is no separate "now enrol" step,
    because that would need a second secret to carry the new member between
    the two, and emailing one credential is already one more than ideal.
    """
    if not token.strip():
        raise SessionError("an invitation token is required")
    if not user_id.strip():
        raise SessionError("choose a user id for yourself")
    body = _post(
        endpoint,
        "/v1/invitations/accept",
        {
            "token": token.strip(),
            "user_id": user_id.strip(),
            "public_key": identity.device_public_key_b64(),
            "label": label or _default_label(),
            "platform": platform.system() or os.name,
        },
    )
    session = _session_from(endpoint, body)
    cache.save(session)
    return session


def request_enrollment_code() -> tuple[str, str]:
    """Mint a code to type on another machine. Returns the code and expiry."""
    body = _signed("/v1/devices/codes", {})
    return str(body["enrollment_code"]), str(body["expires_at"])


def fetch_device_keys() -> dict[str, str]:
    """Refresh the cached public keys of this organization's devices.

    Kept beside the entitlement rather than fetched per sync: verifying a
    peer's artifacts has to work while the control plane is unreachable,
    which is the whole point of holding signed artifacts in the first place.
    """
    current = cache.load()
    if current is None:
        raise SessionError("this device is not logged in")
    body = _signed("/v1/devices/keyring", {})
    current.device_keys = dict(body.get("devices") or {})
    cache.save(current)
    return current.device_keys


def list_devices() -> list[dict[str, Any]]:
    """Every machine enrolled under this member."""
    return list(_signed("/v1/devices/list", {}).get("devices") or [])


def revoke_device(device_id: str) -> None:
    """Retire a machine. Yours always; anyone's if you are an admin."""
    _signed("/v1/devices/revoke", {"device_id": device_id})


def invite_member(email: str, *, admin: bool = False) -> str:
    """Invite someone to the organization. Returns the invitation token."""
    body = _signed("/v1/members/invite", {"email": email, "role": "admin" if admin else "member"})
    return str(body["token"])


def list_members() -> dict[str, Any]:
    """Who is in the organization, and the seat count that implies."""
    return _signed("/v1/members/list", {})


def remove_member(membership_id: str) -> None:
    _signed("/v1/members/remove", {"membership_id": membership_id})


def _signed(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Call an endpoint that authenticates by device signature."""
    current = cache.load()
    if current is None:
        raise SessionError("this device is not logged in")
    request = device_auth.sign_request(body, device_id=current.device_id)
    return _post(current.endpoint, path, request.to_dict())


def mesh_credential() -> tuple[str, str, datetime] | None:
    """A fresh credential for joining this organization's private network.

    Deliberately not stored. The control plane issues one with every
    entitlement, so asking again is cheap, and a single-use join key sitting
    in a cache is a secret at rest bought for nothing.

    Returns the credential, the management URL, and when it stops working,
    or None when the deployment has no mesh provider. None is an ordinary
    answer: devices still sync over any network they already share.
    """
    current = cache.load()
    if current is None:
        raise SessionError("this device is not logged in")
    request = device_auth.sign_request({}, device_id=current.device_id)
    body = _post(current.endpoint, "/v1/entitlements", request.to_dict())

    mesh = body.get("mesh") or {}
    credential = str(mesh.get("credential") or "")
    if not credential:
        return None

    # Carried through rather than invented. A join key that has already
    # expired should fail saying so, not fail looking like a bad key.
    raw = str(mesh.get("expires_at") or "")
    try:
        expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        expires_at = datetime.now(timezone.utc)
    return credential, str(mesh.get("management_url") or ""), expires_at
