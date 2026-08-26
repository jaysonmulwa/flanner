"""MCP server tool tests: direct function calls, success and error dicts."""

import os

import pytest

import flanner.database as fdb
from flanner import linear_api
from flanner.database import create_plan_file as db_create_plan_file
from flanner.database import create_project, create_version, get_session
from flanner.server import (
    configure_jira_tool,
    configure_linear_tool,
    configure_project_tool,
    create_plan_file_tool,
    create_project_tool,
    delete_project_tool,
    ensure_database,
    get_jira_config_tool,
    get_jira_links_tool,
    get_linear_config_tool,
    get_linear_links_tool,
    get_plan_config,
    get_plan_file_tool,
    get_plan_history_tool,
    link_plan_to_jira_tool,
    link_plan_to_linear_tool,
    list_jira_links_tool,
    list_linear_links_tool,
    list_plan_files_tool,
    unlink_jira_issue_tool,
    unlink_linear_issue_tool,
)

BAD_UUID = "not-a-uuid"
MISSING_UUID = "00000000-0000-0000-0000-000000000000"
JIRA_URL = "https://x.atlassian.net"


@pytest.fixture
def project(db, git_repo):
    """A project backed by a real git repo. Returns the tool result dict."""
    result = create_project_tool(name="proj", project_root=str(git_repo))
    assert not result.get("error"), result.get("message")
    return result


@pytest.fixture
def plan(project):
    """A v1 plan file in the project."""
    result = create_plan_file_tool(project_id=project["id"], name="myplan", content="# Plan v1\n")
    assert not result.get("error"), result.get("message")
    return result


def _live_sessions():
    import gc

    from sqlalchemy.orm import Session as SASession

    gc.collect()
    return sum(1 for obj in gc.get_objects() if isinstance(obj, SASession))


def test_sessions_do_not_accumulate_across_tool_calls(project):
    """The property the pool choice rests on.

    Tools call `get_session()` and never close it, which is safe only
    because the session is dropped when the tool returns and refcounting
    closes it there. That is an assumption about the interpreter, so it is
    checked here against the real tool path rather than trusted.

    What this would catch is a session stored somewhere that outlives the
    call — a cache, a module global, a closure. That is the change that
    would make NullPool a workaround again instead of a decision.
    """
    list_plan_files_tool(project["id"])  # warm any lazily built state
    before = _live_sessions()

    for _ in range(20):
        list_plan_files_tool(project["id"])

    assert _live_sessions() <= before


def test_create_plan_in_subdirectory(project):
    import os

    result = create_plan_file_tool(
        project_id=project["id"], name="auth/login-flow", content="# Login\n"
    )
    assert not result.get("error"), result.get("message")
    assert result["name"] == "auth/login-flow"  # stored name keeps the subpath
    assert result["file_path"].replace("\\", "/").endswith(".plans/auth/login-flow_v1.md")
    assert os.path.exists(result["file_path"])  # actually nested on disk


def test_create_plan_rejects_path_traversal(project):
    result = create_plan_file_tool(project_id=project["id"], name="../../escape", content="# X\n")
    assert result["error"] is True
    assert "traversal" in result["message"]


# --- config ---


def test_get_plan_config_with_project(project):
    config = get_plan_config(project["id"])
    assert config["project_root"] == project["project_root"]
    assert config["naming_convention"] == "{plan_name}_v{version}.md"


def test_get_plan_config_invalid_uuid(db):
    assert get_plan_config(BAD_UUID)["error"] is True


def test_get_plan_config_missing_project_returns_defaults(db):
    config = get_plan_config(MISSING_UUID)
    assert config["plan_directory"] == ".plans"


# --- project management ---


