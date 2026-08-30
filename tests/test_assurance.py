"""Plan assurance: the one answer an agent needs before acting (PRD Phase 5)."""

import json
import subprocess
from pathlib import Path

import pytest

from flanner import assurance, workflow
from flanner.database import (
    create_project,
    get_session,
    save_artifact,
)
from flanner.plan_ops import create_plan, workspace_id_for
from flanner.workflow import APPROVE, BLOCK, EDITOR, MAINTAINER, Policy

ROLES = {"alice": EDITOR, "maria": MAINTAINER}


@pytest.fixture
def project(db, tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    session = get_session()
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "src.py").write_text("def rotate_tokens():\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    proj = create_project(session, name="p", project_root=str(root), auto_gitignore=False)
    return session, proj


_LEGACY = "def gone_function():\n    pass\n"


def drift(project):
    """Add a symbol, then remove it, so citing it is real drift.

    `gone_function` used to be enough on its own, until freshness learned
    to tell a deleted citation from one that was never here. A name that
    never existed is an env var or another repo's file, not evidence of a
    plan falling behind.
    """
    _session, proj = project
    root = Path(proj.project_root)
    (root / "legacy.py").write_text(_LEGACY, encoding="utf-8")
    _git_commit(root, "add legacy")
    (root / "legacy.py").unlink()
    _git_commit(root, "drop legacy")


def _git_commit(root, message):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message],
        cwd=root,
        check=True,
    )


def make_plan(project, content="Uses `src.py` and `rotate_tokens`.\n"):
    session, proj = project
    plan_file, version = create_plan(
        session, project=proj, name="arch", content=content, created_by="alice"
    )
    session.commit()
    return plan_file, version


def store(session, event, plan_file_id):
    """Persist a workflow event the way a synced one would arrive."""
    art = event.artifact
    save_artifact(
        session,
        artifact_id=art.artifact_id,
        artifact_type=art.artifact_type,
        workspace_id=art.workspace_id,
        content_hash=art.content_hash,
        actor_device_id=art.actor_device_id,
        created_at=art.created_at,
        signature=art.signature,
        plan_file_id=plan_file_id,
        parents=list(art.parents),
        actor_user_id=art.actor_user_id,
        payload=json.dumps(event.payload),
    )


# --- the exit criterion ---


def test_assurance_states_artifact_anchor_freshness_and_review(project):
    """PRD Phase 5 exit criterion, in one answer."""
    session, proj = project
    plan_file, version = make_plan(project)

    result = assurance.assess(session, project=proj, plan_file=plan_file)

    assert result.artifact_id == version.artifact_id  # the exact artifact
    assert result.commit_anchor  # the code it was written against
    assert result.freshness_status == "fresh"  # evidence
    assert result.reviewed is False  # and whether anyone approved
    assert result.evidence["evaluated_by_device"].startswith("dev_")


def test_a_fresh_unreviewed_plan_is_implementable_with_a_warning(project):
    session, proj = project
    plan_file, _ = make_plan(project)
    result = assurance.assess(session, project=proj, plan_file=plan_file)

    assert result.safe_to_implement is True
    assert any("no approval recorded" in w for w in result.warnings)
    assert result.blockers == ()


def test_evidence_names_the_device_that_produced_it(project):
    """Freshness is one device's claim about one checkout (PRD §13)."""
    session, proj = project
    plan_file, _ = make_plan(project)
    result = assurance.assess(session, project=proj, plan_file=plan_file)
    assert "evaluated_by_device" in result.evidence
    assert result.evidence["artifact_id"] == result.artifact_id


# --- freshness feeding the verdict ---


def test_a_stale_plan_warns_by_default(project):
    session, proj = project
    drift(project)
    plan_file, _ = make_plan(project, content="Uses `gone_function` which no longer exists.\n")

    result = assurance.assess(session, project=proj, plan_file=plan_file)
    assert result.freshness_status == "stale"
    assert result.safe_to_implement is True  # warn-only is the default
    assert any("stale" in w for w in result.warnings)


def test_a_workspace_can_choose_to_block_stale_plans(project):
    session, proj = project
    drift(project)
    plan_file, _ = make_plan(project, content="Uses `gone_function` which no longer exists.\n")

    strict = Policy(policy_id="strict-v1", stale_plans=BLOCK)
    result = assurance.assess(session, project=proj, plan_file=plan_file, policy=strict)
    assert result.safe_to_implement is False
    assert any("stale" in b for b in result.blockers)


def test_a_workspace_can_require_review(project):
    session, proj = project
    plan_file, _ = make_plan(project)

    strict = Policy(policy_id="reviewed-only", unreviewed_plans=BLOCK)
    result = assurance.assess(session, project=proj, plan_file=plan_file, policy=strict)
    assert result.safe_to_implement is False
    assert any("no approval recorded" in b for b in result.blockers)


# --- review history read back from stored artifacts ---


