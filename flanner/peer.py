"""Syncing with another device over the network (PRD §14).

``sync`` defines the protocol and left the transport out on purpose. This
fills it in: an HTTP server that answers for this device's catalog, and a
:class:`Peer` implementation that talks to another device's.

Both halves live here because they are two ends of one wire format. Putting
them in separate modules is how the two drift.

**What authorises a peer.** Two proofs, and neither involves asking the
control plane at the time of the request:

1. *You hold this device's key.* A device id is the hash of its public key,
   so a caller that sends a public key which hashes to the id it claims has
   proved the two belong together, and the signature over the request
   proves it holds the private half. No key registry is needed for this.
2. *That device may read this workspace.* The caller sends its entitlement,
   which the control plane signed and which names both the device id and
   the workspace role. The ids in the two proofs must match, so an
   entitlement is useless to anyone who did not receive it.

Together those mean a peer can authorise a stranger while completely
offline, which is the property that makes a mesh worth having.

**What is still checked afterwards.** Authorisation is not verification.
Every artifact received is checked against its *author's* key, which is
usually a third device, using the organization keyring cached at login.
A peer you trust to talk to is not a peer you trust to be honest about
what someone else wrote (§14.4).

**What this is not.** There is no private addressing here. This speaks to
whatever address it is given, so it works over a LAN, a tunnel, or a mesh
provider's network without changing. Provisioning that network is the mesh
provider's job (§10), and deliberately not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import entitlements, identity, sync
from .device_auth import SignedRequest, sign_request, verify_request
from .sync import Manifest

DEFAULT_PORT = 8776
REQUEST_TIMEOUT = 30.0


class PeerError(Exception):
    """A peer could not be reached, or refused."""


@dataclass(frozen=True)
class PeerIdentity:
    """A caller that has proved who it is and what it may read."""

    device_id: str
    user_id: str
    organization_id: str
    role: str


def authorize(
    payload: dict[str, Any],
    workspace_id: str,
    issuer_keyring: dict[str, str],
    *,
    now: Any = None,
) -> PeerIdentity:
    """Decide whether a caller may read this workspace. Raises if not.

    Never trusts a claimed device id. The public key travels with the
    request and must hash to the id it claims, which is what lets this work
    without a registry lookup.
    """
    try:
        request = SignedRequest.from_dict(payload)
    except ValueError as e:
        raise PeerError(f"malformed request: {e}") from None

    public_key = str(request.body.get("public_key") or "")
    if not public_key:
        raise PeerError("request carries no public key")
    try:
        derived = identity.device_id_for(identity.load_public_key(public_key))
    except ValueError as e:
        raise PeerError(f"unusable public key: {e}") from None
    if derived != request.device_id:
        raise PeerError("public key does not match the device id claimed")

    result = verify_request(request, public_key, now=now)
    if not result:
        raise PeerError(result.reason)

    verdict = entitlements.verify(
        str(request.body.get("entitlement") or ""), issuer_keyring, now=now
    )
    if not verdict.usable or verdict.claims is None:
        raise PeerError(f"entitlement is {verdict.status}")
    if verdict.claims.device_id != request.device_id:
        raise PeerError("that entitlement was issued to a different device")

    role = verdict.claims.role_in(workspace_id)
    if role is None:
        raise PeerError("no access to this workspace")

    return PeerIdentity(
        device_id=request.device_id,
        user_id=verdict.claims.user_id,
        organization_id=verdict.claims.organization_id,
        role=role,
    )


def create_peer_app(sessions: Any, held: Any) -> Any:
    """An HTTP app serving this device's catalog to authorised peers.

    ``held`` is a callable returning the current cached session, read per
    request rather than captured, so a renewed entitlement or a refreshed
    keyring takes effect without a restart.
    """
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Flanner peer", version=str(sync.PROTOCOL_VERSION))

    def _issuer_keyring() -> dict[str, str]:
        current = held()
        if current is None:
            # Not logged in: this device has no way to check anyone's
            # entitlement, so it serves nobody rather than serving everyone.
            raise HTTPException(503, "this device is not part of a team")
        return dict(current.keyring)

    def _caller(payload: dict[str, Any], workspace_id: str) -> PeerIdentity:
        try:
            return authorize(payload, workspace_id, _issuer_keyring())
        except PeerError as e:
            raise HTTPException(403, str(e)) from None

    @app.post("/peer/manifest")
    def manifest(payload: dict[str, Any]) -> dict[str, Any]:
        """What this device holds for a workspace the caller may read."""
        workspace_id = str(payload.get("body", {}).get("workspace_id") or "")
        if not workspace_id:
            raise HTTPException(400, "workspace_id is required")
        _caller(payload, workspace_id)
        with sessions() as session:
            return sync.build_manifest(session, workspace_id).to_dict()

    @app.post("/peer/fetch")
    def fetch(payload: dict[str, Any]) -> dict[str, Any]:
        """Hand over specific artifacts, and only from the named workspace."""
        body = payload.get("body") or {}
        workspace_id = str(body.get("workspace_id") or "")
        wanted = [str(x) for x in (body.get("artifact_ids") or [])]
        if not workspace_id:
            raise HTTPException(400, "workspace_id is required")
        if len(wanted) > sync.MAX_FETCH_BATCH:
            raise HTTPException(413, f"at most {sync.MAX_FETCH_BATCH} artifacts per request")
        _caller(payload, workspace_id)

        with sessions() as session:
            local = sync.LocalPeer(session)
            out = []
            for envelope, blob in local.fetch(wanted):
                # The caller proved access to one workspace, so anything
                # belonging to another is withheld even if it was asked for
                # by id. Ids are guessable in principle; access is not.
                if envelope.get("workspace_id") != workspace_id:
                    continue
                out.append(
                    {
                        "envelope": envelope,
                        "payload": blob.decode("utf-8", errors="replace")
                        if blob is not None
                        else None,
                    }
                )
            return {"artifacts": out}

    return app


class RemotePeer:
    """Another device, reached over HTTP. Implements :class:`sync.Peer`."""

    def __init__(
        self,
        address: str,
        workspace_id: str,
        held: Any,
        *,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._address = address.rstrip("/")
        # Held on the instance because sync.Peer.fetch takes only ids: the
        # workspace is a property of the connection, not of each request.
        self._workspace = workspace_id
        self._held = held
        self._timeout = timeout

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        import json
        import urllib.error
        import urllib.request

        current = self._held()
        if current is None:
            raise PeerError("this device is not logged in")

        # One read of the key, and the id derived from it, so the public key
        # and the device id cannot disagree. Taking the id from the cached
        # session instead would produce a request the far side rejects as a
        # key mismatch, which blames the wrong thing entirely.
        key = identity.load_or_create_device_key()
        device_id = identity.device_id_for(key.public_key())
        if device_id != current.device_id:
            raise PeerError(
                "the cached session belongs to a different device; run 'flanner login' again"
            )

        # The public key travels with every request so the far side can
        # check it against the device id without a registry.
        signed = sign_request(
            {
                **body,
                "public_key": identity.public_key_b64(key.public_key()),
                "entitlement": current.entitlement,
            },
            device_id=device_id,
            signing_key=key,
        )
        url = self._address + path
        if not url.startswith(("http://", "https://")):
            raise PeerError(f"{self._address} is not an http address")
        request = urllib.request.Request(  # noqa: S310 - scheme checked above
            url,
            data=json.dumps(signed.to_dict()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return dict(json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            raise PeerError(_detail(e)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PeerError(f"could not reach {self._address}: {e}") from None
        except ValueError as e:
            raise PeerError(f"peer sent something unusable: {e}") from None

    def manifest(self, workspace_id: str) -> Manifest:
        return Manifest.from_dict(self._post("/peer/manifest", {"workspace_id": workspace_id}))

    def fetch(self, artifact_ids: list[str]) -> list[tuple[dict[str, Any], bytes | None]]:
        """Fetch in batches, so one large sync is not one huge request."""
        out: list[tuple[dict[str, Any], bytes | None]] = []
        for start in range(0, len(artifact_ids), sync.MAX_FETCH_BATCH):
            batch = artifact_ids[start : start + sync.MAX_FETCH_BATCH]
            body = self._post(
                "/peer/fetch", {"workspace_id": self._workspace, "artifact_ids": batch}
            )
            for item in body.get("artifacts") or []:
                envelope = item.get("envelope")
                if not isinstance(envelope, dict):
                    continue
                blob = item.get("payload")
                out.append((envelope, blob.encode("utf-8") if blob is not None else None))
        return out


def pull(session: Any, address: str, workspace_id: str, held: Any) -> sync.SyncReport:
    """Pull everything a peer holds for a workspace that this device lacks.

    Artifacts are verified against their author's key from the cached
    organization keyring, not against the peer that handed them over.
    """
    current = held()
    if current is None:
        report = sync.SyncReport()
        report.rejected.append(("<peer>", "this device is not logged in"))
        return report

    peer = RemotePeer(address, workspace_id, held)
    return sync.sync_from_peer(session, peer, workspace_id, current.resolve_device_key)


def _detail(error: Any) -> str:
    import json

    try:
        detail = json.loads(error.read().decode("utf-8")).get("detail")
    except (ValueError, OSError):
        detail = None
    return str(detail) if detail else f"peer refused the request ({error.code})"


__all__ = [
    "DEFAULT_PORT",
    "PeerError",
    "PeerIdentity",
    "RemotePeer",
    "authorize",
    "create_peer_app",
    "pull",
]
