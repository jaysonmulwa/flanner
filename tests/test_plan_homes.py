"""Where a pulled plan goes when a workspace has more than one repository.

A workspace is a team, and a team has more than one repository, so the
workspace id on an artifact cannot say which local project a plan belongs
in. The old answer was `.first()`: whichever project the query returned.
Not a rule anyone could predict, and it put plans in the wrong checkout.

The rule now: a plan already held goes where it lives; the project the
person named is used; a lone project in the workspace needs no choosing;
anything else is reported and not guessed. Reporting is only acceptable
because a later pull with `--project` actually writes what was stored, so
the second half of this file is about that.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from flanner import identity, sync
from flanner.database import create_project, get_session, list_plan_files
from flanner.plan_ops import create_plan, record_new_version

WS = "ws_team"


def _repo(where: Path) -> str:
    where.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(where)], check=True)
    return str(where)


def _plans_in(project: Any) -> set[str]:
    return {p.name for p in Path(project.project_root, project.plan_directory).glob("*.md")}


@pytest.fixture
def teammate(tmp_path):
    """Somebody else's catalog, with a plan in the shared workspace.

    Same device key as the receiver — one process holds one — which is fine
    here: `LocalPeer` hands artifacts over without signing a request, and
    the receiver resolves the author's key to its own.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from flanner.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'teammate.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    project = create_project(
        session, name="theirs", project_root=_repo(tmp_path / "theirs"), auto_gitignore=False
    )
    project.workspace_id = WS
    session.commit()
    plan_file, _ = create_plan(
        session, project=project, name="arch", content="# arch\n", created_by="alice"
    )
    session.commit()
    return session, project, plan_file


@pytest.fixture
def knows_author():
    return {identity.device_id(): identity.device_public_key_b64()}.get


@pytest.fixture
def two_repos(db, tmp_path):
    """The receiving device: two local projects, both in the workspace."""
    session = get_session()
    api = create_project(session, name="api", project_root=_repo(tmp_path / "api"))
    web = create_project(session, name="web", project_root=_repo(tmp_path / "web"))
    api.workspace_id = web.workspace_id = WS
    session.commit()
    return session, api, web


# --- the rule ---------------------------------------------------------------


def test_two_repositories_and_no_say_is_reported_not_guessed(two_repos, teammate, knows_author):
    session, api, web = two_repos
    their_session, _, _ = teammate

    report = sync.sync_from_peer(session, sync.LocalPeer(their_session), WS, knows_author)

    assert report.accepted, "the artifact itself should still be taken and stored"
    assert len(report.unreadable) == 1
    _, why = report.unreadable[0]
    assert "2 local projects share that workspace" in why
    assert "api, web" in why
    assert "--project" in why
    assert _plans_in(api) == set() and _plans_in(web) == set(), "it guessed"


def test_the_project_the_person_named_is_where_it_goes(two_repos, teammate, knows_author):
    session, api, web = two_repos
    their_session, _, _ = teammate

    report = sync.sync_from_peer(
        session, sync.LocalPeer(their_session), WS, knows_author, project=web
    )

    assert report.unreadable == []
    assert _plans_in(web) == {"arch_v1.md"}
    assert _plans_in(api) == set()


def test_a_lone_project_in_the_workspace_needs_no_choosing(db, tmp_path, teammate, knows_author):
    session = get_session()
    only = create_project(session, name="only", project_root=_repo(tmp_path / "only"))
    only.workspace_id = WS
    session.commit()
    their_session, _, _ = teammate

    report = sync.sync_from_peer(session, sync.LocalPeer(their_session), WS, knows_author)

    assert report.unreadable == []
    assert _plans_in(only) == {"arch_v1.md"}


def test_a_plan_already_held_stays_where_it_lives(two_repos, teammate, knows_author):
    """Naming the other project does not split a plan across two checkouts."""
    session, api, web = two_repos
    their_session, their_project, their_plan = teammate
    sync.sync_from_peer(session, sync.LocalPeer(their_session), WS, knows_author, project=api)
    assert _plans_in(api) == {"arch_v1.md"}

    record_new_version(
        their_session,
        project=their_project,
        plan_file=their_plan,
        content="# arch, again\n",
        created_by="alice",
        notes="",
    )
    report = sync.sync_from_peer(
        session, sync.LocalPeer(their_session), WS, knows_author, project=web
    )

    assert report.unreadable == []
    assert _plans_in(api) == {"arch_v1.md", "arch_v2.md"}, "v2 did not follow v1"
    assert _plans_in(web) == set(), "the plan was split across two projects"


def test_a_project_named_outside_the_workspace_is_not_used(db, tmp_path, teammate, knows_author):
    """`--project` is a choice among the workspace's projects, not an override."""
    session = get_session()
    elsewhere = create_project(session, name="other", project_root=_repo(tmp_path / "other"))
    session.commit()
    their_session, _, _ = teammate

    report = sync.sync_from_peer(
        session, sync.LocalPeer(their_session), WS, knows_author, project=elsewhere
    )

    assert report.unreadable and "no local project is in that workspace" in report.unreadable[0][1]
    assert _plans_in(elsewhere) == set()


# --- the second pull ----------------------------------------------------------


def test_what_was_stored_but_not_written_is_written_by_the_next_pull(
    two_repos, teammate, knows_author
):
    """The property that makes "reported, not guessed" acceptable.

    The fetch loop only ingests what this device lacks, so after the first
    pull stored the artifact, a second pull would report it as already
    held and never look at it again. The plan would be stuck: verified,
    stored, and never a file. This is the pull that unsticks it.
    """
    session, api, web = two_repos
    their_session, _, _ = teammate
    first = sync.sync_from_peer(session, sync.LocalPeer(their_session), WS, knows_author)
    assert first.unreadable and not _plans_in(api) and not _plans_in(web)

    second = sync.sync_from_peer(
        session, sync.LocalPeer(their_session), WS, knows_author, project=api
    )

    assert second.accepted == [], "nothing new was fetched, which is correct"
    assert second.unreadable == []
    assert _plans_in(api) == {"arch_v1.md"}
    assert [p.name for p in list_plan_files(session, api.id)] == ["arch"]


def test_a_device_never_rewrites_its_own_plans(db, tmp_path, knows_author):
    """Every plan this device wrote has a version row, so the second-chance
    pass must skip all of them. Written again they would land beside
    themselves as conflict copies."""
    session = get_session()
    mine = create_project(session, name="mine", project_root=_repo(tmp_path / "mine"))
    mine.workspace_id = WS
    session.commit()
    create_plan(session, project=mine, name="own", content="# own\n", created_by="me")
    session.commit()

    class Nobody:
        def manifest(self) -> Any:
            return sync.Manifest(workspace_id=WS, artifact_ids=frozenset(), heads=frozenset())

        def fetch(self, ids: list[str]) -> list[Any]:
            return []

    report = sync.sync_from_peer(session, Nobody(), WS, knows_author)

    assert report.unreadable == []
    assert _plans_in(mine) == {"own_v1.md"}, "a conflict copy of the device's own plan appeared"
