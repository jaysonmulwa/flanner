"""CLI tests via click.testing.CliRunner.

All invocations run with FLANNER_HOME pointed at a per-test temp directory so
nothing touches the real ~/.flanner. Claude-config-writing commands monkeypatch
get_claude_config_path so the real Claude config is never written.
"""

import json
import os
from uuid import uuid4

import pytest
from click.testing import CliRunner

import flanner.claude_integration as ci
from flanner.cli import cli
from flanner.database import (
    create_jira_link,
    create_plan_file,
    create_project,
    create_version,
    get_session,
    init_database,
)

JIRA_URL = "https://x.atlassian.net"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def runner(home):
    return CliRunner(env={"FLANNER_HOME": str(home), "FLANNER_DB_PATH": None})


@pytest.fixture
def initialized(home):
    """Database file at FLANNER_HOME/data.db, singleton pointed at it."""
    init_database(str(home / "data.db"))


@pytest.fixture
def project(initialized, git_repo):
    """Project named 'proj'; returns its UUID (plain value, no ORM detachment)."""
    return create_project(get_session(), name="proj", project_root=str(git_repo)).id


@pytest.fixture
def plan(project, git_repo):
    """Plan file 'myplan' with one version; returns its UUID."""
    session = get_session()
    pf = create_plan_file(session, project, "myplan")
    create_version(session, pf.id, 1, str(git_repo / ".plans" / "myplan_v1.md"), "hash1")
    return pf.id


@pytest.fixture
def claude_config(tmp_path, monkeypatch):
    """Redirect all Claude config reads/writes to a temp file."""
    path = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(ci, "get_claude_config_path", lambda: path)
    return path


# --- guard: commands that require an initialized database exit 1 ---


