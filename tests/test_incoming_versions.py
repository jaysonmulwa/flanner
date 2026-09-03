"""What arriving from a peer does, and what it must never do.

Two defects met here. `sync_from_peer` reported `accepted` for artifacts it
stored and never turned into anything a person could open — no file, no
version record, invisible to `history`, the web UI and any agent. And
`materialize_version` advanced the plan's current-version pointer on
"higher number wins", so a teammate's push silently changed what you had
open even though your file was never touched.

The rule now: **your work is never displaced.** A plan you have only ever
received tracks along; the moment you write a version yourself, the pointer
is a decision of yours and a peer does not get to change it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from flanner import identity, sync
from flanner.database import get_plan_file, get_session, list_versions
from flanner.plan_ops import create_plan, materialize_version, record_new_version


def _dir(project: Any) -> Path:
    return Path(project.project_root) / project.plan_directory


def _transfer(session: Any, artifact_id: str) -> Any:
    return sync.LocalPeer(session).fetch([artifact_id])[0]


def test_a_received_version_becomes_a_file_and_a_record(sender, receiver) -> None:
    """The defect: `accepted` meant a row in a table and nothing on disk."""
    s_session, s_project = sender
    r_session, r_project = receiver
    plan_file, v1 = create_plan(
        s_session, project=s_project, name="arch", content="# one\n", created_by="alice"
    )
    s_session.commit()

    envelope, payload = _transfer(s_session, v1.artifact_id)
    assert materialize_version(
        r_session, project=r_project, envelope=envelope, managed_file=payload
    ).ok

    assert (_dir(r_project) / "arch_v1.md").exists(), "no file a person could open"
    assert [v.version for v in list_versions(r_session, plan_file.id)] == [1]


def test_a_peer_cannot_move_what_you_have_open(sender, receiver) -> None:
    """The safety property, and the one that was broken.

    Your file is never overwritten — a clash lands beside it under a
    conflict name. But the pointer used to advance on a higher version
    number, and `flanner show`, the web UI and every agent read the pointer.
    So a teammate pushing changed what you opened without touching a byte of
    your work.

    The setup has to share a plan id. An earlier draft had each side create
    its own plan named "arch", which are *different plans* and are refused
    on the name clash — so the pointer stayed put for the wrong reason and
    the test passed while the defect was still there.
    """
    s_session, s_project = sender
    r_session, r_project = receiver

    plan_file, v1 = create_plan(
        s_session, project=s_project, name="arch", content="# one\n", created_by="alice"
    )
    s_session.commit()
    envelope, payload = _transfer(s_session, v1.artifact_id)
    materialize_version(r_session, project=r_project, envelope=envelope, managed_file=payload)

    # Now the receiver writes its own version: the pointer becomes a choice.
    record_new_version(
        r_session,
        project=r_project,
        plan_file=get_plan_file(r_session, plan_file.id),
        content="# mine\n",
        created_by="me",
        notes="",
    )
    assert get_plan_file(r_session, plan_file.id).current_version == 2

    # The teammate goes on ahead: their v2, then a v3.
    record_new_version(
        s_session,
        project=s_project,
        plan_file=plan_file,
        content="# theirs two\n",
        created_by="alice",
        notes="",
    )
    v3 = record_new_version(
        s_session,
        project=s_project,
        plan_file=plan_file,
        content="# theirs three\n",
        created_by="alice",
        notes="",
    )

    envelope, payload = _transfer(s_session, v3.artifact_id)
    materialize_version(r_session, project=r_project, envelope=envelope, managed_file=payload)

    assert get_plan_file(r_session, plan_file.id).current_version == 2, (
        "a peer moved the current version, so `flanner show` now opens their work instead of mine"
    )
    # And it did arrive — the point is that it is available, not applied.
    assert (_dir(r_project) / "arch_v3.md").exists()


def test_a_plan_you_only_receive_tracks_along(sender, receiver) -> None:
    """The other half. Freezing on v1 would be its own bug.

    A history arriving in order, on a device that has authored nothing,
    should end up pointing at its newest version rather than its oldest.
    """
    s_session, s_project = sender
    r_session, r_project = receiver
    plan_file, v1 = create_plan(
        s_session, project=s_project, name="arch", content="# one\n", created_by="alice"
    )
    s_session.commit()
    v2 = record_new_version(
        s_session,
        project=s_project,
        plan_file=plan_file,
        content="# two\n",
        created_by="alice",
        notes="",
    )

    for version in (v1, v2):
        envelope, payload = _transfer(s_session, version.artifact_id)
        materialize_version(r_session, project=r_project, envelope=envelope, managed_file=payload)

    assert get_plan_file(r_session, plan_file.id).current_version == 2


def test_your_file_is_never_overwritten(sender, receiver) -> None:
    """Two devices both writing v2 of the same plan.

    The real clash, and the only one that can happen: two plans created
    independently under the same name are *different plans* and are refused
    rather than merged, because fusing separate histories is not
    recoverable. A genuine conflict needs one shared plan and one shared
    version number.
    """
    s_session, s_project = sender
    r_session, r_project = receiver

    plan_file, v1 = create_plan(
        s_session, project=s_project, name="arch", content="# one\n", created_by="alice"
    )
    s_session.commit()
    envelope, payload = _transfer(s_session, v1.artifact_id)
    materialize_version(r_session, project=r_project, envelope=envelope, managed_file=payload)

    # Both sides now write their own v2 of the same plan.
    mine = record_new_version(
        r_session,
        project=r_project,
        plan_file=get_plan_file(r_session, plan_file.id),
        content="# mine\n",
        created_by="me",
        notes="",
    )
    assert mine.version == 2
    theirs = record_new_version(
        s_session,
        project=s_project,
        plan_file=plan_file,
        content="# theirs\n",
        created_by="alice",
        notes="",
    )

    envelope, payload = _transfer(s_session, theirs.artifact_id)
    materialize_version(r_session, project=r_project, envelope=envelope, managed_file=payload)

    assert "# mine" in (_dir(r_project) / "arch_v2.md").read_text(encoding="utf-8"), (
        "my v2 was overwritten by theirs"
    )
    conflicts = list(_dir(r_project).glob("arch__conflict-*_v2.md"))
    assert conflicts, "their v2 was dropped rather than kept beside mine"
    assert "# theirs" in conflicts[0].read_text(encoding="utf-8")


def test_sync_makes_what_it_accepted_readable(sender, receiver) -> None:
    """End to end, through the function the CLI actually calls.

    This is the shape of the original report: `accepted: 1`, and nothing a
    user could find. It now writes the file as part of the sync.
    """
    s_session, s_project = sender
    r_session, r_project = receiver
    _, v1 = create_plan(
        s_session, project=s_project, name="arch", content="# one\n", created_by="alice"
    )
    s_session.commit()

    keys = {identity.device_id(): identity.device_public_key_b64()}
    report = sync.sync_from_peer(
        r_session, sync.LocalPeer(s_session), s_project.workspace_id or "", keys.get
    )

    assert report.accepted, f"nothing accepted: {report.rejected}"
    assert not report.unreadable, report.unreadable
    assert (_dir(r_project) / "arch_v1.md").exists(), (
        "sync reported accepted and wrote no file, which is the defect"
    )


@pytest.fixture
def sender(db, tmp_path, monkeypatch):
    from flanner.database import create_project

    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "sender-home"))
    session = get_session()
    root = tmp_path / "sender"
    root.mkdir()
    project = create_project(session, name="src", project_root=str(root), auto_gitignore=False)
    project.workspace_id = "ws_shared"
    session.commit()
    return session, project


@pytest.fixture
def receiver(tmp_path):
    """A second catalog and working tree, in the same process.

    Not a second identity: one process holds one device key. The test that
    turns on "did this device write it?" arranges that itself, because
    patching it in a fixture signs the *sender's* artifacts as the receiver
    and breaks key resolution for every sync in the file.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from flanner.database import Base, create_project

    engine = create_engine(f"sqlite:///{tmp_path / 'receiver.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    root = tmp_path / "receiver"
    root.mkdir()
    project = create_project(session, name="dst", project_root=str(root), auto_gitignore=False)
    project.workspace_id = "ws_shared"
    session.commit()
    return session, project
