"""Tests for daemon discovery, IPC endpoints, and MCP write forwarding."""

import pytest
from fastapi.testclient import TestClient

from flanner import ipc


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_daemon_info_roundtrip(home):
    assert ipc.read_daemon_info() is None
    ipc.write_daemon_info(8080, "tok")
    info = ipc.read_daemon_info()
    assert info["port"] == 8080
    assert info["token"] == "tok"
    ipc.clear_daemon_info()
    assert ipc.read_daemon_info() is None


def test_call_daemon_without_daemon_returns_none(home):
    assert ipc.call_daemon("/ipc/create_plan", {}) is None


def test_call_daemon_with_stale_info_returns_none(home):
    # Advertised daemon is not actually listening: fall back, don't raise.
    ipc.write_daemon_info(1, "tok")
    assert ipc.call_daemon("/ipc/create_plan", {}) is None


@pytest.fixture
def web_client(db, monkeypatch):
    from flanner.web import app

    monkeypatch.setenv(ipc.TOKEN_ENV, "secret-token")
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def project_id(web_client, git_repo):
    response = web_client.post(
        "/projects/new",
        data={"name": "ipcproj", "project_root": str(git_repo), "plan_directory": ".plans"},
    )
    assert response.status_code in (302, 303)
    return response.headers["location"].rstrip("/").split("/")[-1]


def _headers(token="secret-token"):
    return {"X-Flanner-Token": token}


def test_ipc_rejects_bad_token(web_client, project_id):
    response = web_client.post(
        "/ipc/create_plan",
        json={"project_id": project_id, "name": "x", "content": "b"},
        headers=_headers("wrong"),
    )
    assert response.status_code == 401


def test_ipc_disabled_without_env(web_client, project_id, monkeypatch):
    monkeypatch.delenv(ipc.TOKEN_ENV)
    response = web_client.post(
        "/ipc/create_plan",
        json={"project_id": project_id, "name": "x", "content": "b"},
        headers=_headers(),
    )
    assert response.status_code == 503


def test_ipc_create_and_update_plan(web_client, project_id):
    created = web_client.post(
        "/ipc/create_plan",
        json={"project_id": project_id, "name": "specs/api", "content": "# v1\n"},
        headers=_headers(),
    )
    assert created.status_code == 200
    body = created.json()
    assert body["version"] == 1
    plan_id = body["id"]

    updated = web_client.post(
        "/ipc/update_plan",
        json={"plan_file_id": plan_id, "content": "# v2\n", "notes": "n"},
        headers=_headers(),
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    unchanged = web_client.post(
        "/ipc/update_plan",
        json={"plan_file_id": plan_id, "content": "# v2\n"},
        headers=_headers(),
    )
    assert unchanged.status_code == 200
    assert "No changes" in unchanged.json()["message"]


def test_mcp_write_tools_forward_to_daemon(monkeypatch):
    import flanner.server as server

    calls = []

    def fake_call(path, payload):
        calls.append(path)
        return {"forwarded": True}

    monkeypatch.setattr(server.ipc, "call_daemon", fake_call)
    assert server.create_plan_file_tool("p", "n", "c") == {"forwarded": True}
    assert server.update_plan_file_tool("p", "c") == {"forwarded": True}
    assert calls == ["/ipc/create_plan", "/ipc/update_plan"]
