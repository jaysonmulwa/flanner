"""
MCP Server for Flanner

Exposes plan file management tools to Claude Code and other AI assistants.
"""

from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from . import artifacts
from .database import (
    artifact_parents,
    get_plan_file,
    get_project,
    get_session,
    get_version,
    list_versions,
)
from .database import list_plan_files as db_list_plan_files
from .database import list_projects as db_list_projects

# Import our modules
from .freshness import compute_freshness
from .services import dispatch, dispatch_optional, ensure_database
from .storage import (
    load_plan_file,
)

# Initialize MCP server
mcp = FastMCP("flanner")


# Configuration Tools


@mcp.tool()
def get_plan_config(project_id: str | None = None) -> dict[str, Any]:
    """
    Get plan file configuration - tells Claude where and how to create plan files.

    Args:
        project_id: Optional project UUID as string (returns defaults if not provided)

    Returns:
        Configuration dictionary with plan directory, file format, naming conventions
    """
    if project_id:
        session = get_session()
        try:
            project_uuid = UUID(project_id)
            project = get_project(session, project_uuid)
        except ValueError:
            return {"error": True, "message": f"Invalid UUID: {project_id}"}

        if project:
            return {
                "plan_directory": project.plan_directory,
                "project_root": project.project_root,
                "full_path_example": (
                    f"{project.project_root}/{project.plan_directory}/example_v1.md"
                ),
                "file_format": {
                    "frontmatter_required": True,
                    "frontmatter_fields": ["mcp_plan_file", "project_id", "version", "created_by"],
                    "version_suffix": True,
                },
                "naming_convention": "{plan_name}_v{version}.md",
                "auto_gitignore": project.auto_gitignore,
            }

    # Return defaults
    return {
        "plan_directory": ".plans",
        "auto_gitignore": True,
        "file_format": {
            "frontmatter_required": True,
            "frontmatter_fields": ["mcp_plan_file", "project_id", "version", "created_by"],
            "version_suffix": True,
        },
        "naming_convention": "{plan_name}_v{version}.md",
    }


# Project Management Tools


