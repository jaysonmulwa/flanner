"""
Web interface for Flanner

Provides a browser-based UI for viewing and managing plan files.
"""

import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

import markdown
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
)
from .database import list_plan_files as db_list_plan_files
from .database import list_projects as db_list_projects
from .exceptions import DatabaseError
from .frontmatter import create_plan_file_content, generate_frontmatter
from .git_integration import find_git_root, update_gitignore, validate_git_repo
from .storage import ensure_plan_directory_exists, load_plan_file, save_plan_file_with_frontmatter
from .utils import format_relative_time, generate_file_name, hash_content

# Initialize FastAPI app
app = FastAPI(
    title="Flanner", description="Manage plan files with automatic versioning", version="0.1.0"
)

# Get paths
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# Ensure database is initialized
def ensure_db():
    """Ensure database is initialized"""
    try:
        get_session()
    except DatabaseError:
        # path resolution is env-aware in init_database
        init_database()


# Template filters
def markdown_filter(text):
    """Convert markdown to HTML"""
    if not text:
        return ""

    # Configure markdown with extensions
    md = markdown.Markdown(
        extensions=["fenced_code", "codehilite", "tables", "toc", "nl2br"],
        extension_configs={"codehilite": {"css_class": "highlight", "linenums": False}},
    )

    return md.convert(text)


# Add custom filters to Jinja2
templates.env.filters["markdown"] = markdown_filter
templates.env.filters["relative_time"] = format_relative_time


# =============================================================================
# HTML PAGES
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard - show all projects"""
    ensure_db()
    session = get_session()

    projects = db_list_projects(session)

    # Calculate stats
    total_projects = len(projects)
    total_plans = sum(len(p.plan_files) for p in projects)

    # Get recent activity (last updated plan files)
    recent_activity = []
    for project in projects:
        for plan_file in project.plan_files:
            recent_activity.append(
                {"project": project, "plan_file": plan_file, "updated_at": plan_file.updated_at}
            )

    # Sort by updated_at
    recent_activity.sort(
        key=lambda x: x["updated_at"] if x["updated_at"] else datetime.min, reverse=True
    )
    recent_activity = recent_activity[:10]  # Top 10

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "projects": projects,
            "total_projects": total_projects,
            "total_plans": total_plans,
            "recent_activity": recent_activity,
        },
    )


@app.get("/projects", response_class=HTMLResponse)
async def projects_list(request: Request):
    """List all projects"""
    ensure_db()
    session = get_session()

    projects = db_list_projects(session)

    return templates.TemplateResponse(
        request, "projects.html", {"request": request, "projects": projects}
    )


@app.get("/projects/new", response_class=HTMLResponse)
async def new_project_form(request: Request):
    """Show create project form"""
    return templates.TemplateResponse(request, "project_new.html", {"request": request})


@app.post("/projects/new")
async def create_project_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    project_root: str = Form(None),
    plan_directory: str = Form(".plans"),
):
    """Create a new project"""
    ensure_db()
    session = get_session()

    # Auto-detect project root if not provided
    if not project_root or project_root.strip() == "":
        project_root = find_git_root(os.getcwd())
        if not project_root:
            return templates.TemplateResponse(
                request,
                "project_new.html",
                {
                    "request": request,
                    "error": (
                        "Could not find git repository. Please specify project root manually."
                    ),
                },
            )

    # Validate git repository
    if not validate_git_repo(project_root):
        return templates.TemplateResponse(
            request,
            "project_new.html",
            {"request": request, "error": f"{project_root} is not a valid git repository"},
        )

    # Create project
    try:
        project = create_project(
            session,
            name=name,
            description=description,
            project_root=project_root,
            plan_directory=plan_directory,
            auto_gitignore=True,
        )

        # Create plan directory
        ensure_plan_directory_exists(project_root, plan_directory)

        # Update .gitignore
        pattern = plan_directory.rstrip("/") + "/"
        update_gitignore(project_root, pattern, comment="MCP Plan Manager")

        return RedirectResponse(url=f"/projects/{project.id}", status_code=303)

    except ValueError as e:
        return templates.TemplateResponse(
            request,
            "project_new.html",
            {
                "request": request,
                "error": str(e),
                "name": name,
                "description": description,
                "project_root": project_root,
                "plan_directory": plan_directory,
            },
        )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: str):
    """Show project detail with all plan files"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
        project = get_project(session, project_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    plan_files = db_list_plan_files(session, project_uuid)

    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {"request": request, "project": project, "plan_files": plan_files},
    )