@pytest.mark.parametrize(
    "args",
    [
        ["list"],
        ["config", "p"],
        ["setup-gitignore", "p"],
        ["delete", "p"],
        ["sync"],
        ["web"],
        ["jira", "config", "p", "--url", JIRA_URL],
        ["jira", "link", "p", "--issue", "A-1"],
        ["jira", "unlink", "p"],
        ["jira", "links"],
        ["jira", "show", "p"],
    ],
)
def test_commands_require_db(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 1
    assert "Database not initialized" in result.output


# --- init ---


def test_init_creates_project(runner, home, git_repo):
    result = runner.invoke(
        cli,
        ["init", "--skip-claude", "--project-root", str(git_repo)],
        input="myproj\n",
    )
    assert result.exit_code == 0, result.output
    assert "Initialized Flanner" in result.output
    assert "Created project: myproj" in result.output
    assert (home / "data.db").exists()
    assert (git_repo / ".plans").is_dir()
    assert ".plans/" in (git_repo / ".gitignore").read_text()


def test_init_existing_project(runner, git_repo):
    runner.invoke(
        cli, ["init", "--skip-claude", "--project-root", str(git_repo)], input="myproj\n"
    )
    result = runner.invoke(cli, ["init", "--skip-claude", "--project-root", str(git_repo)])
    assert result.exit_code == 0
    assert "Project already exists: myproj" in result.output


def test_init_force_new_project(runner, git_repo):
    runner.invoke(
        cli, ["init", "--skip-claude", "--project-root", str(git_repo)], input="myproj\n"
    )
    result = runner.invoke(
        cli,
        ["init", "--skip-claude", "--project-root", str(git_repo), "--force-new-project"],
        input="second\n",
    )
    assert result.exit_code == 0
    assert "already exists here" in result.output
    assert "Created project: second" in result.output


def test_init_force_new_duplicate_name_errors(runner, git_repo):
    runner.invoke(
        cli, ["init", "--skip-claude", "--project-root", str(git_repo)], input="myproj\n"
    )
    result = runner.invoke(
        cli,
        ["init", "--skip-claude", "--project-root", str(git_repo), "--force-new-project"],
        input="myproj\n",
    )
    assert result.exit_code == 0
    assert "already exists" in result.output


def test_init_no_git_repo_skips_project(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # not a git repo
    result = runner.invoke(cli, ["--verbose", "init", "--skip-claude"])
    assert result.exit_code == 0
    assert "Initialized Flanner" in result.output
    assert "Detected git repository" not in result.output


def test_init_registers_with_claude(runner, git_repo, claude_config):
    result = runner.invoke(cli, ["init", "--project-root", str(git_repo)], input="regproj\n")
    assert result.exit_code == 0, result.output
    assert "Registering MCP server" in result.output
    assert "flanner" in json.loads(claude_config.read_text())["mcpServers"]


# --- list ---


def test_list_empty(runner, initialized):
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "No projects yet" in result.output


def test_list_table(runner, project):
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "proj" in result.output


def test_list_json(runner, project):
    result = runner.invoke(cli, ["--quiet", "list", "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["name"] == "proj"
    assert data[0]["plan_files"] == 0


def test_list_project_plans(runner, plan):
    result = runner.invoke(cli, ["list", "--project", "proj"])
    assert result.exit_code == 0
    assert "myplan" in result.output


def test_list_project_no_plans(runner, project):
    result = runner.invoke(cli, ["list", "--project", "proj"])
    assert result.exit_code == 0
    assert "No plan files yet" in result.output


def test_list_project_missing(runner, initialized):
    result = runner.invoke(cli, ["list", "--project", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.output


# --- config ---


def test_config_updates_project(runner, project, git_repo):
    result = runner.invoke(
        cli, ["config", "proj", "--plan-dir", "docs/plans", "--auto-gitignore", "true"]
    )
    assert result.exit_code == 0, result.output
    assert "updated" in result.output
    assert "docs/plans" in result.output
    assert "docs/plans/" in (git_repo / ".gitignore").read_text()


def test_config_project_missing(runner, initialized):
    result = runner.invoke(cli, ["config", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.output


# --- setup-gitignore ---


def test_setup_gitignore(runner, project, git_repo):
    result = runner.invoke(cli, ["setup-gitignore", "proj"])
    assert result.exit_code == 0
    assert "Added" in result.output
    assert ".plans/" in (git_repo / ".gitignore").read_text()

    again = runner.invoke(cli, ["setup-gitignore", "proj"])
    assert "already in .gitignore" in again.output


def test_setup_gitignore_missing_project(runner, initialized):
    result = runner.invoke(cli, ["setup-gitignore", "nope"])
    assert result.exit_code == 1


def test_setup_gitignore_no_root(runner, initialized):
    create_project(get_session(), name="rootless")
    result = runner.invoke(cli, ["setup-gitignore", "rootless"])
    assert result.exit_code == 1
    assert "no project_root" in result.output


# --- delete ---


def test_delete_force(runner, plan):
    result = runner.invoke(cli, ["delete", "proj", "--force"])
    assert result.exit_code == 0
    assert "deleted successfully" in result.output

    gone = runner.invoke(cli, ["list", "--project", "proj"])
    assert gone.exit_code == 1


def test_delete_confirm_no(runner, project):
    result = runner.invoke(cli, ["delete", "proj"], input="n\n")
    assert result.exit_code == 0
    assert "Cancelled" in result.output


def test_delete_confirm_yes(runner, project):
    result = runner.invoke(cli, ["delete", "proj"], input="y\n")
    assert result.exit_code == 0
    assert "deleted successfully" in result.output


def test_delete_missing_project(runner, initialized):
    result = runner.invoke(cli, ["delete", "nope", "--force"])
    assert result.exit_code == 1


# --- sync ---


def _plan_file_content(project_id, plan_file_id, version):
    return f"""---
mcp_plan_file: true
plan_manager_version: '1.0'
project_id: {project_id}
project_name: proj
plan_file_id: {plan_file_id}
plan_name: synced
version: {version}
created_at: '2024-01-01T00:00:00Z'
created_by: user
---

# Synced Plan v{version}
"""


def test_sync_no_projects(runner, initialized):
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0
    assert "No projects found" in result.output


def test_sync_missing_project(runner, initialized):
    result = runner.invoke(cli, ["sync", "--project", "nope"])
    assert result.exit_code == 1


def test_sync_no_plan_directory(runner, project):
    result = runner.invoke(cli, ["sync"])
    assert "Plan directory doesn't exist yet" in result.output


def test_sync_no_project_root(runner, initialized):
    create_project(get_session(), name="rootless")
    result = runner.invoke(cli, ["sync"])
    assert "no project_root configured" in result.output


def test_sync_empty_plan_directory(runner, project, git_repo):
    (git_repo / ".plans").mkdir()
    result = runner.invoke(cli, ["sync"])
    assert "No plan files found" in result.output


def test_sync_dry_run_and_import(runner, project, git_repo):
    plans = git_repo / ".plans"
    plans.mkdir()
    plan_file_id = uuid4()
    (plans / "synced_v1.md").write_text(_plan_file_content(project, plan_file_id, 1))
    (plans / "notes.md").write_text("# just notes, no frontmatter\n")
    (plans / "invalid.md").write_text("---\nmcp_plan_file: true\n---\nmissing fields\n")

    dry = runner.invoke(cli, ["sync", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "DRY RUN MODE" in dry.output
    assert "WOULD IMPORT" in dry.output
    assert "Not an MCP plan file" in dry.output
    assert "Invalid frontmatter" in dry.output
    assert "Run without --dry-run" in dry.output

    real = runner.invoke(cli, ["sync", "--project", "proj"])
    assert real.exit_code == 0, real.output
    assert "OK IMPORTED synced_v1.md" in real.output
    assert "Files imported: 1" in real.output
    assert "Files skipped: 1" in real.output
    assert "Errors: 1" in real.output

    # Re-sync: version already in database
    again = runner.invoke(cli, ["sync"])
    assert "already in database" in again.output

    # A newer version of the same plan gets picked up as an update
    (plans / "synced_v2.md").write_text(_plan_file_content(project, plan_file_id, 2))
    dry2 = runner.invoke(cli, ["sync", "--dry-run"])
    assert "WOULD UPDATE" in dry2.output
    updated = runner.invoke(cli, ["sync"])
    assert "OK UPDATED synced_v2.md" in updated.output
    assert "v1 -> v2" in updated.output


def test_sync_unparseable_plan_id_is_error(runner, project, git_repo):
    plans = git_repo / ".plans"
    plans.mkdir()
    content = _plan_file_content(project, "not-a-uuid", 1)
    (plans / "broken.md").write_text(content)
    result = runner.invoke(cli, ["sync"])
    assert result.exit_code == 0
    assert "ERROR broken.md" in result.output
    assert "Errors: 1" in result.output


# --- start / stop / status ---


def test_start_prints_instructions(runner):
    result = runner.invoke(cli, ["start"])
    assert result.exit_code == 0
    assert "MCP SERVER READY" in result.output


def test_start_already_running(runner, home, monkeypatch):
    (home / "server.pid").write_text("12345")
    monkeypatch.setattr(os, "kill", lambda *a: None)
    result = runner.invoke(cli, ["start"])
    assert "already running" in result.output


def test_start_stale_pid(runner, home, monkeypatch):
    (home / "server.pid").write_text("12345")

    def boom(*a):
        raise OSError("no such process")

    monkeypatch.setattr(os, "kill", boom)
    result = runner.invoke(cli, ["start"])
    assert "MCP SERVER READY" in result.output
    assert not (home / "server.pid").exists()


def test_stop_not_running(runner):
    result = runner.invoke(cli, ["stop"])
    assert result.exit_code == 0
    assert "not running" in result.output


def test_stop_running(runner, home, monkeypatch):
    (home / "server.pid").write_text("12345")
    monkeypatch.setattr(os, "kill", lambda *a: None)
    result = runner.invoke(cli, ["stop"])
    assert "Server stopped" in result.output
    assert not (home / "server.pid").exists()


def test_stop_stale_pid(runner, home, monkeypatch):
    (home / "server.pid").write_text("12345")

    def boom(*a):
        raise OSError("no such process")

    monkeypatch.setattr(os, "kill", boom)
    result = runner.invoke(cli, ["stop"])
    assert "Error stopping server" in result.output
    assert not (home / "server.pid").exists()


def test_status_no_db(runner, claude_config):
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Server Status: Stopped" in result.output
    assert "Database: Not initialized" in result.output
    assert "Not Registered" in result.output


def test_status_with_db_and_registration(runner, home, project, claude_config, monkeypatch):
    ci.register_mcp_server()
    (home / "server.pid").write_text("12345")
    monkeypatch.setattr(os, "kill", lambda *a: None)
    result = runner.invoke(cli, ["status"])
    assert "Server Status: Running" in result.output
    assert "Projects: 1" in result.output
    assert "Registered & Valid" in result.output


def test_status_stale_pid(runner, home, initialized, claude_config, monkeypatch):
    (home / "server.pid").write_text("12345")

    def boom(*a):
        raise OSError("no such process")

    monkeypatch.setattr(os, "kill", boom)
    result = runner.invoke(cli, ["status"])
    assert "Server Status: Stopped" in result.output
    assert not (home / "server.pid").exists()


# --- register / unregister / claude-info ---


def test_register_local(runner, claude_config):
    result = runner.invoke(cli, ["register"])
    assert result.exit_code == 0, result.output
    assert "Next steps" in result.output
    assert "flanner" in json.loads(claude_config.read_text())["mcpServers"]


def test_register_no_config_path(runner, monkeypatch):
    monkeypatch.setattr(ci, "get_claude_config_path", lambda: None)
    result = runner.invoke(cli, ["register"])
    assert result.exit_code == 0
    assert "Could not find Claude Code configuration path" in result.output


def test_register_cloud_requires_url(runner, claude_config):
    result = runner.invoke(cli, ["register", "--type", "cloud"])
    assert result.exit_code == 1


def test_register_cloud(runner, claude_config):
    result = runner.invoke(
        cli, ["register", "--type", "cloud", "--url", "https://mcp.example.com"]
    )
    assert result.exit_code == 0
    config = json.loads(claude_config.read_text())
    assert config["mcpServers"]["flanner"]["url"] == "https://mcp.example.com"


def test_unregister_cancelled(runner, claude_config):
    result = runner.invoke(cli, ["unregister"], input="n\n")
    assert "Cancelled" in result.output


def test_unregister_not_registered(runner, claude_config):
    result = runner.invoke(cli, ["unregister"], input="y\n")
    assert "ERROR" in result.output


def test_unregister_success(runner, claude_config):
    ci.register_mcp_server()
    result = runner.invoke(cli, ["unregister"], input="y\n")
    assert "Unregistered" in result.output


def test_claude_info_not_registered(runner, claude_config):
    result = runner.invoke(cli, ["claude-info"])
    assert result.exit_code == 0
    assert "Not registered" in result.output


def test_claude_info_registered(runner, claude_config):
    ci.register_mcp_server()
    result = runner.invoke(cli, ["claude-info"])
    assert "Current Configuration" in result.output


# --- jira ---


def test_jira_config_valid(runner, project):
    result = runner.invoke(
        cli, ["jira", "config", "proj", "--url", JIRA_URL + "/", "--project-key", "PROJ"]
    )
    assert result.exit_code == 0, result.output
    assert "JIRA configuration updated" in result.output
    assert JIRA_URL in result.output
    assert "PROJ" in result.output


def test_jira_config_invalid_url(runner, project):
    result = runner.invoke(cli, ["jira", "config", "proj", "--url", "not-a-url"])
    assert result.exit_code == 1
    assert "Invalid JIRA URL" in result.output


def test_jira_config_missing_project(runner, initialized):
    result = runner.invoke(cli, ["jira", "config", "nope", "--url", JIRA_URL])
    assert result.exit_code == 1


def test_jira_link_flow(runner, plan):
    runner.invoke(cli, ["jira", "config", "proj", "--url", JIRA_URL])
    result = runner.invoke(
        cli,
        [
            "jira",
            "link",
            "myplan",
            "--issue",
            "proj-1",
            "--project",
            "proj",
            "--type",
            "Epic",
            "--notes",
            "linked from test",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Linked 'myplan' to PROJ-1" in result.output
    assert f"{JIRA_URL}/browse/PROJ-1" in result.output
    assert "Type: Epic" in result.output

    dup = runner.invoke(cli, ["jira", "link", "myplan", "--issue", "PROJ-1", "--project", "proj"])
    assert "already linked" in dup.output


def test_jira_link_invalid_issue_key(runner, plan):
    result = runner.invoke(cli, ["jira", "link", "myplan", "--issue", "123", "--project", "proj"])
    assert result.exit_code == 1
    assert "Invalid JIRA issue key" in result.output


def test_jira_link_missing_plan(runner, project):
    result = runner.invoke(
        cli, ["jira", "link", "ghost", "--issue", "PROJ-1", "--project", "proj"]
    )
    assert result.exit_code == 1
    assert "not found in project" in result.output


def test_jira_link_missing_project(runner, initialized):
    result = runner.invoke(
        cli, ["jira", "link", "myplan", "--issue", "PROJ-1", "--project", "nope"]
    )
    assert result.exit_code == 1


def test_jira_link_from_project_directory(runner, plan, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    result = runner.invoke(cli, ["jira", "link", "myplan", "--issue", "PROJ-9"])
    assert result.exit_code == 0, result.output
    assert "Linked 'myplan' to PROJ-9" in result.output


def test_jira_links_listing(runner, plan):
    create_jira_link(get_session(), plan, "PROJ-1", "Story")

    all_projects = runner.invoke(cli, ["jira", "links"])
    assert all_projects.exit_code == 0
    assert "PROJ-1" in all_projects.output

    one = runner.invoke(cli, ["jira", "links", "--project", "proj"])
    assert "myplan" in one.output
    assert "Story" in one.output


def test_jira_links_none_found(runner, project):
    result = runner.invoke(cli, ["jira", "links", "--project", "proj"])
    assert "No JIRA links found" in result.output


def test_jira_links_no_projects(runner, initialized):
    result = runner.invoke(cli, ["jira", "links"])
    assert "No projects found" in result.output


def test_jira_links_missing_project(runner, initialized):
    result = runner.invoke(cli, ["jira", "links", "--project", "nope"])
    assert result.exit_code == 1


def test_jira_show(runner, plan):
    runner.invoke(cli, ["jira", "config", "proj", "--url", JIRA_URL])
    create_jira_link(get_session(), plan, "PROJ-1", "Epic", "some notes")

    result = runner.invoke(cli, ["jira", "show", "myplan", "--project", "proj"])
    assert result.exit_code == 0
    assert "PROJ-1" in result.output
    assert f"{JIRA_URL}/browse/PROJ-1" in result.output
    assert "some notes" in result.output


def test_jira_show_no_links(runner, plan):
    result = runner.invoke(cli, ["jira", "show", "myplan", "--project", "proj"])
    assert "No JIRA links found" in result.output


def test_jira_show_missing_plan(runner, project):
    result = runner.invoke(cli, ["jira", "show", "ghost", "--project", "proj"])
    assert result.exit_code == 1


def test_jira_show_missing_project(runner, initialized):
    result = runner.invoke(cli, ["jira", "show", "myplan", "--project", "nope"])
    assert result.exit_code == 1


def test_jira_unlink_specific(runner, plan):
    create_jira_link(get_session(), plan, "PROJ-1")
    result = runner.invoke(
        cli, ["jira", "unlink", "myplan", "--issue", "PROJ-1", "--project", "proj"]
    )
    assert result.exit_code == 0
    assert "Unlinked 'myplan' from PROJ-1" in result.output

    missing = runner.invoke(
        cli, ["jira", "unlink", "myplan", "--issue", "PROJ-1", "--project", "proj"]
    )
    assert "not found" in missing.output


def test_jira_unlink_all(runner, plan):
    session = get_session()
    create_jira_link(session, plan, "PROJ-1")
    create_jira_link(session, plan, "PROJ-2")
    result = runner.invoke(cli, ["jira", "unlink", "myplan", "--all", "--project", "proj"])
    assert "Unlinked 2 JIRA issue(s)" in result.output

    none_left = runner.invoke(cli, ["jira", "unlink", "myplan", "--project", "proj"])
    assert "No JIRA links found" in none_left.output


def test_jira_unlink_missing_plan(runner, project):
    result = runner.invoke(cli, ["jira", "unlink", "ghost", "--project", "proj"])
    assert result.exit_code == 1


def test_jira_unlink_missing_project(runner, initialized):
    result = runner.invoke(cli, ["jira", "unlink", "myplan", "--project", "nope"])
    assert result.exit_code == 1


def test_list_project_plans_json(runner, plan):
    result = runner.invoke(cli, ["list", "--project", "proj", "--output", "json"])
    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert rows and rows[0]["name"] == "myplan" and rows[0]["version"] == 1


def test_web_warns_on_non_local_host(runner, initialized, monkeypatch):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    result = runner.invoke(cli, ["web", "--host", "0.0.0.0", "--port", "0"])
    assert result.exit_code == 0
    assert "exposes the web UI beyond localhost" in result.output


def test_web_no_warning_on_localhost(runner, initialized, monkeypatch):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    result = runner.invoke(cli, ["web", "--host", "127.0.0.1", "--port", "0"])
    assert "exposes the web UI" not in result.output