@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """
    List all projects with their configuration.

    Returns:
        List of project dictionaries
    """
    session = get_session()
    projects = db_list_projects(session)

    return [
        {
            "id": str(p.id),  # Convert UUID to string
            "name": p.name,
            "description": p.description,
            "project_root": p.project_root,
            "plan_directory": p.plan_directory,
            "auto_gitignore": p.auto_gitignore,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in projects
    ]


@mcp.tool()
def create_project_tool(
    name: str,
    description: str = "",
    project_root: str | None = None,
    plan_directory: str = ".plans",
) -> dict[str, Any]:
    """
    Create a new project with git integration.

    Args:
        name: Project name (must be unique)
        description: Project description
        project_root: Absolute path to project root (auto-detected from CWD if not provided)
        plan_directory: Relative path for plan files (default: .plans)

    Returns:
        Project information including full plan path
    """
    return dispatch(
        "create_project",
        {
            "name": name,
            "description": description,
            "project_root": project_root,
            "plan_directory": plan_directory,
        },
    )


@mcp.tool()
def initialize_project_tool(
    project_root: str | None = None,
    name: str | None = None,
    plan_directory: str = ".plans",
) -> dict[str, Any]:
    """
    Adopt a repository into flanner so its plan documents are tracked.

    Creates the flanner project (if it does not exist yet) and installs the
    coding-agent integration for the repo: the CLAUDE.md/AGENTS.md guidance
    block, the guard-write hook, the flanner-plan skill, and a .mcp.json entry.
    Use this when the user wants to save a plan, design, architecture, or
    migration doc in a git repo that is not yet flanner-managed, then create the
    document with create_plan_file_tool.

    Args:
        project_root: Repo root to adopt (defaults to the git root of the cwd)
        name: Project name (defaults to the repo directory name)
        plan_directory: Directory for plan files (default ".plans")

    Returns:
        Project info plus the list of integration pieces installed
    """
    return dispatch(
        "initialize_project",
        {"project_root": project_root, "name": name, "plan_directory": plan_directory},
    )


@mcp.tool()
def configure_project_tool(
    project_id: str,
    project_root: str | None = None,
    plan_directory: str | None = None,
    auto_gitignore: bool | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """
    Update project configuration.

    Args:
        project_id: UUID of project to configure (as string)
        project_root: New project root path
        plan_directory: New plan directory
        auto_gitignore: Enable/disable auto .gitignore management
        description: New description

    Returns:
        Updated project information
    """
    return dispatch(
        "configure_project",
        {
            "project_id": project_id,
            "project_root": project_root,
            "plan_directory": plan_directory,
            "auto_gitignore": auto_gitignore,
            "description": description,
        },
    )


@mcp.tool()
def delete_project_tool(project_id: str) -> dict[str, Any]:
    """
    Delete a project and all associated plan files and versions from the database.

    IMPORTANT: This only deletes database records. Plan files on disk are NOT deleted.

    Args:
        project_id: UUID of project to delete (as string)

    Returns:
        Confirmation message or error
    """
    return dispatch("delete_project", {"project_id": project_id})


# Plan File Management Tools


@mcp.tool()
def list_plan_files_tool(
    project_id: str, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """
    List plan files for a project, newest first, one page at a time.

    Defaults return the 50 most recent plan files; pass offset to page
    through the rest. The page size is capped at 200 to keep tool results
    a sane size for the calling model.

    Args:
        project_id: UUID of the project (as string)
        limit: Maximum entries to return (default 50, capped at 200)
        offset: Entries to skip, for paging (default 0)

    Returns:
        List of plan files with current version information
    """
    session = get_session()

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return [{"error": True, "message": f"Invalid UUID: {project_id}"}]

    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    plan_files = db_list_plan_files(session, project_uuid, limit=limit, offset=offset)

    return [
        {
            "id": str(pf.id),  # Convert UUID to string
            "name": pf.name,
            "description": pf.description,
            "current_version": pf.current_version,
            "auto_version": pf.auto_version,
            "created_at": pf.created_at.isoformat() if pf.created_at else None,
            "updated_at": pf.updated_at.isoformat() if pf.updated_at else None,
        }
        for pf in plan_files
    ]


@mcp.tool()
def create_plan_file_tool(
    project_id: str, name: str, content: str, description: str = "", created_by: str = "claude"
) -> dict[str, Any]:
    """
    Create a new plan file (v1) with proper frontmatter.

    IMPORTANT: This function automatically adds YAML frontmatter to the file.
    Just provide the markdown body content without frontmatter.

    Args:
        project_id: UUID of the project (as string)
        name: Plan file name (without .md extension)
        content: The actual plan content (markdown body, WITHOUT frontmatter)
        description: Optional description
        created_by: Who created it (claude, codex, user)

    Returns:
        Plan file information including full file path where it was created
    """
    return dispatch(
        "create_plan_file",
        {
            "project_id": project_id,
            "name": name,
            "content": content,
            "description": description,
            "created_by": created_by,
        },
    )


@mcp.tool()
def update_plan_file_tool(
    plan_file_id: str, content: str, notes: str = "", created_by: str = "claude"
) -> dict[str, Any] | None:
    """
    Update a plan file (creates new version if content changed).

    Args:
        plan_file_id: UUID of the plan file to update (as string)
        content: Updated content (markdown body, without frontmatter)
        notes: Version notes / changelog
        created_by: Who created this version (claude, codex, user)

    Returns:
        New version information or message if no changes detected
    """
    return dispatch_optional(
        "update_plan_file",
        {
            "plan_file_id": plan_file_id,
            "content": content,
            "notes": notes,
            "created_by": created_by,
        },
    )


@mcp.tool()
def get_plan_file_tool(
    plan_file_id: str, version: int | None = None, max_chars: int = 100_000
) -> dict[str, Any]:
    """
    Get plan file content (specific version or latest).

    Content longer than max_chars is truncated so a huge plan cannot blow
    the calling model's context; the result then carries truncated=True and
    total_chars. Raise max_chars (or page by reading the file_path) when the
    full text is genuinely needed.

    Args:
        plan_file_id: UUID of the plan file (as string)
        version: Optional version number (defaults to latest)
        max_chars: Maximum content characters to return (default 100000)

    Returns:
        Plan file content with metadata
    """
    session = get_session()

    # Convert to UUID
    try:
        plan_file_uuid = UUID(plan_file_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {plan_file_id}"}

    # Get plan file
    plan_file = get_plan_file(session, plan_file_uuid)
    if not plan_file:
        return {"error": True, "message": f"Plan file with ID {plan_file_id} not found"}

    # Get version
    version_obj = get_version(session, plan_file_uuid, version)
    if not version_obj:
        return {"error": True, "message": f"Version {version if version else 'latest'} not found"}

    # Load file content
    try:
        frontmatter_data, body = load_plan_file(version_obj.file_path)
    except FileNotFoundError:
        return {"error": True, "message": f"File not found at {version_obj.file_path}"}

    total_chars = len(body)
    truncated = total_chars > max_chars > 0
    if truncated:
        body = body[:max_chars]

    result: dict[str, Any] = {
        "plan_file": {
            "id": str(plan_file.id),  # Convert UUID to string
            "name": plan_file.name,
            "description": plan_file.description,
            "current_version": plan_file.current_version,
        },
        "version": {
            "id": str(version_obj.id),  # Convert UUID to string
            "version": version_obj.version,
            "file_path": version_obj.file_path,
            "created_by": version_obj.created_by,
            "created_at": version_obj.created_at.isoformat() if version_obj.created_at else None,
            "notes": version_obj.notes,
            # Cite this when acting on the plan; version numbers are display
            # projections and are not unique across devices (PRD §20).
            "artifact_id": version_obj.artifact_id,
        },
        "frontmatter": frontmatter_data,
        "content": body,
    }
    if truncated:
        result["truncated"] = True
        result["total_chars"] = total_chars
        result["message"] = (
            f"Content truncated to {max_chars} of {total_chars} characters; "
            "pass a larger max_chars to read more."
        )
    return result


@mcp.tool()
def get_plan_history_tool(plan_file_id: str) -> dict[str, Any]:
    """
    Get version history of a plan file.

    Args:
        plan_file_id: UUID of the plan file (as string)

    Returns:
        Plan file info and list of all versions
    """
    session = get_session()

    # Convert to UUID
    try:
        plan_file_uuid = UUID(plan_file_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {plan_file_id}"}

    # Get plan file
    plan_file = get_plan_file(session, plan_file_uuid)
    if not plan_file:
        return {"error": True, "message": f"Plan file with ID {plan_file_id} not found"}

    # Get all versions
    versions = list_versions(session, plan_file_uuid)
    lineage = artifact_parents(session, str(plan_file_uuid))

    return {
        "plan_file": {
            "id": str(plan_file.id),  # Convert UUID to string
            "name": plan_file.name,
            "description": plan_file.description,
            "current_version": plan_file.current_version,
        },
        "versions": [
            {
                "id": str(v.id),  # Convert UUID to string
                "version": v.version,
                "file_path": v.file_path,
                "content_hash": v.content_hash,
                "created_by": v.created_by,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "notes": v.notes,
                "artifact_id": v.artifact_id,
                "parents": list(lineage.get(v.artifact_id or "", ())),
            }
            for v in versions
        ],
        "total_versions": len(versions),
        # Lineage is what establishes order; version numbers are display only.
        "heads": sorted(artifacts.find_heads(lineage)),
        "conflicted": artifacts.is_conflicted(lineage),
    }


@mcp.tool()
def get_plan_freshness_tool(plan_file_id: str) -> dict[str, Any]:
    """
    Check whether a plan is still likely true before trusting it.

    Computes an evidence-backed freshness status for the plan's latest
    version: which paths and symbols it cites, whether those still exist
    in the repo, and how many commits have touched the cited files since
    the version was authored.

    Args:
        plan_file_id: UUID of the plan file (as string)

    Returns:
        status (fresh | aging | suspect | stale), reasons, and the full
        evidence record (anchor commit, cited refs, invalid refs, churn)
    """
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {plan_file_id}"}

    plan_file = get_plan_file(session, plan_file_uuid)
    if not plan_file:
        return {"error": True, "message": f"Plan file with ID {plan_file_id} not found"}

    project = get_project(session, plan_file.project_id)
    if not project:
        return {"error": True, "message": f"Project for plan {plan_file_id} not found"}

    version_obj = get_version(session, plan_file_uuid, None)
    if not version_obj:
        return {"error": True, "message": "No versions found for this plan"}

    try:
        _, body = load_plan_file(version_obj.file_path)
    except FileNotFoundError:
        return {"error": True, "message": f"File not found at {version_obj.file_path}"}

    if not project.project_root:
        return {"error": True, "message": f"Project '{project.name}' has no project_root"}

    evidence = compute_freshness(project.project_root, body, version_obj.created_at)
    return {
        "plan_file": {
            "id": str(plan_file.id),
            "name": plan_file.name,
            "current_version": plan_file.current_version,
        },
        **evidence,
    }


# JIRA Integration Tools


@mcp.tool()
def configure_jira_tool(
    project_id: str, jira_url: str, jira_project_key: str | None = None
) -> dict[str, Any]:
    """
    Configure JIRA integration for a project.

    Args:
        project_id: UUID of the project (as string)
        jira_url: JIRA base URL (e.g., https://company.atlassian.net)
        jira_project_key: Optional default JIRA project key (e.g., PROJ)

    Returns:
        Configuration result with JIRA settings
    """
    return dispatch(
        "configure_jira",
        {"project_id": project_id, "jira_url": jira_url, "jira_project_key": jira_project_key},
    )


@mcp.tool()
def link_plan_to_jira_tool(
    plan_file_id: str, jira_issue_key: str, issue_type: str | None = None, notes: str | None = None
) -> dict[str, Any]:
    """
    Link a plan file to a JIRA issue.

    Args:
        plan_file_id: UUID of the plan file (as string)
        jira_issue_key: JIRA issue key (e.g., PROJ-123)
        issue_type: Optional issue type (Epic, Story, Task, etc.)
        notes: Optional notes about the link

    Returns:
        Link result with JIRA issue URL
    """
    return dispatch(
        "link_plan_to_jira",
        {
            "plan_file_id": plan_file_id,
            "jira_issue_key": jira_issue_key,
            "issue_type": issue_type,
            "notes": notes,
        },
    )


@mcp.tool()
def get_jira_links_tool(plan_file_id: str) -> dict[str, Any]:
    """
    Get all JIRA links for a plan file.

    Args:
        plan_file_id: UUID of the plan file (as string)

    Returns:
        List of JIRA links with URLs
    """
    from .database import get_jira_config, get_jira_links
    from .jira_utils import generate_jira_issue_url

    ensure_database()
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {plan_file_id}"}

    # Check if plan file exists
    plan_file = get_plan_file(session, plan_file_uuid)
    if not plan_file:
        return {"error": True, "message": f"Plan file with ID {plan_file_id} not found"}

    # Get links
    links = get_jira_links(session, plan_file_uuid)

    # Get JIRA config for URL generation
    jira_config = get_jira_config(session, plan_file.project_id)

    return {
        "plan_file_id": plan_file_id,
        "plan_file_name": plan_file.name,
        "links": [
            {
                "id": str(link.id),
                "jira_issue_key": link.jira_issue_key,
                "jira_issue_type": link.jira_issue_type,
                "notes": link.notes,
                "jira_url": generate_jira_issue_url(jira_config.jira_url, link.jira_issue_key)
                if jira_config
                else None,
                "created_at": link.created_at.isoformat() if link.created_at else None,
                "created_by": link.created_by,
            }
            for link in links
        ],
        "total_links": len(links),
    }


@mcp.tool()
def list_jira_links_tool(project_id: str) -> dict[str, Any]:
    """
    List all JIRA links for all plan files in a project.

    Args:
        project_id: UUID of the project (as string)

    Returns:
        List of all JIRA links in the project
    """
    from .database import get_jira_config, list_all_jira_links
    from .jira_utils import generate_jira_issue_url

    ensure_database()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    # Check if project exists
    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    # Get all links
    links = list_all_jira_links(session, project_uuid)

    # Get JIRA config for URL generation
    jira_config = get_jira_config(session, project_uuid)

    return {
        "project_id": project_id,
        "project_name": project.name,
        "links": [
            {
                "plan_file_id": str(link["plan_file_id"]),
                "plan_file_name": link["plan_file_name"],
                "jira_link_id": str(link["jira_link_id"]),
                "jira_issue_key": link["jira_issue_key"],
                "jira_issue_type": link["jira_issue_type"],
                "notes": link["notes"],
                "jira_url": generate_jira_issue_url(jira_config.jira_url, link["jira_issue_key"])
                if jira_config
                else None,
                "created_at": link["created_at"].isoformat() if link["created_at"] else None,
                "created_by": link["created_by"],
            }
            for link in links
        ],
        "total_links": len(links),
    }


@mcp.tool()
def unlink_jira_issue_tool(plan_file_id: str, jira_issue_key: str | None = None) -> dict[str, Any]:
    """
    Unlink a JIRA issue from a plan file.

    Args:
        plan_file_id: UUID of the plan file (as string)
        jira_issue_key: Optional JIRA issue key to unlink (if None, unlinks all)

    Returns:
        Result of unlink operation
    """
    return dispatch(
        "unlink_jira_issue", {"plan_file_id": plan_file_id, "jira_issue_key": jira_issue_key}
    )


@mcp.tool()
def get_jira_config_tool(project_id: str) -> dict[str, Any]:
    """
    Get JIRA configuration for a project.

    Args:
        project_id: UUID of the project (as string)

    Returns:
        JIRA configuration or None if not configured
    """
    from .database import get_jira_config

    ensure_database()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    # Check if project exists
    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    # Get JIRA config
    jira_config = get_jira_config(session, project_uuid)

    if jira_config:
        return {
            "configured": True,
            "id": str(jira_config.id),
            "project_id": str(jira_config.project_id),
            "jira_url": jira_config.jira_url,
            "jira_project_key": jira_config.jira_project_key,
            "created_at": jira_config.created_at.isoformat() if jira_config.created_at else None,
            "updated_at": jira_config.updated_at.isoformat() if jira_config.updated_at else None,
        }
    else:
        return {"configured": False, "message": "JIRA not configured for this project"}


# Linear Integration Tools


@mcp.tool()
def configure_linear_tool(project_id: str, workspace: str) -> dict[str, Any]:
    """
    Configure Linear integration for a project.

    Args:
        project_id: UUID of the project (as string)
        workspace: Linear workspace slug or URL (e.g. "acme" or
            https://linear.app/acme)

    Returns:
        Configuration result with the stored workspace slug
    """
    return dispatch("configure_linear", {"project_id": project_id, "workspace": workspace})


@mcp.tool()
def link_plan_to_linear_tool(
    plan_file_id: str,
    linear_issue_id: str,
    notes: str | None = None,
    verify: bool = True,
    attach_url: str | None = None,
) -> dict[str, Any]:
    """
    Link a plan file to a Linear issue.

    If LINEAR_API_KEY is set and verify is true, the issue is checked against
    the Linear API and its title/state are cached. A missing issue is an error;
    a network failure falls back to a link-only record with a warning. When
    attach_url is given, that URL is attached to the Linear issue.

    Args:
        plan_file_id: UUID of the plan file (as string)
        linear_issue_id: Linear issue identifier (e.g. ENG-123)
        notes: Optional notes about the link
        verify: Verify/enrich via the Linear API when a key is configured
        attach_url: Optional URL to attach to the Linear issue

    Returns:
        Link result with the Linear issue URL and any cached title/state
    """
    return dispatch(
        "link_plan_to_linear",
        {
            "plan_file_id": plan_file_id,
            "linear_issue_id": linear_issue_id,
            "notes": notes,
            "verify": verify,
            "attach_url": attach_url,
        },
    )


@mcp.tool()
def get_linear_links_tool(plan_file_id: str) -> dict[str, Any]:
    """
    Get all Linear links for a plan file.

    Args:
        plan_file_id: UUID of the plan file (as string)

    Returns:
        List of Linear links with URLs and any cached title/state
    """
    from .database import get_linear_config, get_linear_links
    from .linear_utils import generate_linear_issue_url

    ensure_database()
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {plan_file_id}"}

    plan_file = get_plan_file(session, plan_file_uuid)
    if not plan_file:
        return {"error": True, "message": f"Plan file with ID {plan_file_id} not found"}

    links = get_linear_links(session, plan_file_uuid)
    config = get_linear_config(session, plan_file.project_id)

    return {
        "plan_file_id": plan_file_id,
        "plan_file_name": plan_file.name,
        "links": [
            {
                "id": str(link.id),
                "linear_issue_id": link.linear_issue_id,
                "issue_title": link.issue_title,
                "issue_state": link.issue_state,
                "notes": link.notes,
                "linear_url": generate_linear_issue_url(config.workspace, link.linear_issue_id)
                if config
                else None,
                "created_at": link.created_at.isoformat() if link.created_at else None,
                "created_by": link.created_by,
            }
            for link in links
        ],
        "total_links": len(links),
    }


@mcp.tool()
def list_linear_links_tool(project_id: str) -> dict[str, Any]:
    """
    List all Linear links for all plan files in a project.

    Args:
        project_id: UUID of the project (as string)

    Returns:
        List of all Linear links in the project
    """
    from .database import get_linear_config, list_all_linear_links
    from .linear_utils import generate_linear_issue_url

    ensure_database()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    links = list_all_linear_links(session, project_uuid)
    config = get_linear_config(session, project_uuid)

    return {
        "project_id": project_id,
        "project_name": project.name,
        "links": [
            {
                "plan_file_id": str(link["plan_file_id"]),
                "plan_file_name": link["plan_file_name"],
                "linear_link_id": str(link["linear_link_id"]),
                "linear_issue_id": link["linear_issue_id"],
                "issue_title": link["issue_title"],
                "issue_state": link["issue_state"],
                "notes": link["notes"],
                "linear_url": generate_linear_issue_url(config.workspace, link["linear_issue_id"])
                if config
                else None,
                "created_at": link["created_at"].isoformat() if link["created_at"] else None,
                "created_by": link["created_by"],
            }
            for link in links
        ],
        "total_links": len(links),
    }


@mcp.tool()
def unlink_linear_issue_tool(
    plan_file_id: str, linear_issue_id: str | None = None
) -> dict[str, Any]:
    """
    Unlink a Linear issue from a plan file.

    Args:
        plan_file_id: UUID of the plan file (as string)
        linear_issue_id: Optional issue id to unlink (if None, unlinks all)

    Returns:
        Result of the unlink operation
    """
    return dispatch(
        "unlink_linear_issue", {"plan_file_id": plan_file_id, "linear_issue_id": linear_issue_id}
    )


@mcp.tool()
def get_linear_config_tool(project_id: str) -> dict[str, Any]:
    """
    Get Linear configuration for a project.

    Args:
        project_id: UUID of the project (as string)

    Returns:
        Linear configuration or a not-configured marker
    """
    from .database import get_linear_config

    ensure_database()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    config = get_linear_config(session, project_uuid)
    if config:
        return {
            "configured": True,
            "id": str(config.id),
            "project_id": str(config.project_id),
            "workspace": config.workspace,
            "created_at": config.created_at.isoformat() if config.created_at else None,
            "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        }
    return {"configured": False, "message": "Linear not configured for this project"}


def main() -> None:
    """Run the MCP server over stdio.

    Entry point for the `flanner-mcp` console script and for
    `python -m flanner.server`.
    """
    # Initialize the database before serving: tools assume a live session,
    # and an MCP client's first call is otherwise "Database not initialized"
    ensure_database()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
