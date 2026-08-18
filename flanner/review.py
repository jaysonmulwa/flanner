"""Recording review: propose, decide, and advance the baseline (PRD §12.5).

The projection side of review already existed and was read-only. This is the
write side: it creates the signed events, stores them as artifacts, and
emits the accepted-head transition when a decision satisfies the policy.

Approving is deliberately not the same as accepting. A decision records
what a reviewer thought; only an accepted-head transition moves the team's
baseline, and only when the approvals it cites meet the workspace policy.
Doing both in one call is a convenience, not a shortcut: the transition is
still a separate signed event that any peer can validate on its own.

**On authorization.** Roles here are advisory, not enforced. Real roles
arrive as signed workspace capabilities from the control plane (§11.3),
which does not exist yet, so a local roles map is a placeholder: anyone who
can edit it can promote themselves. It is enough to exercise the machinery
and shape the UX, and it is not a security boundary. Nothing downstream
assumes otherwise, because every event is signed and re-validated by the
peer that receives it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from . import authz, workflow
from .assurance import load_review_events
from .database import (
    PlanFileModel,
    ProjectModel,
    get_version,
    save_artifact,
)
from .plan_ops import workspace_id_for
from .workflow import (
    APPROVE,
    DEFAULT_POLICY,
    MAINTAINER,
    WITHDRAW,
    Event,
    Policy,
    WorkflowState,
    local_roles,
)


@dataclass(frozen=True)
class ReviewResult:
    """What a review action recorded, and whether it moved the baseline."""

    event: Event
    accepted: Event | None = None
    reason: str = ""

    @property
    def advanced_baseline(self) -> bool:
        return self.accepted is not None


def save_event(session: Session, event: Event, plan_file_id: str) -> None:
    """Store a signed event as an artifact, payload alongside."""
    artifact = event.artifact
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
        plan_file_id=plan_file_id,
        parents=list(artifact.parents),
        actor_user_id=artifact.actor_user_id,
        payload=json.dumps(event.payload),
    )


def status(
    session: Session,
    *,
    plan_file: PlanFileModel,
    project: ProjectModel | None = None,
    roles: dict[str, str] | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> WorkflowState:
    """Project the current review state for a plan.

    Without a project there is no workspace to resolve an entitlement
    against, so the local placeholder stands in. Callers that hold one
    should pass it, or a joined workspace will read as advisory here while
    the assurance verdict enforces it.
    """
    if roles is None:
        roles = authz.resolve(project).roles if project is not None else local_roles()
    return workflow.project(load_review_events(session, str(plan_file.id)), roles, policy)


def propose(
    session: Session,
    *,
    project: ProjectModel,
    plan_file: PlanFileModel,
    artifact_id: str | None = None,
    message: str = "",
    actor: str | None = None,
    roles: dict[str, str] | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> ReviewResult:
    """Offer a version for review.

    Defaults to the plan's newest version, and records the baseline it was
    written against so the proposal can later be recognised as stale if the
    baseline moves first (§12.5.4).
    """
    target = artifact_id
    if target is None:
        version = get_version(session, plan_file.id, None)
        if version is None or not version.artifact_id:
            raise ValueError("this plan has no signed version to propose")
        target = version.artifact_id

    authorization = authz.resolve(project, actor=actor)
    effective_roles = roles if roles is not None else authorization.roles
    # Refuse before writing. Projection would drop an unauthorized proposal
    # anyway - it has to, because the same rule governs events arriving from
    # peers - but a local caller deserves to be told, rather than watch the
    # command succeed and the proposal never appear.
    _require(effective_roles, authorization, workflow.MAY_PROPOSE, "propose on this plan")

    state = status(session, plan_file=plan_file, roles=effective_roles, policy=policy)
    event = workflow.make_proposal(
        workspace_id=workspace_id_for(project),
        plan_file_id=str(plan_file.id),
        target_artifact_id=target,
        base_accepted_event_ids=state.accepted_event_ids,
        message=message,
        policy=policy,
        actor_user_id=authorization.actor,
    )
    save_event(session, event, str(plan_file.id))
    session.commit()
    return ReviewResult(event=event)


def decide(
    session: Session,
    *,
    project: ProjectModel,
    plan_file: PlanFileModel,
    proposal_id: str,
    action: str,
    actor: str | None = None,
    roles: dict[str, str] | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> ReviewResult:
    """Record a decision, and advance the baseline if policy is now satisfied.

    The decision names the exact version the reviewer saw, so it can never
    be replayed against different content.
    """
    authorization = authz.resolve(project, actor=actor)
    acting_as = authorization.actor
    effective_roles = roles if roles is not None else authorization.roles
    before = status(session, plan_file=plan_file, roles=effective_roles, policy=policy)
    proposal = before.proposals.get(proposal_id)
    if proposal is None:
        raise ValueError(f"no proposal {proposal_id} on this plan")

    # Withdrawing is the proposer's own act, so it needs no review role.
    if action != WITHDRAW:
        _require(effective_roles, authorization, workflow.MAY_REVIEW, "review this plan")

    event = workflow.make_decision(
        workspace_id=workspace_id_for(project),
        plan_file_id=str(plan_file.id),
        proposal_id=proposal_id,
        target_artifact_id=proposal.target_artifact_id,
        action=action,
        actor_user_id=acting_as,
    )
    save_event(session, event, str(plan_file.id))
    session.commit()

    if action != APPROVE:
        return ReviewResult(event=event, reason=f"recorded {action}")

    accepted, reason = _try_accept(
        session,
        project=project,
        plan_file=plan_file,
        proposal_id=proposal_id,
        actor=acting_as,
        roles=effective_roles,
        policy=policy,
    )
    return ReviewResult(event=event, accepted=accepted, reason=reason)


def _require(
    roles: dict[str, str], authorization: authz.Authorization, permitted: frozenset[str], what: str
) -> None:
    """Stop early when the resolved authorization does not allow this.

    The message names the reason the entitlement gave, because "you may not
    do that" without saying why is the least useful refusal there is.
    """
    held = roles.get(authorization.actor)
    if held in permitted:
        return
    detail = authorization.reason or (
        f"{authorization.actor} is a {held} here, and this needs " + " or ".join(sorted(permitted))
        if held
        else f"{authorization.actor} holds no role here"
    )
    raise PermissionError(f"cannot {what}: {detail}")


def _try_accept(
    session: Session,
    *,
    project: ProjectModel,
    plan_file: PlanFileModel,
    proposal_id: str,
    actor: str,
    roles: dict[str, str],
    policy: Policy,
) -> tuple[Event | None, str]:
    """Emit an accepted-head transition when the approvals now justify one.

    Returns the transition and why, or None and the reason it was withheld,
    so a caller can always explain the outcome to a human.
    """
    state = status(session, plan_file=plan_file, roles=roles, policy=policy)
    proposal = state.proposals.get(proposal_id)
    if proposal is None:
        return None, "proposal is no longer projected"
    if proposal.state == workflow.ACCEPTED:
        return None, "already the accepted baseline"
    if len(proposal.approvals) < policy.approvals_required:
        return None, (
            f"{len(proposal.approvals)} of {policy.approvals_required} "
            "required approvals recorded"
        )
    if roles.get(actor) != MAINTAINER:
        return None, f"{actor} may not advance the baseline"
    if state.conflicted:
        return None, "the baseline is contested and must be merged first"

    decisions = [
        event.event_id
        for event in load_review_events(session, str(plan_file.id))
        if event.payload.get("proposal_id") == proposal_id
        and event.payload.get("action") == APPROVE
    ]
    accepted = workflow.make_accepted_head(
        workspace_id=workspace_id_for(project),
        plan_file_id=str(plan_file.id),
        target_artifact_id=proposal.target_artifact_id,
        proposal_id=proposal_id,
        decision_event_ids=decisions,
        # Citing the heads we observed keeps this a descendant rather than a
        # second root, which is what would otherwise look like a conflict.
        predecessor_event_ids=state.accepted_event_ids,
        policy=policy,
        actor_user_id=actor,
    )
    save_event(session, accepted, str(plan_file.id))
    session.commit()
    return accepted, "baseline advanced"
