"""Tests for the proposal / review / accepted-baseline projection (PRD §12.5)."""

import pytest

from flanner import artifacts, workflow
from flanner.workflow import (
    APPROVE,
    EDITOR,
    MAINTAINER,
    READER,
    REJECT,
    REQUEST_CHANGES,
    WITHDRAW,
    Policy,
)

WS = "ws_1"
PLAN = "plan_1"

ROLES = {"alice": EDITOR, "maria": MAINTAINER, "mo": MAINTAINER, "raj": READER}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))


def version_id(text: str) -> str:
    """Stand-in for a plan.version artifact id."""
    return artifacts.hash_text(text)


def propose(target, actor="alice", base=(), **kw):
    return workflow.make_proposal(
        workspace_id=WS,
        plan_file_id=PLAN,
        target_artifact_id=target,
        base_accepted_event_ids=base,
        actor_user_id=actor,
        **kw,
    )


def decide(proposal, action, actor="maria", target=None):
    return workflow.make_decision(
        workspace_id=WS,
        plan_file_id=PLAN,
        proposal_id=proposal.event_id,
        target_artifact_id=target or proposal.payload["target_artifact_id"],
        action=action,
        actor_user_id=actor,
    )


def accept(proposal, decisions, actor="maria", predecessors=(), **kw):
    return workflow.make_accepted_head(
        workspace_id=WS,
        plan_file_id=PLAN,
        target_artifact_id=proposal.payload["target_artifact_id"],
        proposal_id=proposal.event_id,
        decision_event_ids=[d.event_id for d in decisions],
        predecessor_event_ids=predecessors,
        actor_user_id=actor,
        **kw,
    )


# --- the central rule: syncing a proposal does not move the baseline ---


def test_a_synchronized_proposal_does_not_become_the_baseline():
    v1 = version_id("one")
    state = workflow.project([propose(v1)], ROLES)
    assert state.accepted_artifact_id is None
    assert state.proposals[list(state.proposals)[0]].state == workflow.OPEN


def test_approval_alone_does_not_move_the_baseline():
    # Only an accepted-head transition advances it, never a decision.
    p = propose(version_id("one"))
    state = workflow.project([p, decide(p, APPROVE)], ROLES)
    assert state.accepted_artifact_id is None


def test_acceptance_advances_the_baseline():
    v1 = version_id("one")
    p = propose(v1)
    d = decide(p, APPROVE)
    state = workflow.project([p, d, accept(p, [d])], ROLES)
    assert state.accepted_artifact_id == v1
    assert state.implementable_artifact_id == v1
    assert state.proposals[p.event_id].state == workflow.ACCEPTED


# --- event order must not matter ---


def test_events_project_the_same_in_any_order():
    p = propose(version_id("one"))
    d = decide(p, APPROVE)
    a = accept(p, [d])
    forward = workflow.project([p, d, a], ROLES)
    backward = workflow.project([a, d, p], ROLES)
    assert forward.accepted_artifact_id == backward.accepted_artifact_id
    assert not backward.pending


def test_a_decision_arriving_before_its_proposal_is_held_not_dropped():
    p = propose(version_id("one"))
    d = decide(p, APPROVE)
    state = workflow.project([d], ROLES)  # proposal has not arrived
    assert state.pending == [d.event_id]
    assert workflow.project([d, p], ROLES).proposals[p.event_id].approvals == ("maria",)


# --- authorization ---


def test_a_reader_cannot_propose():
    state = workflow.project([propose(version_id("one"), actor="raj")], ROLES)
    assert state.proposals == {}
    assert "may not propose" in state.rejected[0][1]


def test_an_editor_cannot_approve():
    p = propose(version_id("one"))
    state = workflow.project([p, decide(p, APPROVE, actor="alice")], ROLES)
    assert state.proposals[p.event_id].approvals == ()
    assert "may not review" in state.rejected[0][1]


def test_an_editor_cannot_advance_the_baseline():
    p = propose(version_id("one"))
    d = decide(p, APPROVE)
    state = workflow.project([p, d, accept(p, [d], actor="alice")], ROLES)
    assert state.accepted_artifact_id is None
    assert any("may not accept" in reason for _, reason in state.rejected)


def test_only_the_proposer_may_withdraw():
    p = propose(version_id("one"), actor="alice")
    state = workflow.project([p, decide(p, WITHDRAW, actor="mo")], ROLES)
    assert state.proposals[p.event_id].state == workflow.OPEN
    assert "only the proposer" in state.rejected[0][1]
    withdrawn = workflow.project([p, decide(p, WITHDRAW, actor="alice")], ROLES)
    assert withdrawn.proposals[p.event_id].state == workflow.WITHDRAWN


def test_self_approval_is_allowed_by_default_and_refused_under_independent_review():
    p = propose(version_id("one"), actor="maria")
    d = decide(p, APPROVE, actor="maria")
    assert workflow.project([p, d], ROLES).proposals[p.event_id].approvals == ("maria",)

    strict = Policy(policy_id="independent-v1", allow_self_approval=False)
    state = workflow.project([p, d], ROLES, strict)
    assert state.proposals[p.event_id].approvals == ()
    assert "independent review" in state.rejected[0][1]