def approve(session, proj, plan_file, version):
    """Record a full approval: proposal, decision, accepted head."""
    ws = workspace_id_for(proj)
    pid = str(plan_file.id)
    proposal = workflow.make_proposal(
        workspace_id=ws,
        plan_file_id=pid,
        target_artifact_id=version.artifact_id,
        actor_user_id="alice",
    )
    decision = workflow.make_decision(
        workspace_id=ws,
        plan_file_id=pid,
        proposal_id=proposal.event_id,
        target_artifact_id=version.artifact_id,
        action=APPROVE,
        actor_user_id="maria",
    )
    accepted = workflow.make_accepted_head(
        workspace_id=ws,
        plan_file_id=pid,
        target_artifact_id=version.artifact_id,
        proposal_id=proposal.event_id,
        decision_event_ids=[decision.event_id],
        actor_user_id="maria",
    )
    for event in (proposal, decision, accepted):
        store(session, event, pid)


def test_an_approved_plan_reports_its_accepted_baseline(project):
    session, proj = project
    plan_file, version = make_plan(project)

    approve(session, proj, plan_file, version)

    result = assurance.assess(session, project=proj, plan_file=plan_file, roles=ROLES)
    assert result.reviewed is True
    assert result.accepted_artifact_id == version.artifact_id
    assert result.safe_to_implement is True
    assert not any("no approval" in w for w in result.warnings)


def test_an_approval_under_local_roles_says_it_authorizes_nothing(project):
    """The verdict that misleads by looking settled.

    A solo project projects review against a role map anyone holding the
    machine can edit, so `reviewed` is true and binds nobody. An agent
    reading that field alone would cite it as sign-off, which is the one
    thing it is not.
    """
    session, proj = project
    plan_file, version = make_plan(project)

    approve(session, proj, plan_file, version)

    result = assurance.assess(session, project=proj, plan_file=plan_file)

    assert result.reviewed is True
    assert result.authorization == "local"
    assert any("authorizes nothing" in w for w in result.warnings)
    # Exclusive with its opposite by construction: that one fires when
    # nothing was approved, this one when something was.
    assert not any("no approval recorded" in w for w in result.warnings)


def test_a_contested_baseline_always_blocks(project):
    """Not policy-configurable: there is no single answer to implement."""
    session, proj = project
    plan_file, version = make_plan(project)
    ws = workspace_id_for(proj)
    pid = str(plan_file.id)

    # Two peers accepted different versions while disconnected.
    for label in ("left", "right"):
        proposal = workflow.make_proposal(
            workspace_id=ws,
            plan_file_id=pid,
            target_artifact_id=f"sha256:{label}",
            message=label,
            actor_user_id="alice",
        )
        decision = workflow.make_decision(
            workspace_id=ws,
            plan_file_id=pid,
            proposal_id=proposal.event_id,
            target_artifact_id=f"sha256:{label}",
            action=APPROVE,
            actor_user_id="maria",
        )
        accepted = workflow.make_accepted_head(
            workspace_id=ws,
            plan_file_id=pid,
            target_artifact_id=f"sha256:{label}",
            proposal_id=proposal.event_id,
            decision_event_ids=[decision.event_id],
            actor_user_id="maria",
        )
        for event in (proposal, decision, accepted):
            store(session, event, pid)

    lenient = Policy(policy_id="everything-warns", stale_plans="warn", unreviewed_plans="warn")
    result = assurance.assess(
        session, project=proj, plan_file=plan_file, roles=ROLES, policy=lenient
    )
    assert result.conflicted is True
    assert result.safe_to_implement is False
    assert any("contested" in b for b in result.blockers)


def test_an_unparseable_event_payload_does_not_break_the_answer(project):
    session, proj = project
    plan_file, version = make_plan(project)
    save_artifact(
        session,
        artifact_id="sha256:corrupt",
        artifact_type="review.proposal",
        workspace_id=workspace_id_for(proj),
        content_hash="sha256:x",
        actor_device_id="dev_x",
        created_at="2026-01-01T00:00:00Z",
        signature="s",
        plan_file_id=str(plan_file.id),
        payload="{not json",
    )
    result = assurance.assess(session, project=proj, plan_file=plan_file, roles=ROLES)
    assert result.artifact_id == version.artifact_id  # still answerable


# --- degraded inputs ---


def test_a_plan_with_no_versions_is_not_implementable(project):
    from flanner.database import create_plan_file

    session, proj = project
    empty = create_plan_file(session, project_id=proj.id, name="empty")
    session.commit()
    result = assurance.assess(session, project=proj, plan_file=empty)
    assert result.safe_to_implement is False
    assert "no versions" in result.blockers[0]


def test_a_missing_plan_file_is_reported_not_raised(project):
    from pathlib import Path

    session, proj = project
    plan_file, version = make_plan(project)
    Path(version.file_path).unlink()

    result = assurance.assess(session, project=proj, plan_file=plan_file)
    assert result.freshness_status == "unknown"
    assert any("could not read" in r for r in result.freshness_reasons)


# --- the MCP surface ---


def test_the_mcp_tool_returns_the_whole_verdict(project):
    session, proj = project
    plan_file, version = make_plan(project)
    from flanner import server

    result = server.get_plan_assurance_tool(str(plan_file.id))
    assert result["artifact_id"] == version.artifact_id
    assert result["safe_to_implement"] is True
    assert result["commit_anchor"]
    assert "freshness_status" in result and "warnings" in result


def test_the_mcp_tool_reports_an_unknown_plan(project):
    from flanner import server

    assert server.get_plan_assurance_tool("not-a-uuid")["error"] is True
    assert server.get_plan_assurance_tool("00000000-0000-0000-0000-000000000000")["error"] is True
