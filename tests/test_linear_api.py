"""Unit tests for flanner.linear_api. Network is always stubbed."""

import io
import json
import urllib.error

import pytest

from flanner import linear_api
from flanner.exceptions import LinearError


class _FakeResponse:
    """Minimal context-manager stand-in for an http.client response."""

    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


def test_get_api_key(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    assert linear_api.get_api_key() is None
    monkeypatch.setenv("LINEAR_API_KEY", "  lin_api_x  ")
    assert linear_api.get_api_key() == "lin_api_x"
    monkeypatch.setenv("LINEAR_API_KEY", "   ")
    assert linear_api.get_api_key() is None


def test_post_success(monkeypatch):
    monkeypatch.setattr(
        linear_api.urllib.request, "urlopen", lambda *a, **k: _FakeResponse({"data": {"ok": 1}})
    )
    assert linear_api._post({"query": "x"}, "key") == {"ok": 1}


def test_post_graphql_errors(monkeypatch):
    monkeypatch.setattr(
        linear_api.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"errors": [{"message": "nope"}]}),
    )
    with pytest.raises(LinearError, match="nope"):
        linear_api._post({"query": "x"}, "key")


def test_post_http_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(linear_api.urllib.request, "urlopen", boom)
    with pytest.raises(LinearError, match="HTTP 401"):
        linear_api._post({"query": "x"}, "key")


def test_post_url_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(linear_api.urllib.request, "urlopen", boom)
    with pytest.raises(LinearError, match="reach the Linear API"):
        linear_api._post({"query": "x"}, "key")


def test_post_malformed(monkeypatch):
    class Bad(_FakeResponse):
        def read(self) -> bytes:
            return b"not json"

    monkeypatch.setattr(linear_api.urllib.request, "urlopen", lambda *a, **k: Bad({}))
    with pytest.raises(LinearError, match="malformed"):
        linear_api._post({"query": "x"}, "key")


def test_fetch_issue_found(monkeypatch):
    node = {
        "id": "uuid-1",
        "identifier": "ENG-1",
        "title": "Do the thing",
        "url": "https://linear.app/acme/issue/ENG-1",
        "state": {"name": "In Progress", "type": "started"},
        "assignee": {"name": "Ada"},
    }
    monkeypatch.setattr(linear_api, "_post", lambda *a, **k: {"issues": {"nodes": [node]}})
    issue = linear_api.fetch_issue("ENG", 1, "key")
    assert issue["id"] == "uuid-1"
    assert issue["title"] == "Do the thing"
    assert issue["state"] == "In Progress"
    assert issue["assignee"] == "Ada"


def test_fetch_issue_missing(monkeypatch):
    monkeypatch.setattr(linear_api, "_post", lambda *a, **k: {"issues": {"nodes": []}})
    assert linear_api.fetch_issue("ENG", 99, "key") is None


def test_fetch_issue_by_identifier(monkeypatch):
    seen = {}

    def fake_fetch(team, number, api_key, timeout=10.0):
        seen["team"], seen["number"] = team, number
        return {"title": "t", "state": "s", "id": "u"}

    monkeypatch.setattr(linear_api, "fetch_issue", fake_fetch)
    # A malformed identifier short-circuits to None without calling fetch_issue.
    assert linear_api.fetch_issue_by_identifier("nope", "key") is None
    assert seen == {}
    linear_api.fetch_issue_by_identifier("ENG-5", "key")
    assert seen == {"team": "ENG", "number": 5}


def test_attach_url_to_issue(monkeypatch):
    monkeypatch.setattr(
        linear_api, "_post", lambda *a, **k: {"attachmentLinkURL": {"success": True}}
    )
    assert linear_api.attach_url_to_issue("uuid-1", "https://x", "title", "key") is True
    monkeypatch.setattr(linear_api, "_post", lambda *a, **k: {"attachmentLinkURL": {}})
    assert linear_api.attach_url_to_issue("uuid-1", "https://x", "title", "key") is False
