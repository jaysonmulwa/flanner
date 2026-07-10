"""
Linear utility functions for Flanner

Validation and URL generation for Linear integration. No network here; this is
the foundation layer (see linear_api for the GraphQL client).

Linear issue identifiers look like ``ENG-123`` (team key + number), the same
shape as a JIRA issue key. Issues live under a workspace URL slug (the
``urlKey``), e.g. ``https://linear.app/acme/issue/ENG-123``.
"""

import re
from urllib.parse import urlparse

# Team key + issue number, e.g. ENG-123. Same shape as a JIRA issue key.
LINEAR_ISSUE_ID_PATTERN = r"^[A-Z][A-Z0-9]*-[0-9]+$"
# Workspace URL slug (Linear's urlKey): lowercase alphanumerics and hyphens.
LINEAR_WORKSPACE_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


def is_valid_linear_issue_id(issue_id: str) -> bool:
    """
    Validate a Linear issue identifier.

    Valid: ENG-123, A-1, PLATFORM-9999. Invalid: eng-123, ENG123, 1ENG-1.
    """
    if not issue_id:
        return False
    return bool(re.match(LINEAR_ISSUE_ID_PATTERN, issue_id.strip()))


def format_linear_issue_id(issue_id: str) -> str:
    """Normalize an identifier to Linear's canonical uppercase form."""
    return issue_id.strip().upper()


def normalize_linear_workspace(workspace: str) -> str:
    """
    Reduce a workspace input to its bare URL slug.

    Accepts either a slug (``acme``) or a full workspace URL
    (``https://linear.app/acme`` or ``https://linear.app/acme/team/...``) and
    returns ``acme``. Lowercased, surrounding slashes/whitespace stripped.
    """
    value = workspace.strip()
    if value.lower().startswith(("http://", "https://")):
        # Take the first path segment after the host, e.g. .../acme/... -> acme
        path = urlparse(value).path.strip("/")
        value = path.split("/")[0] if path else ""
    return value.strip("/").lower()


def is_valid_linear_workspace(workspace: str) -> bool:
    """Validate a workspace slug after normalization."""
    slug = normalize_linear_workspace(workspace)
    if not slug:
        return False
    return bool(re.match(LINEAR_WORKSPACE_PATTERN, slug))


def generate_linear_issue_url(workspace: str, issue_id: str) -> str:
    """
    Build the canonical Linear issue URL.

    ``linear.app/<workspace>/issue/<ID>`` redirects to the full slugged URL, so
    the short form is a stable, shareable link.
    """
    slug = normalize_linear_workspace(workspace)
    return f"https://linear.app/{slug}/issue/{format_linear_issue_id(issue_id)}"


def extract_team_key(issue_id: str) -> str | None:
    """Team key from an identifier, e.g. ENG-123 -> ENG. None if invalid."""
    if not is_valid_linear_issue_id(issue_id):
        return None
    return issue_id.strip().upper().split("-")[0]


def extract_issue_number(issue_id: str) -> int | None:
    """Issue number from an identifier, e.g. ENG-123 -> 123. None if invalid."""
    if not is_valid_linear_issue_id(issue_id):
        return None
    try:
        return int(issue_id.strip().split("-")[1])
    except (IndexError, ValueError):
        return None
