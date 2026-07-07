"""MCP server tool tests."""

import os
import subprocess

from flanner.server import (
    get_plan_config,
    list_projects,
    create_project_tool,
    list_plan_files_tool,
    create_plan_file_tool,
)


def test_get_plan_config_has_default_directory(db):
    config = get_plan_config()
    assert config["plan_directory"]


def test_list_projects_empty(db):
    assert list_projects() == []


def test_create_project_and_plan_file(db, tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)

    result = create_project_tool(
        name="test-project",
        description="A test project",
        project_root=str(project_root),
        plan_directory=".plans",
    )
    assert not result.get("error"), result.get("message")
    assert result["name"] == "test-project"
    project_id = result["id"]

    assert list_plan_files_tool(project_id) == []

    plan = create_plan_file_tool(
        project_id=project_id,
        name="test-architecture",
        content="# Test Architecture Plan\n\nBody.\n",
        description="Test architecture plan",
        created_by="pytest",
    )
    assert not plan.get("error"), plan.get("message")
    assert plan["name"] == "test-architecture"
    assert plan["version"]
    assert os.path.exists(plan["file_path"])

    assert len(list_plan_files_tool(project_id)) == 1
