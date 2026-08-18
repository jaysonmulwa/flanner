"""Is this plan safe to implement, and what exactly would I be implementing?

Phase 5's whole point, in one answer (PRD §25, Phase 5 exit criterion): an
agent should be able to state the exact artifact it is building from, the
code revision that artifact was written against, the freshness evidence for
it, and whether anyone approved it.

Those four facts already exist in separate places. This module joins them
and then applies the workspace policy, so the decision to proceed is made
once, consistently, rather than re-derived by each caller from parts they
might read differently.

Two rules are not policy-configurable, because getting them wrong is a
correctness failure rather than a preference:

- A contested baseline always blocks. Two peers accepted different versions
  while disconnected, so there is no single answer to "the current plan"
  and an agent must stop rather than pick one (§20).
- Evidence always names its evaluator. Freshness is computed against one
  checkout on one device, so a record that does not say which is not
  evidence, it is an opinion (§13).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from . import artifacts, identity, workflow
from .artifacts import Artifact
from .database import (
    PlanFileModel,
    ProjectModel,
    get_version,
    list_artifacts,
)
from .freshness import compute_freshness
from .frontmatter import read_managed
from .workflow import BLOCK, DEFAULT_POLICY, Policy

# Freshness statuses that mean the plan may no longer describe the code.
_DOUBTFUL = frozenset({"suspect", "stale"})

_REVIEW_TYPES = (
    artifacts.REVIEW_PROPOSAL,
    artifacts.REVIEW_DECISION,
    artifacts.ACCEPTED_HEAD,
)


@dataclass(frozen=True)
class Assurance:
    """Everything an agent needs to decide whether to act, and to cite why."""

    plan_name: str
    artifact_id: str | None = None
    version: int | None = None
    commit_anchor: str | None = None
    freshness_status: str = "unknown"
    freshness_reasons: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    accepted_artifact_id: str | None = None
    reviewed: bool = False
    conflicted: bool = False
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def safe_to_implement(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_name": self.plan_name,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "commit_anchor": self.commit_anchor,
            "freshness_status": self.freshness_status,
            "freshness_reasons": list(self.freshness_reasons),
            "evidence": self.evidence,
            "accepted_artifact_id": self.accepted_artifact_id,
            "reviewed": self.reviewed,
            "conflicted": self.conflicted,
            "safe_to_implement": self.safe_to_implement,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def load_review_events(session: Session, plan_file_id: str) -> list[workflow.Event]:
    """Rebuild a plan's review events from the artifacts that carry them.

    Events are stored like any other artifact, with their payload alongside.
    A payload that will not parse is skipped rather than raising: one bad
    row must not make a plan unreadable.
    """
    events: list[workflow.Event] = []
    for row in list_artifacts(session, plan_file_id=plan_file_id):
        if row.artifact_type not in _REVIEW_TYPES or row.payload is None:
            continue
        try:
            payload = json.loads(row.payload)
            parents = tuple(json.loads(row.parents))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        events.append(
            workflow.Event(
                artifact=Artifact(
                    artifact_type=row.artifact_type,
                    workspace_id=row.workspace_id,
                    content_hash=row.content_hash,
                    actor_device_id=row.actor_device_id,
                    created_at=row.created_at,
                    artifact_id=row.artifact_id,
                    signature=row.signature,
                    protocol_version=row.protocol_version,
                    organization_id=row.organization_id,
                    plan_file_id=row.plan_file_id,
                    actor_user_id=row.actor_user_id,
                    parents=parents,
                ),
                payload=payload,
            )
        )
    return events


def assess(
    session: Session,
    *,
    project: ProjectModel,
    plan_file: PlanFileModel,
    roles: dict[str, str] | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> Assurance:
    """Judge one plan against its code and its review history."""
    version = get_version(session, plan_file.id, None)
    if version is None:
        return Assurance(
            plan_name=plan_file.name,
            blockers=("this plan has no versions",),
        )

    # Defaults to the same local placeholder the review surface uses. If
    # the two disagreed, an approval recorded through one would look
    # unauthorized to the other, and the verdict would silently contradict
    # the review status. Note `roles or ...` would be wrong: the
    # placeholder is an empty mapping that answers for every actor.
    state = workflow.project(
        load_review_events(session, str(plan_file.id)),
        roles if roles is not None else workflow.local_roles(),
        policy,
    )

    # Prefer the team's accepted baseline; fall back to the newest local
    # version when no review has happened, which is the normal solo case.
    artifact_id = state.accepted_artifact_id or version.artifact_id
    reviewed = state.accepted_artifact_id is not None

    evidence, status, reasons, anchor = _freshness_for(project, version)
    blockers: list[str] = []
    warnings: list[str] = []

    # Not configurable: a contested baseline has no single answer (§20).
    if state.conflicted:
        blockers.append("the accepted baseline is contested; two acceptances must be merged first")

    if status in _DOUBTFUL:
        detail = f"plan is {status}: " + "; ".join(reasons)
        (blockers if policy.stale_plans == BLOCK else warnings).append(detail)

    if not reviewed:
        detail = "no approval recorded for this plan"
        (blockers if policy.unreviewed_plans == BLOCK else warnings).append(detail)

    if not evidence.get("git_available"):
        warnings.append("no git repository, so freshness was judged on age alone")

    return Assurance(
        plan_name=plan_file.name,
        artifact_id=artifact_id,
        version=version.version,
        commit_anchor=anchor,
        freshness_status=status,
        freshness_reasons=tuple(reasons),
        evidence=evidence,
        accepted_artifact_id=state.accepted_artifact_id,
        reviewed=reviewed,
        conflicted=state.conflicted,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def _freshness_for(
    project: ProjectModel, version: Any
) -> tuple[dict[str, Any], str, list[str], str | None]:
    """Freshness evidence for a version, stamped with who evaluated it."""
    root = project.project_root
    if not root:
        return ({}, "unknown", ["project has no project_root configured"], None)
    try:
        _, body = read_managed(Path(version.file_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return ({}, "unknown", [f"could not read the plan file: {e}"], None)

    evidence = compute_freshness(root, body, version.created_at)
    # Evidence is a claim by one device about one checkout, and is only
    # meaningful alongside who made it (§13).
    evidence["evaluated_by_device"] = identity.device_id()
    evidence["artifact_id"] = version.artifact_id
    return (
        evidence,
        str(evidence.get("status", "unknown")),
        list(evidence.get("reasons", [])),
        evidence.get("anchored_at_commit"),
    )
