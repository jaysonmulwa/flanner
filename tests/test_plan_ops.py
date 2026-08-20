"""Direct tests for the shared plan write-path."""

import subprocess

import pytest

from flanner.database import create_plan_file, create_project, get_session
from flanner.exceptions import DatabaseError
from flanner.plan_ops import write_version


def _project(session, root, project_root=None):
    return create_project(
        session,
        name="wv",
        project_root=str(project_root) if project_root is not None else None,
        plan_directory=".plans",
        auto_gitignore=False,
    )


def test_write_version_saves_file_and_records_row(db, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    session = get_session()
    project = _project(session, root, project_root=root)
    plan_file = create_plan_file(session, project_id=project.id, name="design")

    version = write_version(
        session,
        project=project,
        plan_file=plan_file,
        version=1,
        content="# Design\n\nbody",
        created_by="tester",
        notes="Initial version",
    )

    assert version.version == 1
    assert version.created_by == "tester"
    assert version.content_hash  # body hashed
    written = (root / ".plans" / "design_v1.md").read_text(encoding="utf-8")
    assert "mcp_plan_file: true" in written  # frontmatter added
    assert "# Design" in written


def test_write_version_crlf_body_matches_lf_hash(db, tmp_path):
    # A CRLF body (as a browser submits) must hash and store identically to the
    # LF equivalent, so an unchanged plan is not seen as modified and the file
    # never accumulates carriage returns.
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    session = get_session()
    project = _project(session, root, project_root=root)
    lf = create_plan_file(session, project_id=project.id, name="lf")
    crlf = create_plan_file(session, project_id=project.id, name="crlf")

    v_lf = write_version(
        session,
        project=project,
        plan_file=lf,
        version=1,
        content="# T\n\nbody\n",
        created_by="t",
        notes="",
    )
    v_crlf = write_version(
        session,
        project=project,
        plan_file=crlf,
        version=1,
        content="# T\r\n\r\nbody\r\n",
        created_by="t",
        notes="",
    )
    assert v_lf.content_hash == v_crlf.content_hash
    assert b"\r" not in (root / ".plans" / "crlf_v1.md").read_bytes()


def test_write_version_rejects_project_without_root(db):
    session = get_session()
    project = _project(session, root=None, project_root=None)
    plan_file = create_plan_file(session, project_id=project.id, name="x")

    with pytest.raises(DatabaseError):
        write_version(
            session,
            project=project,
            plan_file=plan_file,
            version=1,
            content="body",
            created_by="tester",
            notes="",
        )


# --- joining a workspace with plans already written ----------------------
#
# A workspace id sits inside the signed envelope, so joining cannot move an
# artifact written before it. Without adoption a team that starts using
# flanner after six months finds their existing plans invisible to each
# other, which reads as broken rather than as a design.


def _joined_project(session, tmp_path, name="wv"):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    project = create_project(
        session,
        name=name,
        project_root=str(root),
        plan_directory=".plans",
        auto_gitignore=False,
    )
    return project


def _plan_with_versions(session, project, name, bodies):
    plan_file = create_plan_file(session, project_id=project.id, name=name)
    for n, body in enumerate(bodies, start=1):
        write_version(
            session,
            project=project,
            plan_file=plan_file,
            version=n,
            content=body,
            created_by="maria",
            notes="",
        )
    return plan_file


def test_adoption_re_roots_the_current_head_in_the_workspace(db, tmp_path):
    from flanner.database import ArtifactModel, get_version
    from flanner.plan_ops import adopt_into_workspace

    session = get_session()
    project = _joined_project(session, tmp_path)
    plan_file = _plan_with_versions(session, project, "design", ["# one\n", "# two\n"])

    before = get_version(session, plan_file.id, None).artifact_id
    report = adopt_into_workspace(session, project=project, workspace_id="ws_team")

    assert report.adopted == ("design",)
    latest = get_version(session, plan_file.id, None)
    assert latest.artifact_id != before

    adopted = session.query(ArtifactModel).filter_by(artifact_id=latest.artifact_id).one()
    assert adopted.workspace_id == "ws_team"
    # Content is carried, not re-hashed from something else.
    assert adopted.content_hash == latest.content_hash


def test_the_adopted_artifact_is_a_root_and_not_a_dangling_child(db, tmp_path):
    """Its real parent is in a workspace no peer can verify."""
    import json

    from flanner.database import ArtifactModel, get_version
    from flanner.plan_ops import adopt_into_workspace

    session = get_session()
    project = _joined_project(session, tmp_path)
    plan_file = _plan_with_versions(session, project, "design", ["# one\n", "# two\n"])
    adopt_into_workspace(session, project=project, workspace_id="ws_team")

    latest = get_version(session, plan_file.id, None)
    adopted = session.query(ArtifactModel).filter_by(artifact_id=latest.artifact_id).one()
    assert json.loads(adopted.parents) == []


def test_local_history_is_kept_exactly_as_it_was(db, tmp_path):
    """Adoption adds an artifact. It does not rewrite or delete any."""
    from flanner.database import ArtifactModel
    from flanner.plan_ops import adopt_into_workspace

    session = get_session()
    project = _joined_project(session, tmp_path)
    _plan_with_versions(session, project, "design", ["# one\n", "# two\n"])

    local = f"local:{project.id}"
    before = {
        row.artifact_id: row.workspace_id
        for row in session.query(ArtifactModel).filter_by(workspace_id=local)
    }
    assert len(before) == 2

    adopt_into_workspace(session, project=project, workspace_id="ws_team")

    after = {
        row.artifact_id: row.workspace_id
        for row in session.query(ArtifactModel).filter_by(workspace_id=local)
    }
    assert after == before


def test_the_next_version_chains_into_the_workspace(db, tmp_path):
    """Otherwise every later version points back across the boundary."""
    import json

    from flanner.database import ArtifactModel, get_version
    from flanner.plan_ops import adopt_into_workspace

    session = get_session()
    project = _joined_project(session, tmp_path)
    plan_file = _plan_with_versions(session, project, "design", ["# one\n"])
    adopt_into_workspace(session, project=project, workspace_id="ws_team")
    adopted_id = get_version(session, plan_file.id, None).artifact_id

    project.workspace_id = "ws_team"
    session.commit()
    write_version(
        session,
        project=project,
        plan_file=plan_file,
        version=2,
        content="# after joining\n",
        created_by="maria",
        notes="",
    )

    latest = get_version(session, plan_file.id, None)
    row = session.query(ArtifactModel).filter_by(artifact_id=latest.artifact_id).one()
    assert row.workspace_id == "ws_team"
    assert json.loads(row.parents) == [adopted_id]


def test_adopting_twice_changes_nothing(db, tmp_path):
    from flanner.database import get_version
    from flanner.plan_ops import adopt_into_workspace

    session = get_session()
    project = _joined_project(session, tmp_path)
    plan_file = _plan_with_versions(session, project, "design", ["# one\n"])

    first = adopt_into_workspace(session, project=project, workspace_id="ws_team")
    settled = get_version(session, plan_file.id, None).artifact_id
    second = adopt_into_workspace(session, project=project, workspace_id="ws_team")

    assert first.adopted == ("design",)
    assert second.adopted == ()
    assert second.already_there == ("design",)
    assert get_version(session, plan_file.id, None).artifact_id == settled


def test_a_plan_with_no_versions_is_skipped_rather_than_invented(db, tmp_path):
    from flanner.plan_ops import adopt_into_workspace

    session = get_session()
    project = _joined_project(session, tmp_path)
    create_plan_file(session, project_id=project.id, name="empty")

    report = adopt_into_workspace(session, project=project, workspace_id="ws_team")
    assert report.adopted == ()
    assert report.skipped == (("empty", "no versions"),)


def test_every_plan_in_the_project_is_carried(db, tmp_path):
    from flanner.plan_ops import adopt_into_workspace

    session = get_session()
    project = _joined_project(session, tmp_path)
    _plan_with_versions(session, project, "design", ["# a\n"])
    _plan_with_versions(session, project, "rollout", ["# b\n"])

    report = adopt_into_workspace(session, project=project, workspace_id="ws_team")
    assert sorted(report.adopted) == ["design", "rollout"]