def test_acceptance_needs_the_approvals_the_policy_demands():
    two = Policy(policy_id="two-eyes", approvals_required=2)
    p = propose(version_id("one"))
    d1 = decide(p, APPROVE, actor="maria")
    assert workflow.project([p, d1, accept(p, [d1])], ROLES, two).accepted_artifact_id is None
    d2 = decide(p, APPROVE, actor="mo")
    state = workflow.project([p, d1, d2, accept(p, [d1, d2])], ROLES, two)
    assert state.accepted_artifact_id == p.payload["target_artifact_id"]


def test_acceptance_cannot_cite_approvals_that_were_never_given():
    p = propose(version_id("one"))
    unrelated = decide(p, REQUEST_CHANGES)
    state = workflow.project([p, unrelated, accept(p, [unrelated])], ROLES)
    assert state.accepted_artifact_id is None
    assert any("do not satisfy" in reason for _, reason in state.rejected)


# --- decisions are bound to exact content ---


def test_a_decision_cannot_be_replayed_against_different_content():
    p = propose(version_id("one"))
    swapped = decide(p, APPROVE, target=version_id("something else"))
    state = workflow.project([p, swapped], ROLES)
    assert state.proposals[p.event_id].approvals == ()
    assert "different content" in state.rejected[0][1]


# --- rejection and changes requested ---


def test_rejection_preserves_the_proposal_without_advancing_anything():
    p = propose(version_id("one"))
    state = workflow.project([p, decide(p, REJECT)], ROLES)
    assert state.proposals[p.event_id].state == workflow.REJECTED
    assert state.accepted_artifact_id is None


def test_changes_requested_is_projected():
    p = propose(version_id("one"))
    state = workflow.project([p, decide(p, REQUEST_CHANGES)], ROLES)
    assert state.proposals[p.event_id].state == workflow.CHANGES_REQUESTED


# --- concurrency: the case the v5 review added ---


def test_two_offline_acceptances_conflict_rather_than_one_winning():
    p1, p2 = propose(version_id("left")), propose(version_id("right"))
    d1, d2 = decide(p1, APPROVE), decide(p2, APPROVE)
    state = workflow.project([p1, p2, d1, d2, accept(p1, [d1]), accept(p2, [d2])], ROLES)
    assert state.conflicted is True
    assert state.accepted_artifact_id is None
    assert state.implementable_artifact_id is None  # an agent must stop
    assert len(state.accepted_event_ids) == 2


def test_a_merge_covering_both_branches_restores_one_baseline():
    p1, p2 = propose(version_id("left")), propose(version_id("right"))
    d1, d2 = decide(p1, APPROVE), decide(p2, APPROVE)
    a1, a2 = accept(p1, [d1]), accept(p2, [d2])

    merged_version = version_id("merged")
    pm = propose(merged_version, base=[a1.event_id, a2.event_id])
    dm = decide(pm, APPROVE)
    am = accept(pm, [dm], predecessors=[a1.event_id, a2.event_id])

    state = workflow.project([p1, p2, d1, d2, a1, a2, pm, dm, am], ROLES)
    assert state.conflicted is False
    assert state.accepted_artifact_id == merged_version
    assert state.implementable_artifact_id == merged_version


def test_a_superseded_acceptance_stays_in_history():
    v1, v2 = version_id("one"), version_id("two")
    p1 = propose(v1)
    d1 = decide(p1, APPROVE)
    a1 = accept(p1, [d1])
    p2 = propose(v2, base=[a1.event_id])
    d2 = decide(p2, APPROVE)
    a2 = accept(p2, [d2], predecessors=[a1.event_id])

    state = workflow.project([p1, d1, a1, p2, d2, a2], ROLES)
    assert state.accepted_artifact_id == v2
    assert state.proposals[p1.event_id].state == workflow.SUPERSEDED
    assert state.proposals[p2.event_id].state == workflow.ACCEPTED


def test_a_proposal_based_on_an_overtaken_baseline_goes_stale():
    v1 = version_id("one")
    p1 = propose(v1)
    d1 = decide(p1, APPROVE)
    a1 = accept(p1, [d1])
    # Written against nothing, while the baseline has since moved to a1.
    behind = propose(version_id("written earlier"), base=[])

    state = workflow.project([p1, d1, a1, behind], ROLES)
    assert state.proposals[behind.event_id].state == workflow.STALE
    assert state.accepted_artifact_id == v1  # the baseline is untouched


def test_a_proposal_on_the_current_baseline_is_open_not_stale():
    p1 = propose(version_id("one"))
    d1 = decide(p1, APPROVE)
    a1 = accept(p1, [d1])
    current = propose(version_id("next"), base=[a1.event_id])
    state = workflow.project([p1, d1, a1, current], ROLES)
    assert state.proposals[current.event_id].state == workflow.OPEN


# --- events are signed artifacts like any other ---


def test_events_are_verifiable_signed_artifacts():
    from flanner import identity

    p = propose(version_id("one"))
    payload = artifacts.canonical_bytes(p.payload)
    assert artifacts.verify_artifact(p.artifact, identity.device_public_key_b64(), payload)
    assert p.artifact.artifact_type == artifacts.REVIEW_PROPOSAL


def test_unknown_review_action_is_refused_at_construction():
    p = propose(version_id("one"))
    with pytest.raises(ValueError, match="Unknown review action"):
        decide(p, "rubber_stamp")
