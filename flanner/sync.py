"""Manifest exchange and verified ingest (PRD §14).

Two peers reconcile by comparing manifests: each says which artifacts it
holds, and each pulls only what it lacks. Because artifacts are immutable and
content-addressed, that comparison is a set difference with no merge logic
and no notion of "newer" to get wrong.

Everything arriving from a peer is hostile until proven otherwise. An
artifact is verified *before* it is stored, never after, and a rejection is
recorded with its reason but never with its content (§14.4). Nothing here
raises on bad input: a peer must not be able to halt a sync by sending
garbage.

Transport is deliberately absent. A peer is anything answering
:class:`Peer`, so the protocol is exercised in-process by the tests today
and over the mesh later without the verification rules changing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from . import artifacts
from .artifacts import Artifact
from .database import VersionModel, get_artifact, list_artifacts, save_artifact
from .frontmatter import read_managed

PROTOCOL_VERSION = 1

# A single artifact payload is bounded before anything is allocated for it,
# so an oversized or decompression-bomb response is refused rather than
# absorbed (PRD §14.4).
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Manifest:
    """What one peer holds for a workspace.

    Sent only to an already-authorized peer, and never to Flanner Cloud or
    the network provider: the id set alone would disclose the shape of a
    team's planning activity (PRD §14.3).
    """

    workspace_id: str
    artifact_ids: frozenset[str] = frozenset()
    heads: frozenset[str] = frozenset()
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "workspace_id": self.workspace_id,
            "artifact_ids": sorted(self.artifact_ids),
            "heads": sorted(self.heads),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        try:
            return cls(
                workspace_id=str(data["workspace_id"]),
                artifact_ids=frozenset(data.get("artifact_ids") or ()),
                heads=frozenset(data.get("heads") or ()),
                protocol_version=int(data.get("protocol_version", PROTOCOL_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Malformed manifest: {e}") from None


def build_manifest(session: Session, workspace_id: str) -> Manifest:
    """Describe what this device holds for a workspace."""
    held = [a for a in list_artifacts(session) if a.workspace_id == workspace_id]
    graph: dict[str, tuple[str, ...]] = {}
    for artifact in held:
        graph[artifact.artifact_id] = _parents_of(artifact)
    return Manifest(
        workspace_id=workspace_id,
        artifact_ids=frozenset(graph),
        heads=frozenset(artifacts.find_heads(graph)),
    )


def _parents_of(row: Any) -> tuple[str, ...]:
    import json

    try:
        return tuple(json.loads(row.parents))
    except (ValueError, TypeError):
        return ()


def payload_digest(artifact_type: str, payload: bytes) -> str:
    """The hash an envelope's ``content_hash`` must equal for this payload.

    A plan version travels as its whole managed file, because the receiver
    needs the plan name and version number to materialize it, while the
    signed hash covers the body alone (PRD §12.4). Every other artifact
    type is hashed exactly as delivered.
    """
    if artifact_type == artifacts.PLAN_VERSION:
        _, body = read_managed(payload.decode("utf-8", errors="replace"))
        return artifacts.hash_text(body)
    return artifacts.hash_bytes(payload)


def missing_artifact_ids(local: Manifest, remote: Manifest) -> set[str]:
    """What the remote holds that we do not.

    A plain set difference: immutability means an id we already hold can
    never have different content, so there is nothing to compare or merge.
    """
    if local.workspace_id != remote.workspace_id:
        return set()
    return set(remote.artifact_ids) - set(local.artifact_ids)


class Peer(Protocol):
    """The other side of a sync. Any transport that can answer these."""

    def manifest(self, workspace_id: str) -> Manifest: ...

    def fetch(self, artifact_ids: list[str]) -> list[tuple[dict[str, Any], bytes | None]]: ...


# Resolves a device id to its public key. In a team this is backed by the
# control plane's device registry (PRD §11.2); locally it is just this device.
KeyResolver = Any


@dataclass
class SyncReport:
    """What one sync accomplished, and what it refused."""

    accepted: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    already_held: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected


def ingest_artifact(
    session: Session,
    envelope: dict[str, Any],
    payload: bytes | None,
    resolve_key: KeyResolver,
) -> artifacts.Verdict:
    """Verify a foreign artifact, then store it. Never the other way round.

    Returns a Verdict rather than raising, so one hostile artifact cannot
    abort a sync that also carries good ones.
    """
    try:
        artifact = Artifact.from_dict(envelope)
    except ValueError as e:
        return artifacts.Verdict(False, str(e))

    if payload is not None and len(payload) > MAX_PAYLOAD_BYTES:
        return artifacts.Verdict(False, f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")

    public_key = resolve_key(artifact.actor_device_id)
    if not public_key:
        return artifacts.Verdict(False, f"no known key for device {artifact.actor_device_id}")

    verdict = artifacts.verify_artifact(artifact, public_key)
    if not verdict:
        return verdict
    if payload is not None and payload_digest(artifact.artifact_type, payload) != (
        artifact.content_hash
    ):
        return artifacts.Verdict(False, "payload does not match content_hash")

    save_artifact(
        session,
        artifact_id=artifact.artifact_id,
        artifact_type=artifact.artifact_type,
        workspace_id=artifact.workspace_id,
        content_hash=artifact.content_hash,
        actor_device_id=artifact.actor_device_id,
        created_at=artifact.created_at,
        signature=artifact.signature,
        protocol_version=artifact.protocol_version,
        organization_id=artifact.organization_id,
        plan_file_id=artifact.plan_file_id,
        parents=list(artifact.parents),
        actor_user_id=artifact.actor_user_id,
        payload=payload.decode("utf-8", errors="replace") if payload is not None else None,
    )
    return artifacts.Verdict(True)


def sync_from_peer(
    session: Session,
    peer: Peer,
    workspace_id: str,
    resolve_key: KeyResolver,
) -> SyncReport:
    """Pull everything a peer holds for a workspace that this device lacks.

    Idempotent: running it again with no new artifacts on either side does
    nothing, and re-receiving an artifact already held is recorded rather
    than treated as a conflict.
    """
    report = SyncReport()
    try:
        remote = peer.manifest(workspace_id)
    except Exception as e:  # a peer that cannot answer is not a crash
        report.rejected.append(("<manifest>", f"peer manifest unavailable: {e}"))
        return report

    if remote.protocol_version != PROTOCOL_VERSION:
        report.rejected.append(
            ("<manifest>", f"unsupported peer protocol {remote.protocol_version}")
        )
        return report
    if remote.workspace_id != workspace_id:
        report.rejected.append(("<manifest>", "peer answered for a different workspace"))
        return report

    wanted = sorted(missing_artifact_ids(build_manifest(session, workspace_id), remote))
    if not wanted:
        return report

    try:
        delivered = peer.fetch(wanted)
    except Exception as e:
        report.rejected.append(("<fetch>", f"peer fetch failed: {e}"))
        return report

    asked_for = set(wanted)
    for envelope, payload in delivered:
        artifact_id = str(envelope.get("artifact_id", "<unknown>"))
        # A peer must not smuggle in artifacts that were never requested.
        if artifact_id not in asked_for:
            report.rejected.append((artifact_id, "peer sent an artifact that was not requested"))
            continue
        if get_artifact(session, artifact_id) is not None:
            report.already_held.append(artifact_id)
            continue
        # The envelope must belong to the workspace being synchronized.
        if envelope.get("workspace_id") != workspace_id:
            report.rejected.append((artifact_id, "artifact belongs to a different workspace"))
            continue
        verdict = ingest_artifact(session, envelope, payload, resolve_key)
        if verdict:
            report.accepted.append(artifact_id)
        else:
            report.rejected.append((artifact_id, verdict.reason))
    return report


class LocalPeer:
    """A peer backed by another local session.

    Used by the tests to exercise the whole protocol without a network, and
    useful in its own right for reconciling two catalogs on one machine.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def manifest(self, workspace_id: str) -> Manifest:
        return build_manifest(self._session, workspace_id)

    def _payload_for(self, row: Any) -> bytes | None:
        """The bytes to send for an artifact.

        Event payloads are stored on the row. A plan version's content is the
        managed file itself, which stays the canonical copy rather than being
        duplicated into the catalog (PRD §12.4).
        """
        if row.artifact_type == artifacts.PLAN_VERSION:
            version = (
                self._session.query(VersionModel).filter_by(artifact_id=row.artifact_id).first()
            )
            if version is None:
                return None
            try:
                return Path(version.file_path).read_bytes()
            except OSError:
                return None
        return row.payload.encode("utf-8") if row.payload is not None else None

    def fetch(self, artifact_ids: list[str]) -> list[tuple[dict[str, Any], bytes | None]]:
        out: list[tuple[dict[str, Any], bytes | None]] = []
        for artifact_id in artifact_ids:
            row = get_artifact(self._session, artifact_id)
            if row is None:
                continue
            envelope = {
                "protocol_version": row.protocol_version,
                "artifact_id": row.artifact_id,
                "artifact_type": row.artifact_type,
                "organization_id": row.organization_id,
                "workspace_id": row.workspace_id,
                "plan_file_id": row.plan_file_id,
                "parents": list(_parents_of(row)),
                "created_at": row.created_at,
                "actor_user_id": row.actor_user_id,
                "actor_device_id": row.actor_device_id,
                "content_hash": row.content_hash,
                "signature": row.signature,
            }
            out.append((envelope, self._payload_for(row)))
        return out
