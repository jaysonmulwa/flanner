"""
MCP Server for Flanner

Exposes plan file management tools to Claude Code and other AI assistants.
"""

import os
from datetime import datetime
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .database import create_plan_file as db_create_plan_file
from .database import (
    create_project,
    create_version,
    delete_project,
    get_plan_file,
    get_project,
    get_session,
    get_version,
    init_database,
    list_versions,
    update_project,
)
from .database import list_plan_files as db_list_plan_files
from .database import list_projects as db_list_projects

# Import our modules
from .exceptions import DatabaseError
from .frontmatter import create_plan_file_content, generate_frontmatter
from .git_integration import find_git_root, update_gitignore, validate_git_repo
from .storage import (
    ensure_plan_directory_exists,
    load_plan_file,
    save_plan_file_with_frontmatter,
)
from .utils import generate_file_name, hash_content

# Initialize MCP server
mcp = FastMCP("flanner")


# Initialize database (will be called when tools are used)
def ensure_database() -> None:
    """Ensure database is initialized"""
    try:
        get_session()  # Test if session exists
    except DatabaseError:
        # Database not initialized; path resolution is env-aware in init_database
        init_database()


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
    session = get_session()

    # Auto-detect project root if not provided
    if not project_root:
        project_root = find_git_root(os.getcwd())
        if not project_root:
            return {
                "error": True,
                "message": (
                    "Could not find git repository. Please specify project_root "
                    "or run from within a git repository."
                ),
            }

    # Validate it's a git repository
    if not validate_git_repo(project_root):
        return {"error": True, "message": f"{project_root} is not a valid git repository"}

    # Create project in database
    try:
        project = create_project(
            session,
            name=name,
            description=description,
            project_root=project_root,
            plan_directory=plan_directory,
            auto_gitignore=True,
        )
    except ValueError as e:
        return {"error": True, "message": str(e)}

    # Create plan directory
    full_plan_path = ensure_plan_directory_exists(project_root, plan_directory)

    # Update .gitignore
    pattern = plan_directory.rstrip("/") + "/"
    gitignore_updated = update_gitignore(project_root, pattern, comment="MCP Plan Manager")

    return {
        "id": str(project.id),  # Convert UUID to string
        "name": project.name,
        "description": project.description,
        "project_root": project_root,
        "plan_directory": plan_directory,
        "full_plan_path": full_plan_path,
        "gitignore_updated": gitignore_updated,
        "message": f"Project '{name}' created successfully",
    }


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
    session = get_session()

    # Convert to UUID
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    # Get current project
    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    old_plan_dir = project.plan_directory

    # Update project (returns the same identity-mapped instance as `project`, refreshed)
    update_project(
        session,
        project_uuid,
        project_root=project_root,
        plan_directory=plan_directory,
        auto_gitignore=auto_gitignore,
        description=description,
    )

    # If plan directory changed and auto_gitignore is enabled, update .gitignore
    if plan_directory and plan_directory != old_plan_dir and project.auto_gitignore:
        if project.project_root:
            from .git_integration import update_plan_directory_in_gitignore

            old_pattern = old_plan_dir.rstrip("/") + "/"
            new_pattern = plan_directory.rstrip("/") + "/"
            update_plan_directory_in_gitignore(project.project_root, old_pattern, new_pattern)

    return {
        "id": str(project.id),  # Convert UUID to string
        "name": project.name,
        "description": project.description,
        "project_root": project.project_root,
        "plan_directory": project.plan_directory,
        "auto_gitignore": project.auto_gitignore,
        "message": "Project configuration updated successfully",
    }


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
    session = get_session()

    # Convert to UUID
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    # Get project info before deletion
    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    project_name = project.name
    plan_files_count = len(project.plan_files)
    plan_directory_path = (
        f"{project.project_root}/{project.plan_directory}" if project.project_root else None
    )

    # Delete project (cascade deletes plan files and versions)
    if delete_project(session, project_uuid):
        message = (
            f"Project '{project_name}' deleted successfully. "
            f"Removed {plan_files_count} plan file(s) from database."
        )
        if plan_directory_path:
            message += f" Note: Files on disk at {plan_directory_path} were NOT deleted."

        return {
            "success": True,
            "project_name": project_name,
            "plan_files_deleted": plan_files_count,
            "plan_directory_path": plan_directory_path,
            "message": message,
        }
    else:
        return {"error": True, "message": "Failed to delete project"}


