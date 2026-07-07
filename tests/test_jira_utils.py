"""Unit tests for flanner.jira_utils (no network; URL/key handling only)."""

from flanner.jira_utils import (
    extract_issue_number,
    extract_project_key,
    format_jira_issue_key,
    generate_jira_issue_url,
    is_valid_jira_issue_key,
    is_valid_jira_url,
    normalize_jira_url,
)


def test_is_valid_jira_issue_key():
    assert is_valid_jira_issue_key("PROJ-123") is True
    assert is_valid_jira_issue_key("A1-9") is True
    assert is_valid_jira_issue_key(" PROJ-1 ") is True
    assert is_valid_jira_issue_key("") is False
    assert is_valid_jira_issue_key("proj-123") is False
    assert is_valid_jira_issue_key("PROJ123") is False
    assert is_valid_jira_issue_key("1PROJ-123") is False
    assert is_valid_jira_issue_key("PROJ-") is False


def test_is_valid_jira_url():
    assert is_valid_jira_url("https://x.atlassian.net") is True
    assert is_valid_jira_url("http://localhost:8080") is True
    assert is_valid_jira_url("") is False
    assert is_valid_jira_url("not-a-url") is False
    assert is_valid_jira_url("ftp://x.example.com") is False


def test_normalize_jira_url():
    assert normalize_jira_url("https://x.atlassian.net/") == "https://x.atlassian.net"
    assert normalize_jira_url("  https://x.atlassian.net// ") == "https://x.atlassian.net"


def test_generate_jira_issue_url():
    url = generate_jira_issue_url("https://x.atlassian.net/", "PROJ-42")
    assert url == "https://x.atlassian.net/browse/PROJ-42"


def test_format_jira_issue_key():
    assert format_jira_issue_key(" proj-7 ") == "PROJ-7"


def test_extract_project_key():
    assert extract_project_key("PROJ-123") == "PROJ"
    assert extract_project_key("bogus") is None


def test_extract_issue_number():
    assert extract_issue_number("PROJ-123") == 123
    assert extract_issue_number("bogus") is None
