"""
Linear GraphQL API client for Flanner (Tier 2, live sync).

A thin client over Linear's GraphQL API using only the standard library, so it
adds no runtime dependency. Used to verify that a linked issue exists, pull its
title/state, and attach a URL to it.

Authentication uses a personal API key read from the ``LINEAR_API_KEY``
environment variable. The key is never stored in the database, taken as a CLI
flag, or logged: env-only keeps it out of shell history and the on-disk store.
When no key is set the caller falls back to Tier 1 (link-only) behaviour.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .exceptions import LinearError
from .linear_utils import extract_issue_number, extract_team_key

LINEAR_API_URL = "https://api.linear.app/graphql"
DEFAULT_TIMEOUT = 10.0

# Fetch an issue by its human identifier (team key + number). Linear's `issue`
# query wants the internal UUID, so we filter by team key + number instead.
_ISSUE_QUERY = """
query IssueByIdentifier($team: String!, $number: Float!) {
  issues(filter: { team: { key: { eq: $team } }, number: { eq: $number } }, first: 1) {
    nodes { id identifier title url state { name type } assignee { name } }
  }
}
"""

_ATTACH_MUTATION = """
mutation AttachLink($issueId: String!, $url: String!, $title: String!) {
  attachmentLinkURL(issueId: $issueId, url: $url, title: $title) { success }
}
"""


def get_api_key() -> str | None:
    """Return the Linear API key from the environment, or None if unset."""
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    return key or None


def _post(
    payload: dict[str, Any], api_key: str, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    """
    POST a GraphQL request and return the parsed ``data`` object.

    Raises LinearError on transport failure, non-JSON responses, or GraphQL
    errors. Isolated so tests can stub it without touching the network.
    """
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 (LINEAR_API_URL is a fixed https constant)
        LINEAR_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Personal API keys go in Authorization as-is (no "Bearer" prefix).
            "Authorization": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.code
        raise LinearError(f"Linear API returned HTTP {detail}") from exc
    except urllib.error.URLError as exc:
        raise LinearError(f"Could not reach the Linear API: {exc.reason}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise LinearError("Linear API returned a malformed response") from exc

    if body.get("errors"):
        messages = "; ".join(e.get("message", "unknown error") for e in body["errors"])
        raise LinearError(f"Linear API error: {messages}")

    result: dict[str, Any] = body.get("data") or {}
    return result


def fetch_issue(
    team_key: str, number: int, api_key: str, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any] | None:
    """
    Look up an issue by team key + number.

    Returns a dict with id, identifier, title, url, state, and assignee, or
    None if no such issue exists in the workspace the key can see.
    """
    data = _post(
        {"query": _ISSUE_QUERY, "variables": {"team": team_key, "number": float(number)}},
        api_key,
        timeout,
    )
    nodes = (data.get("issues") or {}).get("nodes") or []
    if not nodes:
        return None
    node = nodes[0]
    state = node.get("state") or {}
    assignee = node.get("assignee") or {}
    return {
        "id": node.get("id"),
        "identifier": node.get("identifier"),
        "title": node.get("title"),
        "url": node.get("url"),
        "state": state.get("name"),
        "state_type": state.get("type"),
        "assignee": assignee.get("name"),
    }


def fetch_issue_by_identifier(
    identifier: str, api_key: str, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any] | None:
    """Look up an issue by its ENG-123 identifier. None if the id is malformed
    or no such issue exists."""
    team_key = extract_team_key(identifier)
    number = extract_issue_number(identifier)
    if team_key is None or number is None:
        return None
    return fetch_issue(team_key, number, api_key, timeout)


def attach_url_to_issue(
    issue_uuid: str, url: str, title: str, api_key: str, timeout: float = DEFAULT_TIMEOUT
) -> bool:
    """
    Attach a link to a Linear issue. ``issue_uuid`` is the internal id returned
    by fetch_issue (not the ENG-123 identifier). Returns True on success.
    """
    data = _post(
        {
            "query": _ATTACH_MUTATION,
            "variables": {"issueId": issue_uuid, "url": url, "title": title},
        },
        api_key,
        timeout,
    )
    return bool((data.get("attachmentLinkURL") or {}).get("success"))