def test_create_project_not_a_git_repo(db, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = create_project_tool(name="x", project_root=str(plain))
    assert result["error"] is True
    assert "not a valid git repository" in result["message"]


def test_create_project_duplicate_name(project, git_repo):
    result = create_project_tool(name="proj", project_root=str(git_repo))
    assert result["error"] is True
    assert "already exists" in result["message"]


def test_create_project_no_root_no_git(db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = create_project_tool(name="x")
    assert result["error"] is True
    assert "Could not find git repository" in result["message"]


def test_configure_project(project, git_repo):
    result = configure_project_tool(
        project_id=project["id"],
        plan_directory="docs/plans",
        description="new desc",
        auto_gitignore=True,
    )
    assert not result.get("error"), result.get("message")
    assert result["plan_directory"] == "docs/plans"
    assert result["description"] == "new desc"
    # gitignore was rewritten for the new directory
    gitignore = (git_repo / ".gitignore").read_text()
    assert "docs/plans/" in gitignore


def test_configure_project_errors(db):
    assert configure_project_tool(project_id=BAD_UUID)["error"] is True
    assert configure_project_tool(project_id=MISSING_UUID)["error"] is True


def test_delete_project(plan, project):
    result = delete_project_tool(project["id"])
    assert result["success"] is True
    assert result["plan_files_deleted"] == 1
    assert "NOT deleted" in result["message"]


def test_delete_project_errors(db):
    assert delete_project_tool(BAD_UUID)["error"] is True
    assert delete_project_tool(MISSING_UUID)["error"] is True


# --- plan files ---


def test_list_plan_files_invalid_uuid(db):
    result = list_plan_files_tool(BAD_UUID)
    assert result[0]["error"] is True


def test_create_plan_file_errors(db, project):
    assert create_plan_file_tool(project_id=BAD_UUID, name="p", content="c")["error"] is True
    assert create_plan_file_tool(project_id=MISSING_UUID, name="p", content="c")["error"] is True

    # Duplicate plan name in same project
    assert not create_plan_file_tool(project_id=project["id"], name="p", content="c").get("error")
    dup = create_plan_file_tool(project_id=project["id"], name="p", content="c")
    assert dup["error"] is True


def test_create_plan_file_no_project_root(db):
    session = get_session()
    rootless = create_project(session, name="rootless")
    result = create_plan_file_tool(project_id=str(rootless.id), name="p", content="c")
    assert result["error"] is True
    assert "no project_root" in result["message"]


def test_update_plan_file_new_version(plan):
    from flanner.server import update_plan_file_tool

    result = update_plan_file_tool(plan["id"], "# Plan v2\n", notes="second")
    assert result["version"] == 2
    assert os.path.exists(result["file_path"])
    assert result["file_path"].replace("\\", "/").endswith("myplan_v2.md")


def test_update_plan_file_no_changes(plan):
    from flanner.server import update_plan_file_tool

    result = update_plan_file_tool(plan["id"], "# Plan v1\n")
    assert "No changes detected" in result["message"]
    assert result["version"] == 1


def test_update_plan_file_auto_version_off_returns_none(db, project, git_repo):
    from flanner.server import update_plan_file_tool

    session = get_session()
    projects = fdb.list_projects(session)
    pf = db_create_plan_file(session, projects[0].id, "manual", auto_version=False)
    create_version(session, pf.id, 1, str(git_repo / ".plans" / "manual_v1.md"), "hash1")

    result = update_plan_file_tool(str(pf.id), "different content")
    assert result is None


def test_update_plan_file_errors(db, project):
    from flanner.server import update_plan_file_tool

    assert update_plan_file_tool(BAD_UUID, "c")["error"] is True
    assert update_plan_file_tool(MISSING_UUID, "c")["error"] is True

    # Plan exists but has no versions
    session = get_session()
    projects = fdb.list_projects(session)
    pf = db_create_plan_file(session, projects[0].id, "empty")
    result = update_plan_file_tool(str(pf.id), "c")
    assert result["error"] is True
    assert "No versions" in result["message"]


def test_update_plan_file_no_project_root(db, git_repo):
    from flanner.server import update_plan_file_tool

    session = get_session()
    rootless = create_project(session, name="rootless2")
    pf = db_create_plan_file(session, rootless.id, "p")
    create_version(session, pf.id, 1, str(git_repo / "x.md"), "hash1")
    result = update_plan_file_tool(str(pf.id), "new content")
    assert result["error"] is True
    assert "no project_root" in result["message"]


def test_get_plan_file_latest_and_specific(plan):
    from flanner.server import update_plan_file_tool

    update_plan_file_tool(plan["id"], "# Plan v2\n")

    latest = get_plan_file_tool(plan["id"])
    assert latest["version"]["version"] == 2
    assert "# Plan v2" in latest["content"]
    assert latest["frontmatter"]["mcp_plan_file"] is True

    v1 = get_plan_file_tool(plan["id"], version=1)
    assert v1["version"]["version"] == 1
    assert "# Plan v1" in v1["content"]


def test_get_plan_file_errors(db, plan):
    assert get_plan_file_tool(BAD_UUID)["error"] is True
    assert get_plan_file_tool(MISSING_UUID)["error"] is True
    assert get_plan_file_tool(plan["id"], version=99)["error"] is True

    # Version record exists but file removed from disk
    os.remove(plan["file_path"])
    result = get_plan_file_tool(plan["id"])
    assert result["error"] is True
    assert "File not found" in result["message"]


def test_get_plan_history(plan):
    from flanner.server import update_plan_file_tool

    update_plan_file_tool(plan["id"], "# Plan v2\n")
    result = get_plan_history_tool(plan["id"])
    assert result["total_versions"] == 2
    assert [v["version"] for v in result["versions"]] == [2, 1]


def test_get_plan_history_errors(db):
    assert get_plan_history_tool(BAD_UUID)["error"] is True
    assert get_plan_history_tool(MISSING_UUID)["error"] is True


# --- jira tools ---


def test_configure_jira(project):
    result = configure_jira_tool(project["id"], JIRA_URL + "/", jira_project_key="PROJ")
    assert not result.get("error"), result.get("message")
    assert result["jira_url"] == JIRA_URL  # trailing slash normalized
    assert result["jira_project_key"] == "PROJ"

    # Reconfigure updates in place
    result = configure_jira_tool(project["id"], "https://y.atlassian.net")
    assert result["jira_url"] == "https://y.atlassian.net"


def test_configure_jira_errors(db, project):
    assert configure_jira_tool(project["id"], "not-a-url")["error"] is True
    assert configure_jira_tool(BAD_UUID, JIRA_URL)["error"] is True
    assert configure_jira_tool(MISSING_UUID, JIRA_URL)["error"] is True


def test_link_plan_to_jira_without_config(plan):
    result = link_plan_to_jira_tool(plan["id"], "proj-1", issue_type="Epic", notes="n")
    assert not result.get("error"), result.get("message")
    assert result["jira_issue_key"] == "PROJ-1"  # formatted to uppercase
    assert result["jira_url"] is None  # no jira config yet


def test_link_plan_to_jira_with_config_and_duplicate(project, plan):
    configure_jira_tool(project["id"], JIRA_URL)
    result = link_plan_to_jira_tool(plan["id"], "PROJ-2")
    assert result["jira_url"] == f"{JIRA_URL}/browse/PROJ-2"

    dup = link_plan_to_jira_tool(plan["id"], "PROJ-2")
    assert dup["error"] is True
    assert "already linked" in dup["message"]


def test_link_plan_to_jira_errors(db, plan):
    assert link_plan_to_jira_tool(plan["id"], "bad key")["error"] is True
    assert link_plan_to_jira_tool(BAD_UUID, "PROJ-1")["error"] is True
    assert link_plan_to_jira_tool(MISSING_UUID, "PROJ-1")["error"] is True


def test_get_jira_links(project, plan):
    configure_jira_tool(project["id"], JIRA_URL)
    link_plan_to_jira_tool(plan["id"], "PROJ-1")
    result = get_jira_links_tool(plan["id"])
    assert result["total_links"] == 1
    assert result["links"][0]["jira_url"] == f"{JIRA_URL}/browse/PROJ-1"


def test_get_jira_links_errors(db):
    assert get_jira_links_tool(BAD_UUID)["error"] is True
    assert get_jira_links_tool(MISSING_UUID)["error"] is True


def test_list_jira_links(project, plan):
    configure_jira_tool(project["id"], JIRA_URL)
    link_plan_to_jira_tool(plan["id"], "PROJ-1")
    result = list_jira_links_tool(project["id"])
    assert result["total_links"] == 1
    assert result["links"][0]["plan_file_name"] == "myplan"
    assert result["links"][0]["jira_url"] == f"{JIRA_URL}/browse/PROJ-1"


def test_list_jira_links_errors(db):
    assert list_jira_links_tool(BAD_UUID)["error"] is True
    assert list_jira_links_tool(MISSING_UUID)["error"] is True


def test_unlink_jira_issue_specific(plan):
    link_plan_to_jira_tool(plan["id"], "PROJ-1")
    missing = unlink_jira_issue_tool(plan["id"], "PROJ-9")
    assert missing["error"] is True

    result = unlink_jira_issue_tool(plan["id"], "PROJ-1")
    assert result["success"] is True


def test_unlink_jira_issue_all(plan):
    link_plan_to_jira_tool(plan["id"], "PROJ-1")
    link_plan_to_jira_tool(plan["id"], "PROJ-2")
    result = unlink_jira_issue_tool(plan["id"])
    assert result["success"] is True
    assert result["count"] == 2


def test_unlink_jira_issue_errors(db):
    assert unlink_jira_issue_tool(BAD_UUID)["error"] is True
    assert unlink_jira_issue_tool(MISSING_UUID)["error"] is True


def test_get_jira_config_tool(project):
    result = get_jira_config_tool(project["id"])
    assert result["configured"] is False

    configure_jira_tool(project["id"], JIRA_URL)
    result = get_jira_config_tool(project["id"])
    assert result["configured"] is True
    assert result["jira_url"] == JIRA_URL


def test_get_jira_config_tool_errors(db):
    assert get_jira_config_tool(BAD_UUID)["error"] is True
    assert get_jira_config_tool(MISSING_UUID)["error"] is True


def test_ensure_database_initializes_when_missing(tmp_path, monkeypatch):
    home = tmp_path / "fresh-home"
    monkeypatch.setenv("FLANNER_HOME", str(home))
    monkeypatch.delenv("FLANNER_DB_PATH", raising=False)
    monkeypatch.setattr(fdb, "_SessionLocal", None)
    ensure_database()
    assert (home / "data.db").exists()


# --- linear tools ---


@pytest.fixture
def no_linear_key(monkeypatch):
    """Ensure the Tier 1 (link-only) path runs regardless of the real env."""
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)


def test_configure_linear(project):
    result = configure_linear_tool(project["id"], "https://linear.app/Acme/")
    assert result["workspace"] == "acme"  # slug extracted + lowercased


def test_configure_linear_errors(db, project):
    assert configure_linear_tool(project["id"], "Bad Space")["error"] is True
    assert configure_linear_tool(BAD_UUID, "acme")["error"] is True
    assert configure_linear_tool(MISSING_UUID, "acme")["error"] is True


def test_link_linear_without_config(plan, no_linear_key):
    result = link_plan_to_linear_tool(plan["id"], "eng-1", notes="n")
    assert result["linear_issue_id"] == "ENG-1"  # formatted
    assert result["linear_url"] is None  # no workspace configured
    assert result["issue_title"] is None  # no API key -> no enrichment


def test_link_linear_with_config_and_duplicate(project, plan, no_linear_key):
    configure_linear_tool(project["id"], "acme")
    result = link_plan_to_linear_tool(plan["id"], "ENG-2")
    assert result["linear_url"] == "https://linear.app/acme/issue/ENG-2"
    dup = link_plan_to_linear_tool(plan["id"], "ENG-2")
    assert dup["error"] is True


def test_link_linear_errors(db, plan, no_linear_key):
    assert link_plan_to_linear_tool(plan["id"], "bad id")["error"] is True
    assert link_plan_to_linear_tool(BAD_UUID, "ENG-1")["error"] is True
    assert link_plan_to_linear_tool(MISSING_UUID, "ENG-1")["error"] is True


def test_link_linear_verify_enriches(plan, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    monkeypatch.setattr(
        linear_api,
        "fetch_issue_by_identifier",
        lambda *a, **k: {"id": "u1", "title": "Do it", "state": "In Progress"},
    )
    result = link_plan_to_linear_tool(plan["id"], "ENG-7")
    assert result["issue_title"] == "Do it"
    assert result["issue_state"] == "In Progress"


def test_link_linear_verify_missing_issue(plan, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "key")
    monkeypatch.setattr(linear_api, "fetch_issue_by_identifier", lambda *a, **k: None)
    result = link_plan_to_linear_tool(plan["id"], "ENG-404")
    assert result["error"] is True
    assert "not found" in result["message"]


def test_link_linear_verify_network_error_falls_back(plan, monkeypatch):
    from flanner.exceptions import LinearError

    monkeypatch.setenv("LINEAR_API_KEY", "key")

    def boom(*a, **k):
        raise LinearError("offline")

    monkeypatch.setattr(linear_api, "fetch_issue_by_identifier", boom)
    result = link_plan_to_linear_tool(plan["id"], "ENG-8")
    assert result["linear_issue_id"] == "ENG-8"
    assert "warning" in result  # linked without verification


def test_get_and_list_linear_links(project, plan, no_linear_key):
    configure_linear_tool(project["id"], "acme")
    link_plan_to_linear_tool(plan["id"], "ENG-1")
    got = get_linear_links_tool(plan["id"])
    assert got["links"][0]["linear_url"] == "https://linear.app/acme/issue/ENG-1"
    listed = list_linear_links_tool(project["id"])
    assert listed["total_links"] == 1


def test_unlink_linear(plan, no_linear_key):
    link_plan_to_linear_tool(plan["id"], "ENG-1")
    link_plan_to_linear_tool(plan["id"], "ENG-2")
    assert unlink_linear_issue_tool(plan["id"], "ENG-9")["error"] is True
    assert unlink_linear_issue_tool(plan["id"], "ENG-1")["success"] is True
    assert unlink_linear_issue_tool(plan["id"])["count"] == 1


def test_get_linear_config_tool(project, no_linear_key):
    assert get_linear_config_tool(project["id"])["configured"] is False
    configure_linear_tool(project["id"], "acme")
    result = get_linear_config_tool(project["id"])
    assert result["configured"] is True
    assert result["workspace"] == "acme"


# --- initialize_project_tool (adopt a repo) ----------------------------------


def test_initialize_project_tool_adopts_repo(db, git_repo):
    from flanner.server import initialize_project_tool

    result = initialize_project_tool(project_root=str(git_repo), name="adopted")
    assert not result.get("error"), result.get("message")
    assert result["created"] is True
    assert result["project_name"] == "adopted"
    assert any("skill" in i for i in result["installed"])
    assert (git_repo / ".mcp.json").exists()
    assert (git_repo / ".claude" / "settings.json").exists()

    # Idempotent: re-adopting an existing repo creates nothing new.
    again = initialize_project_tool(project_root=str(git_repo))
    assert again["created"] is False
    assert again["installed"] == []


def test_initialize_project_tool_rejects_non_git(db, tmp_path):
    from flanner.server import initialize_project_tool

    result = initialize_project_tool(project_root=str(tmp_path))  # exists but not a git repo
    assert result["error"] is True
