"""Recording review end to end: propose, decide, advance the baseline."""

import subprocess

import pytest

from flanner import review, workflow
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
    """Refused here, and still refused if the event reaches a peer anyway.

    The write surface stops early so nobody records a decision that can
    never count. Projection enforces the same rule independently, because
    events arriving over the wire never passed through this check
    (test_workflow covers that side).
    """
    session, proj = project
    plan_file, _ = plan
    roles = {"alice": EDITOR, "maria": MAINTAINER}
    proposal = review.propose(
        session, project=proj, plan_file=plan_file, actor="alice", roles=roles
    )

    with pytest.raises(PermissionError, match="review this plan"):
        review.decide(
            session,
            project=proj,
            plan_file=plan_file,
            proposal_id=proposal.event.event_id,
            action=APPROVE,
            actor="alice",
            roles=roles,
        )
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
    assert review.local_roles()[workflow.LOCAL_ACTOR] == MAINTAINER
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


# --- enforcement, once a project joins a workspace ---


def a_signed_entitlement(role, *, workspace, user="maria"):
    """Cache a real entitlement for this device, granting `role` in `workspace`."""
    from datetime import datetime, timedelta, timezone

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from flanner import session as cache
    from flanner.artifacts import canonical_bytes
    from flanner.entitlements import Claims, WorkspaceCapability, encode_token
    from flanner.identity import public_key_b64, sign

    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    claims = Claims(
        organization_id="org_1",
        user_id=user,
        device_id="dev_abc",
        key_id="sk_1",
        issued_at=now.isoformat().replace("+00:00", "Z"),
        expires_at=(now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        workspace_capabilities=(
            (WorkspaceCapability(workspace_id=workspace, role=role),) if role else ()
        ),
    )
    cache.save(
        cache.Session(
            endpoint="https://api.example.test",
            device_id="dev_abc",
            organization_id="org_1",
            user_id=user,
            entitlement=encode_token(claims, sign(canonical_bytes(claims.to_dict()), key)),
            keyring={"sk_1": public_key_b64(key.public_key())},
        )
    )


def join(session, proj, workspace="ws_core"):
    proj.workspace_id = workspace
    session.commit()
    return workspace


def test_a_reader_may_not_propose_once_the_project_has_joined(project, plan):
    """The same call that succeeds solo is refused under a real capability."""
    session, proj = project
    plan_file, _ = plan
    workspace = join(session, proj)
    a_signed_entitlement(workflow.READER, workspace=workspace)

    with pytest.raises(PermissionError):
        review.propose(session, project=proj, plan_file=plan_file)


def test_an_editor_may_propose_but_not_advance_the_baseline(project, plan):
    session, proj = project
    plan_file, _ = plan
    workspace = join(session, proj)
    a_signed_entitlement(EDITOR, workspace=workspace)

    review.propose(session, project=proj, plan_file=plan_file)
    state = review.status(session, plan_file=plan_file, project=proj)
    proposal_id = next(iter(state.proposals))

    with pytest.raises(PermissionError, match="review this plan"):
        review.decide(
            session, project=proj, plan_file=plan_file, proposal_id=proposal_id, action=APPROVE
        )


def test_a_maintainer_carries_the_proposal_all_the_way(project, plan):
    session, proj = project
    plan_file, _ = plan
    workspace = join(session, proj)
    a_signed_entitlement(MAINTAINER, workspace=workspace)

    review.propose(session, project=proj, plan_file=plan_file)
    state = review.status(session, plan_file=plan_file, project=proj)
    result = review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=next(iter(state.proposals)),
        action=APPROVE,
    )
    assert result.accepted is not None, result.reason


def test_the_actor_recorded_is_the_one_the_entitlement_names(project, plan):
    """Not the local placeholder, or the role map would key to nobody."""
    session, proj = project
    plan_file, _ = plan
    workspace = join(session, proj)
    a_signed_entitlement(MAINTAINER, workspace=workspace, user="raj")

    result = review.propose(session, project=proj, plan_file=plan_file)
    assert result.event.artifact.actor_user_id == "raj"


def test_a_joined_project_with_no_entitlement_authorizes_nobody(project, plan):
    """Joining a team must not become more permissive when the token lapses."""
    session, proj = project
    plan_file, _ = plan
    join(session, proj)

    with pytest.raises(PermissionError):
        review.propose(session, project=proj, plan_file=plan_file)


def test_leaving_the_workspace_returns_the_project_to_advisory(project, plan):
    session, proj = project
    plan_file, _ = plan
    join(session, proj)
    proj.workspace_id = None
    session.commit()

    review.propose(session, project=proj, plan_file=plan_file)  # no longer refused


def test_assurance_reports_which_regime_judged_it(project, plan):
    from flanner import assurance

    session, proj = project
    plan_file, _ = plan
    assert assurance.assess(session, project=proj, plan_file=plan_file).authorization == "local"

    workspace = join(session, proj)
    a_signed_entitlement(MAINTAINER, workspace=workspace)
    verdict = assurance.assess(session, project=proj, plan_file=plan_file)
    assert verdict.authorization == "entitlement"


def test_assurance_says_so_when_it_cannot_check_authorization(project, plan):
    """Silence would read as "nobody approved", not "I cannot tell"."""
    from flanner import assurance

    session, proj = project
    plan_file, _ = plan
    join(session, proj)

    verdict = assurance.assess(session, project=proj, plan_file=plan_file)
    assert any("authorization is unavailable" in w for w in verdict.warnings)


def test_joining_warns_about_plans_written_before(project, plan):
    """Found by dogfooding: those artifacts silently never sync.

    The workspace id is inside the signed envelope, so joining cannot move
    an existing artifact into the team's workspace. The team has to be told,
    or they wait for plans that will never arrive.
    """

    session, proj = project
    result = _join(proj)

    assert result.exit_code == 0, result.output
    assert "stay local and will not sync" in result.output


def test_joining_a_project_with_no_history_says_nothing_about_it(project):
    session, proj = project
    result = _join(proj)

    assert result.exit_code == 0
    assert "will not sync" not in result.output


def _join(proj, workspace="ws_core"):
    """Run `flanner join` against the database the fixtures built.

    The CLI finds its catalog at FLANNER_HOME/data.db, which is not where
    the `db` fixture puts it, so point it there for the invocation.
    """
    from pathlib import Path

    from click.testing import CliRunner

    from flanner.cli import cli
    from flanner.database import get_db_path

    home = str(Path(get_db_path()).parent)
    return CliRunner(env={"FLANNER_HOME": home}).invoke(
        cli, ["join", workspace, "--project", proj.name]
    )
