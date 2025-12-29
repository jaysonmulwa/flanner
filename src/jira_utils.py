"""
JIRA utility functions for Flanner

Provides validation and URL generation for JIRA integration.
"""

import re
from urllib.parse import urlparse
from typing import Optional


# JIRA issue key pattern: PROJECT-123
JIRA_ISSUE_KEY_PATTERN = r'^[A-Z][A-Z0-9]*-[0-9]+$'


def is_valid_jira_issue_key(issue_key: str) -> bool:
    """
    Validate JIRA issue key format.

    Valid formats:
    - PROJ-123
    - ABC-1
    - LONGPROJECTKEY-99999

    Args:
        issue_key: JIRA issue key to validate

    Returns:
        True if valid, False otherwise
    """
    if not issue_key:
        return False
    return bool(re.match(JIRA_ISSUE_KEY_PATTERN, issue_key.strip()))


def is_valid_jira_url(url: str) -> bool:
    """
    Validate JIRA base URL format.

    Valid formats:
    - https://company.atlassian.net
    - https://jira.company.com
    - http://localhost:8080

    Args:
        url: JIRA base URL to validate

    Returns:
        True if valid, False otherwise
    """
    if not url:
        return False

    try:
        result = urlparse(url.strip())
        return all([
            result.scheme in ['http', 'https'],
            result.netloc
        ])
    except Exception:
        return False


def normalize_jira_url(url: str) -> str:
    """
    Normalize JIRA URL by removing trailing slashes.

    Args:
        url: JIRA base URL

    Returns:
        Normalized URL
    """
    return url.strip().rstrip('/')


def generate_jira_issue_url(jira_base_url: str, issue_key: str) -> str:
    """
    Generate full JIRA issue URL from base URL and issue key.

    Args:
        jira_base_url: JIRA base URL (e.g., https://company.atlassian.net)
        issue_key: JIRA issue key (e.g., PROJ-123)

    Returns:
        Full JIRA issue URL (e.g., https://company.atlassian.net/browse/PROJ-123)
    """
    base = normalize_jira_url(jira_base_url)
    return f"{base}/browse/{issue_key}"


def format_jira_issue_key(issue_key: str) -> str:
    """
    Format JIRA issue key to uppercase.

    Args:
        issue_key: JIRA issue key

    Returns:
        Uppercase formatted issue key
    """
    return issue_key.strip().upper()


def extract_project_key(issue_key: str) -> Optional[str]:
    """
    Extract project key from JIRA issue key.

    Args:
        issue_key: JIRA issue key (e.g., PROJ-123)

    Returns:
        Project key (e.g., PROJ) or None if invalid
    """
    if not is_valid_jira_issue_key(issue_key):
        return None

    parts = issue_key.split('-')
    if len(parts) >= 2:
        return parts[0]
    return None


def extract_issue_number(issue_key: str) -> Optional[int]:
    """
    Extract issue number from JIRA issue key.

    Args:
        issue_key: JIRA issue key (e.g., PROJ-123)

    Returns:
        Issue number (e.g., 123) or None if invalid
    """
    if not is_valid_jira_issue_key(issue_key):
        return None

    parts = issue_key.split('-')
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None