# Plan File Management Tools


@mcp.tool()
def list_plan_files_tool(project_id: str) -> list[dict[str, Any]]:
    """
    List all plan files for a project.

    Args:
        project_id: UUID of the project (as string)

    Returns:
        List of plan files with current version information
    """
    session = get_session()

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return [{"error": True, "message": f"Invalid UUID: {project_id}"}]

    plan_files = db_list_plan_files(session, project_uuid)

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
    session = get_session()

    # Convert to UUID
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    # Get project
    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    if not project.project_root:
        # Previously crashed with TypeError; surface the config problem instead
        return {
            "error": True,
            "message": f"Project '{project.name}' has no project_root configured",
        }

    # Create plan file record in database
    try:
        plan_file = db_create_plan_file(
            session, project_id=project_uuid, name=name, description=description, auto_version=True
        )
    except ValueError as e:
        return {"error": True, "message": str(e)}

    # Generate frontmatter
    frontmatter_str = generate_frontmatter(
        project_id=project.id,
        project_name=project.name,
        plan_file_id=plan_file.id,
        plan_name=name,
        version=1,
        created_by=created_by,
        created_at=datetime.utcnow(),
    )

    # Combine frontmatter + content
    full_content = create_plan_file_content(frontmatter_str, content)

    # Generate filename
    file_name = generate_file_name(name, 1)

    # Save file to disk (in project's plan directory)
    file_path = save_plan_file_with_frontmatter(
        project_root=project.project_root,
        plan_directory=project.plan_directory,
        file_name=file_name,
        content=full_content,
    )

    # Create version record
    content_hash = hash_content(content)  # Hash body only, not frontmatter
    create_version(
        session,
        plan_file_id=plan_file.id,
        version=1,
        file_path=file_path,
        content_hash=content_hash,
        created_by=created_by,
        notes="Initial version",
    )

    return {
        "id": str(plan_file.id),  # Convert UUID to string
        "name": plan_file.name,
        "version": 1,
        "file_path": file_path,
        "message": f"Plan file created successfully at {file_path}",
    }


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

    # Get project
    project = get_project(session, plan_file.project_id)
    if not project:
        return {"error": True, "message": "Project not found"}

    if not project.project_root:
        # Previously crashed with TypeError; surface the config problem instead
        return {
            "error": True,
            "message": f"Project '{project.name}' has no project_root configured",
        }

    # Get latest version
    latest_version = get_version(session, plan_file_uuid)
    if not latest_version:
        return {"error": True, "message": "No versions found for this plan file"}

    # Check if content actually changed
    new_hash = hash_content(content)
    if latest_version.content_hash == new_hash:
        return {
            "message": "No changes detected (content is identical)",
            "version": latest_version.version,
            "file_path": latest_version.file_path,
        }

    # Create new version
    if plan_file.auto_version:
        new_version_num = plan_file.current_version + 1

        # Generate frontmatter for new version
        frontmatter_str = generate_frontmatter(
            project_id=project.id,
            project_name=project.name,
            plan_file_id=plan_file.id,
            plan_name=plan_file.name,
            version=new_version_num,
            created_by=created_by,
            created_at=datetime.utcnow(),
        )

        # Combine frontmatter + content
        full_content = create_plan_file_content(frontmatter_str, content)

        # Generate new filename
        file_name = generate_file_name(plan_file.name, new_version_num)

        # Save new file
        file_path = save_plan_file_with_frontmatter(
            project_root=project.project_root,
            plan_directory=project.plan_directory,
            file_name=file_name,
            content=full_content,
        )

        # Create version record
        version = create_version(
            session,
            plan_file_id=plan_file.id,
            version=new_version_num,
            file_path=file_path,
            content_hash=new_hash,
            created_by=created_by,
            notes=notes,
        )

        # Update plan file current version
        plan_file.current_version = new_version_num
        plan_file.updated_at = datetime.utcnow()
        session.commit()

        return {
            "id": str(version.id),  # Convert UUID to string
            "version": new_version_num,
            "file_path": file_path,
            "content_hash": new_hash,
            "created_by": created_by,
            "message": f"Created version {new_version_num} at {file_path}",
        }

    # auto_version disabled: preserve historical behavior of returning None
    return None


