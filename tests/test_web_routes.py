"""Web interface tests: pages, API endpoints, and error statuses."""

import pytest
from fastapi.testclient import TestClient

from flanner.web import app, markdown_filter

BAD_UUID = "not-a-uuid"
MISSING_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def client(db):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def project_id(client, git_repo):
    response = client.post(
        "/projects/new",
        data={"name": "webproj", "project_root": str(git_repo), "plan_directory": ".plans"},
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


@pytest.fixture
def plan_id(client, project_id):
    response = client.post(
        f"/projects/{project_id}/plans/new",
        data={"name": "webplan", "description": "d", "content": "# Web Plan v1\n"},
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def test_markdown_filter():
    assert markdown_filter(None) == ""
    assert markdown_filter("") == ""
    assert "<h1" in markdown_filter("# Title")


# --- project pages ---


def test_dashboard_with_activity(client, plan_id):
    response = client.get("/")
    assert response.status_code == 200
    assert "webplan" in response.text


def test_create_project_invalid_git_root(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    response = client.post("/projects/new", data={"name": "x", "project_root": str(plain)})
    assert response.status_code == 200
    assert "not a valid git repository" in response.text


def test_create_project_no_root_no_git(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = client.post("/projects/new", data={"name": "x", "project_root": ""})
    assert response.status_code == 200
    assert "Could not find git repository" in response.text


def test_create_project_duplicate_name(client, project_id, git_repo):
    response = client.post(
        "/projects/new", data={"name": "webproj", "project_root": str(git_repo)}
    )
    assert response.status_code == 200
    assert "already exists" in response.text


def test_project_detail(client, project_id):
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 200
    assert "webproj" in response.text


def test_project_detail_errors(client):
    assert client.get(f"/projects/{BAD_UUID}").status_code == 400
    assert client.get(f"/projects/{MISSING_UUID}").status_code == 404


def test_delete_project_post(client, project_id):
    response = client.post(f"/projects/{project_id}/delete")
    assert response.status_code == 303
    assert client.get(f"/projects/{project_id}").status_code == 404


def test_delete_project_post_errors(client):
    assert client.post(f"/projects/{BAD_UUID}/delete").status_code == 400
    assert client.post(f"/projects/{MISSING_UUID}/delete").status_code == 404


# --- plan pages ---


def test_new_plan_form(client, project_id):
    assert client.get(f"/projects/{project_id}/plans/new").status_code == 200
    assert client.get(f"/projects/{BAD_UUID}/plans/new").status_code == 400
    assert client.get(f"/projects/{MISSING_UUID}/plans/new").status_code == 404


def test_create_plan_errors(client, project_id, plan_id):
    # Duplicate plan name: re-renders form with error
    response = client.post(
        f"/projects/{project_id}/plans/new", data={"name": "webplan", "content": "x"}
    )
    assert response.status_code == 200
    assert "already exists" in response.text

    assert (
        client.post(f"/projects/{BAD_UUID}/plans/new", data={"name": "p", "content": "c"})
    ).status_code == 400
    assert (
        client.post(f"/projects/{MISSING_UUID}/plans/new", data={"name": "p", "content": "c"})
    ).status_code == 404


def test_create_plan_no_project_root(client, db):
    from flanner.database import create_project, get_session

    rootless = create_project(get_session(), name="rootless-web")
    response = client.post(
        f"/projects/{rootless.id}/plans/new", data={"name": "p", "content": "c"}
    )
    assert response.status_code == 400


def test_plan_view_and_versions(client, plan_id):
    # Edit to create version 2
    response = client.post(
        f"/plans/{plan_id}/edit", data={"content": "# Web Plan v2\n", "notes": "n"}
    )
    assert response.status_code == 303

    latest = client.get(f"/plans/{plan_id}")
    assert latest.status_code == 200
    assert "Web Plan v2" in latest.text

    v1 = client.get(f"/plans/{plan_id}?version=1")
    assert v1.status_code == 200
    assert "Web Plan v1" in v1.text

    assert client.get(f"/plans/{plan_id}?version=99").status_code == 404


def test_plan_view_errors(client, plan_id):
    assert client.get(f"/plans/{BAD_UUID}").status_code == 400
    assert client.get(f"/plans/{MISSING_UUID}").status_code == 404


def test_plan_view_file_missing_on_disk(client, plan_id, git_repo):
    (git_repo / ".plans" / "webplan_v1.md").unlink()
    assert client.get(f"/plans/{plan_id}").status_code == 404


def test_plan_edit_page(client, plan_id):
    response = client.get(f"/plans/{plan_id}/edit")
    assert response.status_code == 200
    assert "Web Plan v1" in response.text

    assert client.get(f"/plans/{BAD_UUID}/edit").status_code == 400
    assert client.get(f"/plans/{MISSING_UUID}/edit").status_code == 404


def test_plan_update_no_changes(client, plan_id):
    response = client.post(f"/plans/{plan_id}/edit", data={"content": "# Web Plan v1\n"})
    assert response.status_code == 303
    assert "no_changes" in response.headers["location"]


def test_plan_update_errors(client):
    assert client.post(f"/plans/{BAD_UUID}/edit", data={"content": "c"}).status_code == 400
    assert client.post(f"/plans/{MISSING_UUID}/edit", data={"content": "c"}).status_code == 404


def test_plan_history(client, plan_id):
    response = client.get(f"/plans/{plan_id}/history")
    assert response.status_code == 200

    assert client.get(f"/plans/{BAD_UUID}/history").status_code == 400
    assert client.get(f"/plans/{MISSING_UUID}/history").status_code == 404


# --- API endpoints ---


def test_api_projects_and_plans(client, project_id, plan_id):
    projects = client.get("/api/projects").json()
    assert any(p["id"] == project_id for p in projects)

    plans = client.get(f"/api/projects/{project_id}/plans").json()
    assert plans[0]["name"] == "webplan"

    assert client.get(f"/api/projects/{BAD_UUID}/plans").status_code == 400


def test_api_get_plan(client, plan_id):
    data = client.get(f"/api/plans/{plan_id}").json()
    assert data["plan_file"]["name"] == "webplan"
    assert "# Web Plan v1" in data["content"]
    assert data["frontmatter"]["mcp_plan_file"] is True

    versioned = client.get(f"/api/plans/{plan_id}?version=1").json()
    assert versioned["version"]["version"] == 1


def test_api_get_plan_errors(client, plan_id, git_repo):
    assert client.get(f"/api/plans/{BAD_UUID}").status_code == 400
    assert client.get(f"/api/plans/{MISSING_UUID}").status_code == 404
    assert client.get(f"/api/plans/{plan_id}?version=99").status_code == 404

    (git_repo / ".plans" / "webplan_v1.md").unlink()
    assert client.get(f"/api/plans/{plan_id}").status_code == 404


def test_api_delete_project(client, project_id):
    result = client.delete(f"/api/projects/{project_id}").json()
    assert result["success"] is True

    assert client.delete(f"/api/projects/{BAD_UUID}").status_code == 400
    assert client.delete(f"/api/projects/{MISSING_UUID}").status_code == 404


# --- linear surfacing in the web UI ---


def test_plan_view_shows_linear_panel(client, plan_id, project_id):
    from uuid import UUID

    from flanner.database import create_linear_config, create_linear_link, get_session

    session = get_session()
    create_linear_config(session, UUID(project_id), "acme")
    create_linear_link(
        session, UUID(plan_id), "ENG-42", issue_title="Do it", issue_state="In Progress"
    )

    html = client.get(f"/plans/{plan_id}").text
    assert 'class="linear-panel"' in html
    assert "ENG-42" in html
    assert "https://linear.app/acme/issue/ENG-42" in html
    assert "In Progress" in html


def test_plan_view_no_panel_when_unlinked(client, plan_id):
    assert 'class="linear-panel"' not in client.get(f"/plans/{plan_id}").text


def test_project_detail_linear_marker(client, plan_id, project_id):
    from uuid import UUID

    from flanner.database import create_linear_link, get_session

    create_linear_link(get_session(), UUID(plan_id), "ENG-7")
    assert "linear-marker" in client.get(f"/projects/{project_id}").text
