"""CLI tests via click.testing.CliRunner.

All invocations run with FLANNER_HOME pointed at a per-test temp directory so
nothing touches the real ~/.flanner. Claude-config-writing commands monkeypatch
get_claude_config_path so the real Claude config is never written.
"""

import json
import os
from pathlib import Path
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
    """The one flanner home for this test.

    Reuses the isolated FLANNER_HOME conftest already set, so the CLI and
    any fixture that calls into flanner directly share a device identity.
    Pointing the runner somewhere else would quietly simulate two devices,
    and signature checks would then fail for the wrong reason.
    """
    h = Path(os.environ["FLANNER_HOME"])
    h.mkdir(parents=True, exist_ok=True)
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
        ["linear", "config", "p", "--workspace", "acme"],
        ["linear", "link", "p", "--issue", "ENG-1"],
        ["linear", "unlink", "p"],
        ["linear", "links"],
        ["linear", "show", "p"],
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
    # Claude Code CLI registration (.mcp.json) is written even with --skip-claude,
    # which only skips the Claude Desktop config.
    mcp_json = json.loads((git_repo / ".mcp.json").read_text())
    assert mcp_json["mcpServers"]["flanner"]["command"] == "flanner-mcp"


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
    assert "No plans in" in result.output


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
    assert "Dry run" in dry.output
    assert "WOULD IMPORT" in dry.output
    assert "Not an MCP plan file" in dry.output
    assert "Invalid frontmatter" in dry.output
    assert "Run flanner sync to apply" in dry.output

    real = runner.invoke(cli, ["sync", "--project", "proj"])
    assert real.exit_code == 0, real.output
    assert "OK IMPORTED synced_v1.md" in real.output
    assert "1 imported" in real.output
    assert "1 skipped" in real.output
    assert "1 error" in real.output

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
    assert "1 error" in result.output


# --- start / stop / status ---


def test_start_prints_instructions(runner):
    result = runner.invoke(cli, ["start"])
    assert result.exit_code == 0
    assert "MCP server ready" in result.output


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
    assert "MCP server ready" in result.output
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
    assert "stopped" in result.output
    assert "not initialized" in result.output
    assert "not registered" in result.output


def test_status_with_db_and_registration(runner, home, project, claude_config, monkeypatch):
    ci.register_mcp_server()
    (home / "server.pid").write_text("12345")
    monkeypatch.setattr(os, "kill", lambda *a: None)
    result = runner.invoke(cli, ["status"])
    assert "running" in result.output
    assert "1 project" in result.output
    assert "registered" in result.output


def test_status_stale_pid(runner, home, initialized, claude_config, monkeypatch):
    (home / "server.pid").write_text("12345")

    def boom(*a):
        raise OSError("no such process")

    monkeypatch.setattr(os, "kill", boom)
    result = runner.invoke(cli, ["status"])
    assert "stopped" in result.output
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
    assert "Current configuration" in result.output


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


def test_web_port_in_use_is_graceful(runner, initialized):
    import socket

    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        result = runner.invoke(cli, ["web", "--host", "127.0.0.1", "--port", str(port)])
    assert result.exit_code == 1
    assert "already in use" in result.output
    assert f"flanner web --port {port + 1}" in result.output  # actionable next step


# --- linear ---


