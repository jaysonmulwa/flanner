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