@mcp.tool()
def get_plan_file_tool(plan_file_id: str, version: int | None = None) -> dict[str, Any]:
    """
    Get plan file content (specific version or latest).

    Args:
        plan_file_id: UUID of the plan file (as string)
        version: Optional version number (defaults to latest)

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

    return {
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
        },
        "frontmatter": frontmatter_data,
        "content": body,
    }


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
            }
            for v in versions
        ],
        "total_versions": len(versions),
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
    from .database import create_jira_config
    from .jira_utils import is_valid_jira_url, normalize_jira_url

    ensure_database()
    session = get_session()

    # Validate URL
    if not is_valid_jira_url(jira_url):
        return {
            "error": True,
            "message": f"Invalid JIRA URL format: {jira_url}. Expected format: https://company.atlassian.net",
        }

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {project_id}"}

    # Check if project exists
    project = get_project(session, project_uuid)
    if not project:
        return {"error": True, "message": f"Project with ID {project_id} not found"}

    # Create or update JIRA config
    try:
        normalized_url = normalize_jira_url(jira_url)
        jira_config = create_jira_config(session, project_uuid, normalized_url, jira_project_key)

        return {
            "id": str(jira_config.id),
            "project_id": str(jira_config.project_id),
            "jira_url": jira_config.jira_url,
            "jira_project_key": jira_config.jira_project_key,
            "message": f"JIRA configuration updated for project '{project.name}'",
        }
    except Exception as e:
        return {"error": True, "message": str(e)}


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
    from .database import create_jira_link, get_jira_config
    from .jira_utils import format_jira_issue_key, generate_jira_issue_url, is_valid_jira_issue_key

    ensure_database()
    session = get_session()

    # Validate and format issue key
    formatted_issue = format_jira_issue_key(jira_issue_key)
    if not is_valid_jira_issue_key(formatted_issue):
        return {
            "error": True,
            "message": (
                f"Invalid JIRA issue key format: {jira_issue_key}. Expected format: PROJECT-123"
            ),
        }

    try:
        plan_file_uuid = UUID(plan_file_id)
    except ValueError:
        return {"error": True, "message": f"Invalid UUID: {plan_file_id}"}

    # Check if plan file exists
    plan_file = get_plan_file(session, plan_file_uuid)
    if not plan_file:
        return {"error": True, "message": f"Plan file with ID {plan_file_id} not found"}

    # Create link
    try:
        jira_link = create_jira_link(
            session, plan_file_uuid, formatted_issue, issue_type, notes, created_by="claude"
        )

        # Get JIRA config for URL generation
        jira_config = get_jira_config(session, plan_file.project_id)
        jira_url = None
        if jira_config:
            jira_url = generate_jira_issue_url(jira_config.jira_url, formatted_issue)

        return {
            "id": str(jira_link.id),
            "plan_file_id": str(jira_link.plan_file_id),
            "jira_issue_key": jira_link.jira_issue_key,
            "jira_issue_type": jira_link.jira_issue_type,
            "notes": jira_link.notes,
            "jira_url": jira_url,
            "created_at": jira_link.created_at.isoformat() if jira_link.created_at else None,
            "message": f"Linked '{plan_file.name}' to {formatted_issue}",
        }
    except ValueError as e:
        return {"error": True, "message": str(e)}
    except Exception as e:
        return {"error": True, "message": f"Failed to create link: {str(e)}"}


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
    from .database import delete_all_jira_links, delete_jira_link_by_key
    from .jira_utils import format_jira_issue_key

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

    # Unlink
    try:
        if jira_issue_key:
            formatted_issue = format_jira_issue_key(jira_issue_key)
            deleted = delete_jira_link_by_key(session, plan_file_uuid, formatted_issue)
            if deleted:
                return {
                    "success": True,
                    "message": f"Unlinked '{plan_file.name}' from {formatted_issue}",
                }
            else:
                return {"error": True, "message": f"Link to {formatted_issue} not found"}
        else:
            count = delete_all_jira_links(session, plan_file_uuid)
            return {
                "success": True,
                "message": f"Unlinked {count} JIRA issue(s) from '{plan_file.name}'",
                "count": count,
            }
    except Exception as e:
        return {"error": True, "message": f"Failed to unlink: {str(e)}"}


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


if __name__ == "__main__":
    # Run the MCP server
    mcp.run(transport="stdio")
