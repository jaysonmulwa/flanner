"""Web interface route tests."""

import pytest
from fastapi.testclient import TestClient

from flanner.web import app


@pytest.fixture
def client(db):
    return TestClient(app)


def test_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200


def test_projects_list(client):
    response = client.get("/projects")
    assert response.status_code == 200


def test_new_project_form(client):
    response = client.get("/projects/new")
    assert response.status_code == 200


def test_api_list_projects(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
