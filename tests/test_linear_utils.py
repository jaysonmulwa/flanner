"""Unit tests for flanner.linear_utils (no network; validation/URL only)."""

from flanner.linear_utils import (
    extract_issue_number,
    extract_team_key,
    format_linear_issue_id,
    generate_linear_issue_url,
    is_valid_linear_issue_id,
    is_valid_linear_workspace,
    normalize_linear_workspace,
)


def test_is_valid_linear_issue_id():
    assert is_valid_linear_issue_id("ENG-123") is True
    assert is_valid_linear_issue_id("A1-9") is True
    assert is_valid_linear_issue_id(" ENG-1 ") is True
    assert is_valid_linear_issue_id("") is False
    assert is_valid_linear_issue_id("eng-123") is False
    assert is_valid_linear_issue_id("ENG123") is False
    assert is_valid_linear_issue_id("1ENG-1") is False
    assert is_valid_linear_issue_id("ENG-") is False


def test_format_linear_issue_id():
    assert format_linear_issue_id(" eng-7 ") == "ENG-7"


def test_normalize_linear_workspace():
    assert normalize_linear_workspace("Acme") == "acme"
    assert normalize_linear_workspace("  acme/ ") == "acme"
    assert normalize_linear_workspace("https://linear.app/acme") == "acme"
    assert normalize_linear_workspace("https://linear.app/acme/team/ENG/all") == "acme"
    assert normalize_linear_workspace("https://linear.app/") == ""


def test_is_valid_linear_workspace():
    assert is_valid_linear_workspace("acme") is True
    assert is_valid_linear_workspace("acme-corp") is True
    assert is_valid_linear_workspace("https://linear.app/acme") is True
    assert is_valid_linear_workspace("") is False
    assert is_valid_linear_workspace("-bad") is False
    assert is_valid_linear_workspace("Bad Space") is False


def test_generate_linear_issue_url():
    url = generate_linear_issue_url("https://linear.app/Acme/", "eng-42")
    assert url == "https://linear.app/acme/issue/ENG-42"


def test_extract_team_key():
    assert extract_team_key("ENG-123") == "ENG"
    assert extract_team_key("bogus") is None


def test_extract_issue_number():
    assert extract_issue_number("ENG-123") == 123
    assert extract_issue_number("bogus") is None
