"""Creating a plan when the disk says no.

The plan row is committed before its first version is written, so anything
that stops the write — a full disk, a read-only checkout, a directory
standing where the file should go — used to leave a plan with no versions.

That is worse than a plain failure. The empty row holds the name, so every
retry afterwards is refused as a duplicate: the error is permanent, it
survives fixing whatever caused it, and the only way out is deleting a row
by hand in a database most people will never open.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from flanner.database import (
    create_project,
    get_session,
    list_plan_files,
    list_versions,
)
from flanner.exceptions import DuplicateError
from flanner.plan_ops import create_plan


@pytest.fixture
def project(db, tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    session = get_session()
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    proj = create_project(session, name="p", project_root=str(root), auto_gitignore=False)
    return session, proj


def _block(project, name: str) -> Path:
    """Put a directory exactly where the plan's first version has to go.

    A real failure rather than a patched one: the same OSError a full disk
    or a read-only checkout produces, raised by the filesystem itself.
    """
    plans = Path(project.project_root) / project.plan_directory
    plans.mkdir(parents=True, exist_ok=True)
    blocker = plans / f"{name}_v1.md"
    blocker.mkdir()
    return blocker


def test_a_failed_write_leaves_no_plan_behind(project) -> None:
    session, proj = project
    _block(proj, "arch")

    with pytest.raises(OSError):
        create_plan(session, project=proj, name="arch", content="# x\n", created_by="me")

    assert list_plan_files(session, proj.id) == [], (
        "a plan with no versions was left holding the name"
    )


def test_the_retry_works_once_the_cause_is_fixed(project) -> None:
    """The consequence that made this worth fixing.

    Reproduced against the old code, the second attempt raised
    DuplicateError even after the obstruction was removed, so the failure
    outlived its cause.
    """
    session, proj = project
    blocker = _block(proj, "arch")

    with pytest.raises(OSError):
        create_plan(session, project=proj, name="arch", content="# x\n", created_by="me")

    blocker.rmdir()
    plan_file, version = create_plan(
        session, project=proj, name="arch", content="# x\n", created_by="me"
    )
    session.commit()

    assert version.version == 1
    assert (Path(proj.project_root) / proj.plan_directory / "arch_v1.md").is_file()
    assert [v.version for v in list_versions(session, plan_file.id)] == [1]


def test_a_genuine_duplicate_is_still_refused(project) -> None:
    """The cleanup must not turn a real duplicate into a silent overwrite."""
    session, proj = project
    create_plan(session, project=proj, name="arch", content="# one\n", created_by="me")
    session.commit()

    with pytest.raises(DuplicateError):
        create_plan(session, project=proj, name="arch", content="# two\n", created_by="me")

    assert len(list_plan_files(session, proj.id)) == 1


def test_an_unrelated_plan_survives_the_cleanup(project) -> None:
    """Only the plan that failed is removed, not the session's other work."""
    session, proj = project
    create_plan(session, project=proj, name="keep", content="# keep\n", created_by="me")
    session.commit()
    _block(proj, "doomed")

    with pytest.raises(OSError):
        create_plan(session, project=proj, name="doomed", content="# x\n", created_by="me")

    assert [pf.name for pf in list_plan_files(session, proj.id)] == ["keep"]
