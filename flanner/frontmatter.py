"""
Frontmatter handling for Flanner

Provides YAML frontmatter generation and parsing for plan files.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import frontmatter
import yaml


def generate_frontmatter(
    project_id: UUID,
    project_name: str,
    plan_file_id: UUID,
    plan_name: str,
    version: int,
    created_by: str,
    created_at: datetime | None = None,
) -> str:
    """
    Generate YAML frontmatter for a plan file.

    Args:
        project_id: UUID of the project
        project_name: Name of the project
        plan_file_id: UUID of the plan file
        plan_name: Name of the plan
        version: Version number
        created_by: Who created this version (user, claude, codex)
        created_at: Creation timestamp (defaults to now)

    Returns:
        YAML frontmatter string (including --- delimiters)
    """
    if created_at is None:
        created_at = datetime.utcnow()

    # Create frontmatter dictionary
    fm_data = {
        "mcp_plan_file": True,
        "plan_manager_version": "1.0",
        "project_id": str(project_id),  # Convert UUID to string
        "project_name": project_name,
        "plan_file_id": str(plan_file_id),  # Convert UUID to string
        "plan_name": plan_name,
        "version": version,
        "created_at": created_at.isoformat() + "Z",
        "created_by": created_by,
    }

    # Generate YAML
    yaml_str = yaml.dump(fm_data, default_flow_style=False, sort_keys=False)

    # Wrap in --- delimiters
    return f"---\n{yaml_str}---"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """
    Parse frontmatter from markdown content.

    Args:
        content: Full markdown content (with frontmatter)

    Returns:
        Tuple of (frontmatter_dict, content_body)
        If no frontmatter, returns (empty dict, original content)
    """
    try:
        post = frontmatter.loads(content)
        return (dict(post.metadata), post.content)
    except (yaml.YAMLError, ValueError):
        # No frontmatter or invalid format
        return ({}, content)


def validate_frontmatter(fm_data: dict[str, Any]) -> bool:
    """
    Validate that frontmatter has required fields.

    Args:
        fm_data: Frontmatter dictionary

    Returns:
        True if valid, False otherwise
    """
    required_fields = [
        "mcp_plan_file",
        "project_id",
        "plan_file_id",
        "plan_name",
        "version",
        "created_by",
    ]

    for field in required_fields:
        if field not in fm_data:
            return False

    # Check that mcp_plan_file is True
    if fm_data.get("mcp_plan_file") is not True:
        return False

    return True


def is_mcp_plan_file(content: str) -> bool:
    """
    Check if content is an MCP plan file by checking frontmatter.

    Args:
        content: File content

    Returns:
        True if this is an MCP plan file
    """
    fm_data, _ = parse_frontmatter(content)
    return fm_data.get("mcp_plan_file") is True


def update_frontmatter(content: str, updates: dict[str, Any]) -> str:
    """
    Update frontmatter fields in content.

    Args:
        content: Original content with frontmatter
        updates: Dictionary of fields to update

    Returns:
        Updated content with modified frontmatter
    """
    fm_data, body = parse_frontmatter(content)

    # Update fields
    fm_data.update(updates)

    # Reconstruct
    post = frontmatter.Post(body, **fm_data)
    return frontmatter.dumps(post)


def create_plan_file_content(frontmatter_str: str, body: str) -> str:
    """
    Combine frontmatter and body into complete plan file content.

    Args:
        frontmatter_str: Frontmatter string (with --- delimiters)
        body: Markdown body content

    Returns:
        Complete file content
    """
    # Ensure frontmatter ends with newline
    if not frontmatter_str.endswith("\n"):
        frontmatter_str += "\n"

    # Combine
    return frontmatter_str + "\n" + body


def extract_metadata_from_frontmatter(fm_data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract specific metadata fields from frontmatter.

    Args:
        fm_data: Frontmatter dictionary

    Returns:
        Dictionary with extracted metadata
    """
    return {
        "project_id": fm_data.get("project_id"),
        "project_name": fm_data.get("project_name"),
        "plan_file_id": fm_data.get("plan_file_id"),
        "plan_name": fm_data.get("plan_name"),
        "version": fm_data.get("version"),
        "created_by": fm_data.get("created_by"),
        "created_at": fm_data.get("created_at"),
        "plan_manager_version": fm_data.get("plan_manager_version", "1.0"),
    }


def increment_version_in_frontmatter(content: str) -> str:
    """
    Increment the version number in frontmatter.

    Args:
        content: Original content

    Returns:
        Content with incremented version
    """
    fm_data, body = parse_frontmatter(content)

    if "version" in fm_data:
        fm_data["version"] = fm_data["version"] + 1
    else:
        fm_data["version"] = 1

    # Update created_at
    fm_data["created_at"] = datetime.utcnow().isoformat() + "Z"

    post = frontmatter.Post(body, **fm_data)
    return frontmatter.dumps(post)
