"""Receiving artifacts a peer sends without being asked.

Pull answers a question. Push accepts data, which is a genuinely wider
surface, so the rules that make it safe are gathered here rather than
scattered through the transport.

Three of them matter:

**The sender is not the author.** A pushed artifact is verified against the
author's key from the organisation keyring, exactly as on pull. A hostile
peer may offer whatever it likes; anything it did not legitimately receive
fails verification and is dropped. Push changes who starts the exchange, not
who is believed.

**The role has to cover what was sent.** Reading a workspace and writing to
it are different permissions. A `READER` who pushes a plan version is
refused per artifact, not per request, so one over-reaching item in a batch
does not discard the rest.

**A live entitlement, not merely a usable one.** Elsewhere an expired
entitlement stays usable through a grace window, because punishing somebody
for a weekend offline is not security. That reasoning does not reach here:
a device serving a push request is online by definition, so it cannot claim
it was unable to check. Requiring `VALID` cuts the window in which a
revoked device can still write from the grace period plus a lifetime — over
a week — down to at most one entitlement lifetime.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import artifacts, sync, workflow
from .database import get_artifact

#: Which role a sender needs for each kind of artifact. Comments and
#: decisions are cheap and reversible; versions and proposals are the ones
#: that can move what a colleague's tools read, so they need `EDITOR`.
#:
#: An artifact type absent from this mapping is refused rather than allowed
#: by default — a new type should have to say who may send it.
REQUIRED_ROLE: dict[str, frozenset[str]] = {
    artifacts.COMMENT: workflow.MAY_COMMENT,
    artifacts.REVIEW_DECISION: workflow.MAY_COMMENT,
    artifacts.REVIEW_EXTERNAL: workflow.MAY_COMMENT,
    artifacts.FRESHNESS_EVIDENCE: workflow.MAY_COMMENT,
    artifacts.PLAN_VERSION: workflow.MAY_PROPOSE,
    artifacts.PLAN_HEAD: workflow.MAY_PROPOSE,
    artifacts.REVIEW_PROPOSAL: workflow.MAY_PROPOSE,
    artifacts.ACCEPTED_HEAD: workflow.MAY_PROPOSE,
    artifacts.ISSUE_LINK: workflow.MAY_PROPOSE,
    artifacts.COLLAB_CHECKPOINT: workflow.MAY_PROPOSE,
    artifacts.ATTACHMENT_MANIFEST: workflow.MAY_PROPOSE,
}


def may_send(artifact_type: str, role: str) -> bool:
    """Whether a peer holding this role may push this kind of artifact."""
    return role in REQUIRED_ROLE.get(artifact_type, frozenset())


@dataclass
class RateLimiter:
    """How often one device may push, tracked in memory.

    In memory on purpose. The limit protects against a colleague's runaway
    script, not against a determined attacker who could restart our daemon
    — and that attacker already needs a valid entitlement and a signing key.
    Persisting this would buy very little and cost a schema.
    """

    window: float = sync.PUSH_WINDOW
    limit: int = sync.MAX_PUSHES_PER_WINDOW
    _seen: dict[str, list[float]] = field(default_factory=dict)

    def allow(self, device_id: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        recent = [t for t in self._seen.get(device_id, ()) if moment - t < self.window]
        if len(recent) >= self.limit:
            # Not recorded. Otherwise a device hammering the limit would keep
            # extending its own lockout, which turns a rate limit into a ban.
            self._seen[device_id] = recent
            return False
        recent.append(moment)
        self._seen[device_id] = recent
        return True


def check_batch(items: list[dict[str, Any]]) -> str | None:
    """Why this push is too big, or None if it is within the caps.

    Checked before anything is decoded or verified, because the point of a
    cap is to refuse work rather than to do it and then complain.
    """
    if not items:
        return "a push has to carry something"
    if len(items) > sync.MAX_PUSH_BATCH:
        return f"at most {sync.MAX_PUSH_BATCH} artifacts per push"
    total = sum(len(str(item.get("payload") or "")) for item in items)
    if total > sync.MAX_PUSH_BYTES:
        return f"at most {sync.MAX_PUSH_BYTES} bytes per push"
    return None


def accept(
    session: Any,
    items: list[dict[str, Any]],
    *,
    workspace_id: str,
    role: str,
    resolve_key: Any,
) -> sync.SyncReport:
    """Take in what a peer pushed, artifact by artifact.

    Every check that `sync_from_peer` applies to a fetched artifact applies
    here too, and one more: the sender's role has to cover the kind of thing
    they sent. Refusals are per artifact so a batch is not all-or-nothing.

    An artifact whose parents this device lacks is accepted and held. The
    graph tolerates gaps — `heads` simply does not advance past the hole
    until the missing link arrives, which the next catch-up pull supplies.
    Fetching the chain backwards on arrival would let a sender make us do
    unbounded work by pushing one deep orphan.
    """
    report = sync.SyncReport()
    for item in items:
        envelope = item.get("envelope")
        if not isinstance(envelope, dict):
            report.rejected.append(("<unknown>", "malformed push item"))
            continue
        artifact_id = str(envelope.get("artifact_id", "<unknown>"))

        if envelope.get("workspace_id") != workspace_id:
            report.rejected.append((artifact_id, "artifact belongs to a different workspace"))
            continue

        artifact_type = str(envelope.get("artifact_type") or "")
        if not may_send(artifact_type, role):
            report.rejected.append((artifact_id, f"a {role} may not push a {artifact_type}"))
            continue

        # Cheap and worth doing before verification, which is not cheap.
        if get_artifact(session, artifact_id) is not None:
            report.already_held.append(artifact_id)
            continue

        blob = item.get("payload")
        payload = blob.encode("utf-8") if isinstance(blob, str) else None
        verdict = sync.ingest_artifact(session, envelope, payload, resolve_key)
        if verdict:
            report.accepted.append(artifact_id)
        else:
            report.rejected.append((artifact_id, verdict.reason))
    return report


__all__ = [
    "REQUIRED_ROLE",
    "RateLimiter",
    "accept",
    "check_batch",
    "may_send",
]
