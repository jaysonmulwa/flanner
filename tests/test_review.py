"""Recording review end to end: propose, decide, advance the baseline."""

import subprocess

import pytest

from flanner import review
from flanner.database import create_project, get_session
from flanner.plan_ops import create_plan, record_new_version
from flanner.workflow import (
    APPROVE,
    EDITOR,
    MAINTAINER,
    REJECT,
    REQUEST_CHANGES,
    WITHDRAW,
    Policy,
)


@pytest.fixture
def project(db, tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    session = get_session()
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    proj = create_project(session, name="p", project_root=str(root), auto_gitignore=False)
    return session, proj


@pytest.fixture
def plan(project):
    session, proj = project
    plan_file, version = create_plan(
        session, project=proj, name="arch", content="# one\n", created_by="user"
    )
    session.commit()
    return plan_file, version


# --- the loop ---


def test_proposing_records_a_signed_event_without_moving_the_baseline(project, plan):
    session, proj = project
    plan_file, version = plan

    result = review.propose(session, project=proj, plan_file=plan_file, message="please look")

    assert result.event.payload["target_artifact_id"] == version.artifact_id
    assert result.advanced_baseline is False
    state = review.status(session, plan_file=plan_file)
    assert state.accepted_artifact_id is None  # proposing alone changes nothing
    assert state.proposals[result.event.event_id].state == "open"


def test_approving_advances_the_baseline(project, plan):
    session, proj = project
    plan_file, version = plan
    proposal = review.propose(session, project=proj, plan_file=plan_file)

    decision = review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
    )

    assert decision.advanced_baseline is True
    assert decision.reason == "baseline advanced"
    state = review.status(session, plan_file=plan_file)
    assert state.accepted_artifact_id == version.artifact_id
    assert state.conflicted is False


def test_the_events_survive_a_reload(project, plan):
    """Everything is persisted as artifacts, not held in memory."""
    session, proj = project
    plan_file, version = plan
    proposal = review.propose(session, project=proj, plan_file=plan_file)
    review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
    )
    session.expire_all()

    state = review.status(session, plan_file=plan_file)
    assert state.accepted_artifact_id == version.artifact_id


def test_assurance_sees_the_approval(project, plan):
    """The write surface feeds the verdict an agent reads."""
    from flanner import assurance

    session, proj = project
    plan_file, version = plan
    proposal = review.propose(session, project=proj, plan_file=plan_file)
    review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
    )

    verdict = assurance.assess(
        session, project=proj, plan_file=plan_file, roles=review.local_roles()
    )
    assert verdict.reviewed is True
    assert verdict.accepted_artifact_id == version.artifact_id
    assert not any("no approval" in w for w in verdict.warnings)


# --- decisions that must not advance anything ---


@pytest.mark.parametrize("action", [REJECT, REQUEST_CHANGES, WITHDRAW])
def test_a_non_approval_never_advances_the_baseline(project, plan, action):
    session, proj = project
    plan_file, _ = plan
    proposal = review.propose(session, project=proj, plan_file=plan_file)

    result = review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=action,
    )
    assert result.advanced_baseline is False
    assert review.status(session, plan_file=plan_file).accepted_artifact_id is None


def test_an_approval_short_of_policy_records_but_does_not_accept(project, plan):
    session, proj = project
    plan_file, _ = plan
    two = Policy(policy_id="two-eyes", approvals_required=2)
    roles = {"maria": MAINTAINER, "mo": MAINTAINER}

    proposal = review.propose(
        session, project=proj, plan_file=plan_file, actor="maria", roles=roles, policy=two
    )
    first = review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
        actor="maria",
        roles=roles,
        policy=two,
    )
    assert first.advanced_baseline is False
    assert "1 of 2" in first.reason

    second = review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
        actor="mo",
        roles=roles,
        policy=two,
    )
    assert second.advanced_baseline is True