@app.post("/projects/{project_id}/delete")
async def delete_project_post(project_id: str):
    """Delete a project and all associated plan files"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
        project = get_project(session, project_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete project (cascade deletes plan files and versions)
    if delete_project(session, project_uuid):
        return RedirectResponse(url="/projects?message=deleted", status_code=303)
    else:
        raise HTTPException(status_code=500, detail="Failed to delete project")


@app.get("/projects/{project_id}/plans/new", response_class=HTMLResponse)
async def new_plan_form(request: Request, project_id: str):
    """Show create plan file form"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
        project = get_project(session, project_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return templates.TemplateResponse(
        request, "plan_new.html", {"request": request, "project": project}
    )


@app.post("/projects/{project_id}/plans/new")
async def create_plan_post(
    request: Request,
    project_id: str,
    name: str = Form(...),
    description: str = Form(""),
    content: str = Form(...),
):
    """Create a new plan file"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
        project = get_project(session, project_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Create plan file in database
    try:
        plan_file = db_create_plan_file(
            session, project_id=project_uuid, name=name, description=description, auto_version=True
        )
    except ValueError as e:
        return templates.TemplateResponse(
            request,
            "plan_new.html",
            {
                "request": request,
                "project": project,
                "error": str(e),
                "name": name,
                "description": description,
                "content": content,
            },
        )

    # Generate frontmatter
    frontmatter_str = generate_frontmatter(
        project_id=project.id,
        project_name=project.name,
        plan_file_id=plan_file.id,
        plan_name=name,
        version=1,
        created_by="user",
        created_at=datetime.utcnow(),
    )

    # Combine frontmatter + content
    full_content = create_plan_file_content(frontmatter_str, content)

    # Generate filename
    file_name = generate_file_name(name, 1)

    # Save file
    file_path = save_plan_file_with_frontmatter(
        project_root=project.project_root,
        plan_directory=project.plan_directory,
        file_name=file_name,
        content=full_content,
    )

    # Create version record
    content_hash = hash_content(content)
    create_version(
        session,
        plan_file_id=plan_file.id,
        version=1,
        file_path=file_path,
        content_hash=content_hash,
        created_by="user",
        notes="Initial version",
    )

    return RedirectResponse(url=f"/plans/{plan_file.id}", status_code=303)


@app.get("/plans/{plan_file_id}", response_class=HTMLResponse)
async def plan_view(request: Request, plan_file_id: str, version: int | None = None):
    """View a plan file (specific version or latest)"""
    ensure_db()
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
        plan_file = get_plan_file(session, plan_file_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan file ID") from None

    if not plan_file:
        raise HTTPException(status_code=404, detail="Plan file not found")

    # Get project
    project = get_project(session, plan_file.project_id)

    # Get version
    version_obj = get_version(session, plan_file_uuid, version)
    if not version_obj:
        raise HTTPException(
            status_code=404, detail=f"Version {version if version else 'latest'} not found"
        )

    # Get all versions for version selector
    all_versions = list_versions(session, plan_file_uuid)

    # Load file content
    try:
        frontmatter_data, body = load_plan_file(version_obj.file_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"File not found at {version_obj.file_path}"
        ) from None

    return templates.TemplateResponse(
        request,
        "plan_view.html",
        {
            "request": request,
            "project": project,
            "plan_file": plan_file,
            "version": version_obj,
            "all_versions": all_versions,
            "frontmatter": frontmatter_data,
            "content": body,
        },
    )


@app.get("/plans/{plan_file_id}/edit", response_class=HTMLResponse)
async def plan_edit(request: Request, plan_file_id: str):
    """Edit a plan file (creates new version)"""
    ensure_db()
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
        plan_file = get_plan_file(session, plan_file_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan file ID") from None

    if not plan_file:
        raise HTTPException(status_code=404, detail="Plan file not found")

    # Get project
    project = get_project(session, plan_file.project_id)

    # Get latest version
    version_obj = get_version(session, plan_file_uuid)
    if not version_obj:
        raise HTTPException(status_code=404, detail="No versions found")

    # Load file content
    try:
        frontmatter_data, body = load_plan_file(version_obj.file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found") from None

    return templates.TemplateResponse(
        request,
        "plan_edit.html",
        {
            "request": request,
            "project": project,
            "plan_file": plan_file,
            "version": version_obj,
            "content": body,
        },
    )


@app.post("/plans/{plan_file_id}/edit")
async def plan_update(
    request: Request, plan_file_id: str, content: str = Form(...), notes: str = Form("")
):
    """Update a plan file (creates new version)"""
    ensure_db()
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
        plan_file = get_plan_file(session, plan_file_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan file ID") from None

    if not plan_file:
        raise HTTPException(status_code=404, detail="Plan file not found")

    # Get project
    project = get_project(session, plan_file.project_id)

    # Get latest version
    latest_version = get_version(session, plan_file_uuid)

    # Check if content changed
    new_hash = hash_content(content)
    if latest_version.content_hash == new_hash:
        # No changes, redirect back to view
        return RedirectResponse(url=f"/plans/{plan_file_id}?message=no_changes", status_code=303)

    # Create new version
    new_version_num = plan_file.current_version + 1

    # Generate frontmatter
    frontmatter_str = generate_frontmatter(
        project_id=project.id,
        project_name=project.name,
        plan_file_id=plan_file.id,
        plan_name=plan_file.name,
        version=new_version_num,
        created_by="user",
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
    create_version(
        session,
        plan_file_id=plan_file.id,
        version=new_version_num,
        file_path=file_path,
        content_hash=new_hash,
        created_by="user",
        notes=notes,
    )

    # Update plan file current version
    plan_file.current_version = new_version_num
    plan_file.updated_at = datetime.utcnow()
    session.commit()

    return RedirectResponse(url=f"/plans/{plan_file_id}", status_code=303)


@app.get("/plans/{plan_file_id}/history", response_class=HTMLResponse)
async def plan_history(request: Request, plan_file_id: str):
    """View version history of a plan file"""
    ensure_db()
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
        plan_file = get_plan_file(session, plan_file_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan file ID") from None

    if not plan_file:
        raise HTTPException(status_code=404, detail="Plan file not found")

    # Get project
    project = get_project(session, plan_file.project_id)

    # Get all versions
    versions = list_versions(session, plan_file_uuid)

    return templates.TemplateResponse(
        request,
        "plan_history.html",
        {"request": request, "project": project, "plan_file": plan_file, "versions": versions},
    )


# =============================================================================
# API ENDPOINTS (JSON responses for AJAX)
# =============================================================================


@app.get("/api/projects")
async def api_list_projects():
    """API: List all projects"""
    ensure_db()
    session = get_session()

    projects = db_list_projects(session)

    return [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "project_root": p.project_root,
            "plan_directory": p.plan_directory,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "plan_files_count": len(p.plan_files),
        }
        for p in projects
    ]


@app.get("/api/projects/{project_id}/plans")
async def api_list_plan_files(project_id: str):
    """API: List plan files for a project"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
        plan_files = db_list_plan_files(session, project_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    return [
        {
            "id": str(pf.id),
            "name": pf.name,
            "description": pf.description,
            "current_version": pf.current_version,
            "updated_at": pf.updated_at.isoformat() if pf.updated_at else None,
        }
        for pf in plan_files
    ]


@app.get("/api/plans/{plan_file_id}")
async def api_get_plan(plan_file_id: str, version: int | None = None):
    """API: Get plan file content"""
    ensure_db()
    session = get_session()

    try:
        plan_file_uuid = UUID(plan_file_id)
        plan_file = get_plan_file(session, plan_file_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plan file ID") from None

    if not plan_file:
        raise HTTPException(status_code=404, detail="Plan file not found")

    # Get version
    version_obj = get_version(session, plan_file_uuid, version)
    if not version_obj:
        raise HTTPException(status_code=404, detail="Version not found")

    # Load content
    try:
        frontmatter_data, body = load_plan_file(version_obj.file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found") from None

    return {
        "plan_file": {
            "id": str(plan_file.id),
            "name": plan_file.name,
            "description": plan_file.description,
            "current_version": plan_file.current_version,
        },
        "version": {
            "version": version_obj.version,
            "created_by": version_obj.created_by,
            "created_at": version_obj.created_at.isoformat() if version_obj.created_at else None,
            "notes": version_obj.notes,
        },
        "content": body,
        "frontmatter": frontmatter_data,
    }


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str):
    """API: Delete a project"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
        project = get_project(session, project_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_name = project.name
    plan_files_count = len(project.plan_files)

    # Delete project
    if delete_project(session, project_uuid):
        return {
            "success": True,
            "message": f"Project '{project_name}' deleted successfully",
            "plan_files_deleted": plan_files_count,
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to delete project")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)  # local-only tool, no auth layer