def test_linear_auth_no_key(runner, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    result = runner.invoke(cli, ["linear", "auth"])
    assert result.exit_code == 1
    assert "LINEAR_API_KEY is not set" in result.output
    assert "linear.app/settings/api" in result.output


def test_linear_auth_valid(runner, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    from flanner import linear_api

    monkeypatch.setattr(
        linear_api, "fetch_viewer", lambda *a, **k: {"name": "Ada", "email": "ada@x.com"}
    )
    result = runner.invoke(cli, ["linear", "auth"])
    assert result.exit_code == 0, result.output
    assert "Authenticated with Linear as Ada" in result.output
    assert "LINEAR_API_KEY" in result.output  # prints the MCP env snippet


def test_linear_auth_rejected(runner, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "bad")
    from flanner import linear_api
    from flanner.exceptions import LinearError

    def boom(*a, **k):
        raise LinearError("Authentication required")

    monkeypatch.setattr(linear_api, "fetch_viewer", boom)
    result = runner.invoke(cli, ["linear", "auth"])
    assert result.exit_code == 1
    assert "rejected the key" in result.output


def test_linear_config_valid(runner, project):
    result = runner.invoke(
        cli, ["linear", "config", "proj", "--workspace", "https://linear.app/Acme"]
    )
    assert result.exit_code == 0, result.output
    assert "acme" in result.output


def test_linear_config_invalid_workspace(runner, project):
    result = runner.invoke(cli, ["linear", "config", "proj", "--workspace", "Bad Space"])
    assert result.exit_code == 1
    assert "Invalid Linear workspace" in result.output


def test_linear_link_flow(runner, plan, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    runner.invoke(cli, ["linear", "config", "proj", "--workspace", "acme"])
    result = runner.invoke(
        cli, ["linear", "link", "myplan", "--issue", "eng-1", "--project", "proj"]
    )
    assert result.exit_code == 0, result.output
    assert "Linked 'myplan' to ENG-1" in result.output
    assert "https://linear.app/acme/issue/ENG-1" in result.output

    dup = runner.invoke(cli, ["linear", "link", "myplan", "--issue", "ENG-1", "--project", "proj"])
    assert "already linked" in dup.output


def test_linear_link_invalid_issue(runner, plan, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    result = runner.invoke(
        cli, ["linear", "link", "myplan", "--issue", "123", "--project", "proj"]
    )
    assert result.exit_code == 1
    assert "Invalid Linear issue id" in result.output


def test_linear_link_verify_enriches(runner, plan, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    from flanner import linear_api

    monkeypatch.setattr(
        linear_api,
        "fetch_issue_by_identifier",
        lambda *a, **k: {"id": "u1", "title": "Ship it", "state": "Todo"},
    )
    result = runner.invoke(
        cli, ["linear", "link", "myplan", "--issue", "ENG-5", "--project", "proj"]
    )
    assert result.exit_code == 0, result.output
    assert "[Todo] Ship it" in result.output


def test_linear_link_verify_missing_issue(runner, plan, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    from flanner import linear_api

    monkeypatch.setattr(linear_api, "fetch_issue_by_identifier", lambda *a, **k: None)
    result = runner.invoke(
        cli, ["linear", "link", "myplan", "--issue", "ENG-404", "--project", "proj"]
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_linear_links_and_show(runner, plan, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    runner.invoke(cli, ["linear", "config", "proj", "--workspace", "acme"])
    runner.invoke(cli, ["linear", "link", "myplan", "--issue", "ENG-1", "--project", "proj"])

    listed = runner.invoke(cli, ["linear", "links", "--project", "proj"])
    assert "ENG-1" in listed.output
    shown = runner.invoke(cli, ["linear", "show", "myplan", "--project", "proj"])
    assert "ENG-1" in shown.output
    assert "https://linear.app/acme/issue/ENG-1" in shown.output


def test_linear_unlink(runner, plan, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    runner.invoke(cli, ["linear", "link", "myplan", "--issue", "ENG-1", "--project", "proj"])
    result = runner.invoke(
        cli, ["linear", "unlink", "myplan", "--issue", "ENG-1", "--project", "proj"]
    )
    assert result.exit_code == 0
    assert "Unlinked 'myplan' from ENG-1" in result.output


def test_linear_refresh_requires_key(runner, plan, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    result = runner.invoke(cli, ["linear", "refresh", "myplan", "--project", "proj"])
    assert result.exit_code == 1
    assert "LINEAR_API_KEY is not set" in result.output


def test_linear_refresh_updates_cache(runner, plan, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    from flanner import linear_api

    # Link first (no verify) so there is a row to refresh.
    monkeypatch.setattr(linear_api, "fetch_issue_by_identifier", lambda *a, **k: None)
    runner.invoke(
        cli, ["linear", "link", "myplan", "--issue", "ENG-1", "--no-verify", "--project", "proj"]
    )
    monkeypatch.setattr(
        linear_api,
        "fetch_issue_by_identifier",
        lambda *a, **k: {"id": "u1", "title": "Now done", "state": "Done"},
    )
    result = runner.invoke(cli, ["linear", "refresh", "myplan", "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert "[Done] Now done" in result.output


def test_setup_registers_and_writes_global_nudge(runner, tmp_path, monkeypatch, claude_config):
    import shutil
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)  # pretend the claude CLI is absent
    result = runner.invoke(cli, ["setup"])
    assert result.exit_code == 0, result.output
    nudge = tmp_path / ".claude" / "CLAUDE.md"
    assert nudge.exists()
    assert "initialize_project_tool" in nudge.read_text(encoding="utf-8")
    assert "Claude Code CLI not found" in result.output  # took the no-claude branch


# --- doctor: catalog/disk reconciliation ---


@pytest.fixture
def written_plan(project, git_repo):
    """A plan created through the real write path, so its file exists on disk."""
    from flanner.database import get_project
    from flanner.plan_ops import create_plan

    session = get_session()
    proj = get_project(session, project)
    plan_file, _ = create_plan(
        session, project=proj, name="written", content="# body\n", created_by="test"
    )
    session.commit()
    return plan_file.id


def test_doctor_clean_project(runner, written_plan, git_repo):
    result = runner.invoke(cli, ["doctor", "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert "agree" in result.output


def test_doctor_reports_missing_file(runner, plan):
    # The `plan` fixture records a version whose file was never written.
    result = runner.invoke(cli, ["doctor", "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert "missing_file" in result.output


def test_doctor_json_output(runner, plan):
    result = runner.invoke(cli, ["doctor", "--project", "proj", "--output", "json"])
    assert result.exit_code == 0, result.output
    findings = json.loads(result.output)
    assert findings[0]["kind"] == "missing_file"
    assert findings[0]["repairable"] is False


def test_doctor_repair_adopts_orphan(runner, written_plan, git_repo):
    original = (git_repo / ".plans" / "written_v1.md").read_text(encoding="utf-8")
    orphan = git_repo / ".plans" / "written_v2.md"
    orphan.write_text(original.replace("version: 1", "version: 2"), encoding="utf-8")

    result = runner.invoke(cli, ["doctor", "--project", "proj"])
    assert "orphan_file" in result.output
    assert "--repair" in result.output  # suggests the fix

    repaired = runner.invoke(cli, ["doctor", "--project", "proj", "--repair"])
    assert repaired.exit_code == 0, repaired.output
    assert "Repaired 1" in repaired.output

    after = runner.invoke(cli, ["doctor", "--project", "proj"])
    assert "agree" in after.output


def test_doctor_unknown_project_exits_1(runner, initialized):
    """The name given back, not generic advice to do what was just done."""
    result = runner.invoke(cli, ["doctor", "--project", "nope"])
    assert result.exit_code == 1
    assert "No project named 'nope'" in result.output
    assert "pass --project" not in result.output


# --- single-writer discipline: CLI writes go through the daemon ---


def test_cli_write_routes_through_the_daemon(runner, plan, monkeypatch):
    """With a daemon up, a CLI write is forwarded rather than applied directly."""
    from flanner import services

    seen = []

    def fake_call(path, payload):
        seen.append((path, payload["op"]))
        return {"result": {"success": True, "message": "ok", "count": 2}}

    monkeypatch.setattr(services.ipc, "call_daemon", fake_call)
    result = runner.invoke(cli, ["jira", "unlink", "myplan", "--all", "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert seen == [("/ipc/call", "unlink_jira_issue")]
    assert "Unlinked 2 JIRA issue(s)" in result.output


def test_cli_write_applies_locally_without_a_daemon(runner, project, git_repo):
    """No daemon: the write still happens exactly once, in-process."""
    from flanner.database import get_jira_config, get_session

    result = runner.invoke(
        cli, ["jira", "config", "proj", "--url", JIRA_URL, "--project-key", "PROJ"]
    )
    assert result.exit_code == 0, result.output
    assert get_jira_config(get_session(), project).jira_url == JIRA_URL


def test_cli_links_are_attributed_to_the_user_not_the_agent(runner, plan):
    """Routing through the shared service must not relabel human actions."""
    from flanner.database import get_jira_links, get_session

    result = runner.invoke(
        cli, ["jira", "link", "myplan", "--issue", "PROJ-7", "--project", "proj"]
    )
    assert result.exit_code == 0, result.output
    links = get_jira_links(get_session(), plan)
    assert links[0].created_by == "user"


# --- history, diff and why --------------------------------------------------


@pytest.fixture
def revised_plan(project, git_repo):
    """A plan with three versions, so history and diff have something to say."""
    from flanner.database import get_project
    from flanner.plan_ops import create_plan, record_new_version

    session = get_session()
    proj = get_project(session, project)
    plan_file, _ = create_plan(
        session,
        project=proj,
        name="revised",
        content="# Title\n\nfirst line\n",
        created_by="claude",
    )
    record_new_version(
        session,
        project=proj,
        plan_file=plan_file,
        content="# Title\n\nfirst line\nsecond line\n",
        created_by="jayson",
        notes="added a line",
    )
    record_new_version(
        session,
        project=proj,
        plan_file=plan_file,
        content="# Title\n\nreplaced line\nsecond line\n",
        created_by="claude",
        notes="reworded",
    )
    session.commit()
    return plan_file


def test_history_lists_every_version_newest_first(runner, revised_plan):
    result = runner.invoke(cli, ["history", "revised", "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert result.output.index("v3") < result.output.index("v1")
    assert "reworded" in result.output
    assert "added a line" in result.output
    assert "3 versions" in result.output


def test_history_counts_what_changed(runner, revised_plan):
    """The churn column is computed from the files; it is stored nowhere."""
    result = runner.invoke(cli, ["history", "revised", "--project", "proj"])
    assert "+1" in result.output  # v2 added one line
    assert "+3" in result.output  # v1 counts as all-added


def test_history_limit(runner, revised_plan):
    result = runner.invoke(cli, ["history", "revised", "--project", "proj", "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert "v3" in result.output
    assert "v1" not in result.output.split("3 versions")[0]


def test_diff_defaults_to_the_last_two_versions(runner, revised_plan):
    result = runner.invoke(cli, ["diff", "revised", "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert "- first line" in result.output
    assert "+ replaced line" in result.output


def test_diff_takes_a_version_with_or_without_the_v(runner, revised_plan):
    bare = runner.invoke(cli, ["diff", "revised", "1", "3", "--project", "proj"])
    prefixed = runner.invoke(cli, ["diff", "revised", "v1", "v3", "--project", "proj"])
    assert bare.exit_code == 0, bare.output
    assert prefixed.exit_code == 0, prefixed.output
    assert "+ second line" in bare.output
    assert bare.output == prefixed.output


def test_diff_labels_hunks_with_the_heading_above_them(runner, revised_plan):
    """Line numbers are true and useless; the section is what orients you."""
    result = runner.invoke(cli, ["diff", "revised", "v1", "v3", "--project", "proj"])
    assert "@@ # Title @@" in result.output


def test_diff_rejects_a_version_that_does_not_exist(runner, revised_plan):
    result = runner.invoke(cli, ["diff", "revised", "v1", "v99", "--project", "proj"])
    assert result.exit_code == 1
    assert "v99" in result.output


def test_diff_says_so_when_nothing_differs(runner, written_plan):
    result = runner.invoke(cli, ["diff", "written", "v1", "v1", "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert "identical" in result.output


def test_why_reports_the_same_evidence_as_freshness(runner, written_plan, git_repo):
    why = runner.invoke(cli, ["why", "written", "--project", "proj"])
    freshness = runner.invoke(cli, ["freshness", "written", "--project", "proj"])
    assert why.exit_code == 0, why.output
    assert why.output == freshness.output


# --- pack and import ----------------------------------------------------------


def test_pack_writes_one_self_contained_file(runner, written_plan, git_repo, tmp_path):
    out = tmp_path / "packet.html"
    result = runner.invoke(
        cli, ["review", "pack", "written", "--project", "proj", "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "@font-face" in html
    assert 'class="toc"' in html
    assert 'id="notes"' in html
    assert "no team review" in result.output


def test_pack_can_leave_the_fonts_out(runner, written_plan, git_repo, tmp_path):
    fat = tmp_path / "fat.html"
    thin = tmp_path / "thin.html"
    runner.invoke(cli, ["review", "pack", "written", "--project", "proj", "--output", str(fat)])
    runner.invoke(
        cli,
        ["review", "pack", "written", "--project", "proj", "--output", str(thin), "--no-fonts"],
    )
    assert thin.stat().st_size < fat.stat().st_size


def test_pack_refuses_a_version_that_does_not_exist(runner, written_plan, git_repo):
    result = runner.invoke(
        cli, ["review", "pack", "written", "--project", "proj", "--version", "99"]
    )
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_deciding_in_a_solo_project_says_the_decision_binds_nobody(runner, written_plan):
    """`review status` said this already; the moment of deciding did not.

    Somebody can approve a plan without ever running status, and "Recorded
    approve" on its own reads exactly like an authorization.
    """
    runner.invoke(cli, ["review", "propose", "written", "--project", "proj"])

    result = runner.invoke(cli, ["review", "decide", "written", "approve", "--project", "proj"])

    assert result.exit_code == 0, result.output
    assert "Recorded approve" in result.output
    assert "advisory" in result.output
    # The why, not just the word. Taken from the resolution, so this also
    # fails if the two call sites ever start wording it differently.
    assert "has not joined a workspace" in result.output


def test_whoami_says_what_this_device_holds_and_that_it_keeps_it(runner, written_plan):
    """The Settings page has said this since retirement landed; the CLI had not.

    A CLI-only user is the common case, so "keep everything" was a decision
    they lived with and could not see.
    """
    result = runner.invoke(cli, ["whoami"])

    assert result.exit_code == 0, result.output
    assert "Holds" in result.output
    assert "artifacts" in result.output
    assert "never pruned" in result.output


def test_retiring_with_yes_still_says_nothing_was_erased(runner, written_plan):
    """--yes skips the prompt, which was the only place that said this.

    A script is exactly where somebody would assume the command deleted
    something, and it is the one context that never sees the warning.
    """
    result = runner.invoke(cli, ["retire", "written", "--project", "proj", "--yes"])

    assert result.exit_code == 0, result.output
    assert "retired" in result.output
    assert "Nothing was erased" in result.output


def _review_file(tmp_path, plan="written", version=1, notes=None):
    body = {
        "packet": {"plan": plan, "version": version, "project": "proj"},
        "reviewer": "Dana at Acme",
        "notes": notes
        if notes is not None
        else [{"quote": "body", "section": "", "occurrence": 0, "body": "Why this?"}],
    }
    path = tmp_path / "review.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_import_records_the_notes(runner, written_plan, git_repo, tmp_path):
    result = runner.invoke(
        cli, ["review", "import", str(_review_file(tmp_path)), "--project", "proj"]
    )
    assert result.exit_code == 0, result.output
    assert "Recorded 1 note from Dana at Acme" in result.output
    assert "Why this?" in result.output


def test_import_says_the_notes_are_unverified(runner, written_plan, git_repo, tmp_path):
    """The reviewer has no device key. Presenting their notes as though they
    were a teammate's signed comment would be the one unforgivable bug."""
    result = runner.invoke(
        cli, ["review", "import", str(_review_file(tmp_path)), "--project", "proj"]
    )
    assert "Unverified" in result.output
    assert "no device key" in result.output


def test_imported_review_is_stored_as_its_own_artifact_type(
    runner, written_plan, git_repo, tmp_path
):
    """A distinct type is what stops anything downstream mistaking outside
    review for a signed teammate comment."""
    from flanner.artifacts import REVIEW_EXTERNAL
    from flanner.assurance import load_external_reviews

    runner.invoke(cli, ["review", "import", str(_review_file(tmp_path)), "--project", "proj"])
    external = load_external_reviews(get_session(), str(written_plan))
    assert all(e.artifact.artifact_type == REVIEW_EXTERNAL for e in external)
    assert len(external) == 1
    assert external[0].payload["verified"] is False
    assert external[0].payload["reviewer"] == "Dana at Acme"
    assert external[0].payload["notes"][0]["body"] == "Why this?"


def test_import_works_on_a_plan_that_was_never_signed(runner, written_plan, git_repo, tmp_path):
    """A local plan that has never joined a workspace has no signed
    artifact. Refusing there would fail exactly the people most likely to
    be sending plans to outsiders."""
    result = runner.invoke(
        cli, ["review", "import", str(_review_file(tmp_path)), "--project", "proj"]
    )
    assert result.exit_code == 0, result.output


def test_import_warns_when_the_plan_has_moved_on(runner, written_plan, git_repo, tmp_path):
    path = _review_file(tmp_path, version=1)
    from flanner.database import get_project_by_name
    from flanner.plan_ops import record_new_version

    session = get_session()
    proj = get_project_by_name(session, "proj")
    assert proj is not None
    plan = next(p for p in proj.plan_files if p.name == "written")
    record_new_version(
        session,
        project=proj,
        plan_file=plan,
        content="# body\n\nnow with more\n",
        created_by="test",
        notes="a later revision",
    )
    session.commit()

    result = runner.invoke(cli, ["review", "import", str(path), "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert "written against v1" in result.output.lower()


def test_import_rejects_a_file_that_names_no_plan(runner, initialized, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"notes": [{"body": "x"}]}), encoding="utf-8")
    result = runner.invoke(cli, ["review", "import", str(path)])
    assert result.exit_code == 1
    assert "which plan" in result.output


def test_import_of_an_empty_review_changes_nothing(runner, written_plan, git_repo, tmp_path):
    path = _review_file(tmp_path, notes=[])
    result = runner.invoke(cli, ["review", "import", str(path), "--project", "proj"])
    assert result.exit_code == 0, result.output
    assert "no notes" in result.output