def test_a_non_maintainer_cannot_advance_the_baseline(project, plan):
    session, proj = project
    plan_file, _ = plan
    roles = {"alice": EDITOR, "maria": MAINTAINER}
    proposal = review.propose(
        session, project=proj, plan_file=plan_file, actor="alice", roles=roles
    )

    result = review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
        actor="alice",
        roles=roles,
    )
    assert result.advanced_baseline is False
    assert review.status(session, plan_file=plan_file, roles=roles).accepted_artifact_id is None


def test_deciding_on_an_unknown_proposal_is_refused(project, plan):
    session, proj = project
    plan_file, _ = plan
    with pytest.raises(ValueError, match="no proposal"):
        review.decide(
            session,
            project=proj,
            plan_file=plan_file,
            proposal_id="sha256:nonexistent",
            action=APPROVE,
        )


def test_a_plan_with_no_signed_version_cannot_be_proposed(project):
    from flanner.database import create_plan_file

    session, proj = project
    empty = create_plan_file(session, project_id=proj.id, name="empty")
    session.commit()
    with pytest.raises(ValueError, match="no signed version"):
        review.propose(session, project=proj, plan_file=empty)


# --- successive baselines ---


def test_a_second_approval_supersedes_the_first_without_conflict(project, plan):
    """The new transition cites the old one, so it descends rather than diverges."""
    session, proj = project
    plan_file, v1 = plan

    first = review.propose(session, project=proj, plan_file=plan_file)
    review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=first.event.event_id,
        action=APPROVE,
    )

    v2 = record_new_version(
        session, project=proj, plan_file=plan_file, content="# two\n", created_by="user", notes=""
    )
    second = review.propose(session, project=proj, plan_file=plan_file)
    review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=second.event.event_id,
        action=APPROVE,
    )

    state = review.status(session, plan_file=plan_file)
    assert state.accepted_artifact_id == v2.artifact_id
    assert state.conflicted is False, "citing the previous head must not look like divergence"
    assert state.proposals[first.event.event_id].state == "superseded"


# --- the surfaces ---


def test_the_mcp_tools_drive_the_whole_loop(project, plan):
    from flanner import server

    session, proj = project
    plan_file, version = plan
    pid = str(plan_file.id)

    proposed = server.propose_plan_revision_tool(pid, message="review please", actor="user")
    assert proposed["target_artifact_id"] == version.artifact_id

    status = server.get_plan_workflow_status_tool(pid)
    assert status["accepted_artifact_id"] is None
    assert status["proposals"][0]["state"] == "open"

    decided = server.record_plan_review_decision_tool(
        pid, proposed["proposal_id"], "approve", actor="local"
    )
    assert decided["advanced_baseline"] is True

    after = server.get_plan_workflow_status_tool(pid)
    assert after["accepted_artifact_id"] == version.artifact_id
    assert after["conflicted"] is False


def test_the_mcp_tools_report_bad_input(project):
    from flanner import server

    assert server.propose_plan_revision_tool("not-a-uuid")["error"] is True
    assert server.get_plan_workflow_status_tool("not-a-uuid")["error"] is True
    assert (
        server.record_plan_review_decision_tool(
            "00000000-0000-0000-0000-000000000000", "p", "approve"
        )["error"]
        is True
    )


def test_local_roles_are_advisory_and_say_so():
    """A local role map gates nothing; the docstring must not pretend it does."""
    assert review.local_roles()[review.LOCAL_ACTOR] == MAINTAINER
    assert "not a security boundary" in review.__doc__


def test_both_read_paths_agree_about_who_may_act(project, plan):
    """review.status and assurance.assess must default to the same roles.

    They previously disagreed: one used the permissive local placeholder,
    the other an empty map. An approval recorded through the review surface
    then looked unauthorized to assurance, so the verdict silently
    contradicted the review status a moment earlier.
    """
    from flanner import assurance

    session, proj = project
    plan_file, version = plan
    proposal = review.propose(session, project=proj, plan_file=plan_file, actor="claude")
    review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
        actor="someone-else",
    )

    # Neither call is told about roles; they must still agree.
    assert review.status(session, plan_file=plan_file).accepted_artifact_id == version.artifact_id
    assert assurance.assess(session, project=proj, plan_file=plan_file).reviewed is True
