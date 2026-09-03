"""Who owns a plan, what is waiting on it, and what accepting does.

The pointer rule from `test_incoming_versions` has a cost: a teammate's
version can arrive, be verified, be written to disk, and change nothing you
would notice. `flanner list` read the pointer and nothing else, so the plan
looked untouched.

So the arrival has to be visible without being applied, and accepting it has
to actually move the thing every reader uses. Those are the two halves here.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from flanner import review, sync
from flanner.database import create_project, get_plan_file, get_session
from flanner.plan_ops import create_plan, materialize_version, record_new_version, standing
from flanner.workflow import APPROVE


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
def elsewhere(tmp_path):
    """A second catalog and working tree, standing in for a teammate."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from flanner.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'other.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    root = tmp_path / "other"
    root.mkdir()
    proj = create_project(session, name="q", project_root=str(root), auto_gitignore=False)
    proj.workspace_id = "ws_shared"
    session.commit()
    return session, proj


def _send(source: Any, artifact_id: str) -> Any:
    return sync.LocalPeer(source).fetch([artifact_id])[0]


# --- owner ---------------------------------------------------------------


def test_the_owner_is_whoever_wrote_the_first_version(project, plan_by_alice) -> None:
    """Not the last person to touch it.

    A plan five versions deep, most of them written by whoever happened to be
    reviewing, is still the author's plan. `list` showed a hardcoded "user"
    in this column for every row, on every device.
    """
    session, proj = project
    plan_file = plan_by_alice
    record_new_version(
        session, project=proj, plan_file=plan_file, content="# two\n", created_by="bob", notes=""
    )

    assert standing(session, plan_file).owner == "alice"


def test_a_plan_with_nothing_waiting_says_so(project, plan_by_alice) -> None:
    assert standing(session=get_session(), plan_file=plan_by_alice).waiting is None
    assert not standing(get_session(), plan_by_alice).has_incoming


# --- waiting -------------------------------------------------------------


def test_a_version_that_arrived_and_was_not_applied_is_reported(project, elsewhere) -> None:
    """The gap the pointer rule opens, and the reason this exists.

    Their v2 is verified, stored and on disk. The pointer stays on mine by
    design. Something has to say so, or the arrival is silent.
    """
    session, proj = project
    other_session, other_proj = elsewhere
    proj.workspace_id = "ws_shared"
    session.commit()

    plan_file, v1 = create_plan(
        session, project=proj, name="arch", content="# one\n", created_by="alice"
    )
    session.commit()
    envelope, payload = _send(session, v1.artifact_id)
    materialize_version(other_session, project=other_proj, envelope=envelope, managed_file=payload)

    # I write v2 here, so my pointer is a decision of mine.
    record_new_version(
        session,
        project=proj,
        plan_file=plan_file,
        content="# mine\n",
        created_by="alice",
        notes="",
    )
    theirs = record_new_version(
        other_session,
        project=other_proj,
        plan_file=get_plan_file(other_session, plan_file.id),
        content="# theirs\n",
        created_by="bob",
        notes="",
    )
    envelope, payload = _send(other_session, theirs.artifact_id)
    materialize_version(session, project=proj, envelope=envelope, managed_file=payload)

    here = standing(session, get_plan_file(session, plan_file.id))
    assert here.has_incoming, "a teammate's version arrived and nothing said so"
    assert here.waiting == 2


def test_waiting_reports_the_newest_of_several(project, elsewhere) -> None:
    """Three arrive while you are away; the flag is not a count of them."""
    session, proj = project
    other_session, other_proj = elsewhere
    proj.workspace_id = "ws_shared"
    session.commit()

    plan_file, v1 = create_plan(
        session, project=proj, name="arch", content="# one\n", created_by="alice"
    )
    session.commit()
    envelope, payload = _send(session, v1.artifact_id)
    materialize_version(other_session, project=other_proj, envelope=envelope, managed_file=payload)
    record_new_version(
        session,
        project=proj,
        plan_file=plan_file,
        content="# mine\n",
        created_by="alice",
        notes="",
    )

    mirror = get_plan_file(other_session, plan_file.id)
    for body in ("# two\n", "# three\n", "# four\n"):
        made = record_new_version(
            other_session,
            project=other_proj,
            plan_file=mirror,
            content=body,
            created_by="bob",
            notes="",
        )
        envelope, payload = _send(other_session, made.artifact_id)
        materialize_version(session, project=proj, envelope=envelope, managed_file=payload)

    assert standing(session, get_plan_file(session, plan_file.id)).waiting == 4


# --- accepting -----------------------------------------------------------


def test_accepting_a_baseline_moves_what_readers_open(project, plan_by_alice) -> None:
    """The other end of the same problem.

    Approval used to be recorded in the event log and nowhere else. `flanner
    show`, the web UI and every MCP tool read `current_version`, so the
    version the team agreed on was invisible to all of them.
    """
    session, proj = project
    plan_file = plan_by_alice
    record_new_version(
        session, project=proj, plan_file=plan_file, content="# two\n", created_by="alice", notes=""
    )
    plan_file.current_version = 1  # a reader still sitting on v1
    session.commit()

    proposal = review.propose(session, project=proj, plan_file=plan_file)
    decision = review.decide(
        session,
        project=proj,
        plan_file=plan_file,
        proposal_id=proposal.event.event_id,
        action=APPROVE,
    )

    assert decision.reason == "baseline advanced"
    assert get_plan_file(session, plan_file.id).current_version == 2, (
        "the baseline was accepted and `flanner show` still opens the old version"
    )


def test_a_proposal_that_falls_short_leaves_the_pointer_alone(project, plan_by_alice) -> None:
    """Only acceptance moves it. Proposing is not deciding."""
    session, proj = project
    plan_file = plan_by_alice
    record_new_version(
        session, project=proj, plan_file=plan_file, content="# two\n", created_by="alice", notes=""
    )
    plan_file.current_version = 1
    session.commit()

    review.propose(session, project=proj, plan_file=plan_file)

    assert get_plan_file(session, plan_file.id).current_version == 1


@pytest.fixture
def plan_by_alice(project):
    session, proj = project
    plan_file, _ = create_plan(
        session, project=proj, name="arch", content="# one\n", created_by="alice"
    )
    session.commit()
    return plan_file
