"""Database layer tests, including the idempotent-init lookup."""

from flanner.database import (
    create_project,
    get_project_by_root,
    get_session,
    list_projects,
)


def test_get_project_by_root_missing(db, tmp_path):
    session = get_session()
    assert get_project_by_root(session, str(tmp_path)) is None


def test_create_and_find_project_by_root(db, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    session = get_session()

    project = create_project(
        session,
        name="test-project",
        description="Test project",
        project_root=str(root),
        plan_directory=".plans",
        auto_gitignore=False,
    )

    found = get_project_by_root(session, str(root))
    assert found is not None
    assert found.id == project.id
    assert found.name == "test-project"

    # Path normalization: forward slashes resolve to the same project
    found_fwd = get_project_by_root(session, str(root).replace("\\", "/"))
    assert found_fwd is not None
    assert found_fwd.id == project.id

    assert any(p.id == project.id for p in list_projects(session))
