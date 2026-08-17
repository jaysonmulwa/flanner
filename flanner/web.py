"""
Web interface for Flanner

Provides a browser-based UI for viewing and managing plan files.
"""

import logging
import os
import secrets
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import UUID

import markdown
import nh3
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import __version__, ipc, services
from .database import count_plan_files as db_count_plan_files
from .database import count_plan_files_recent as db_count_plan_files_recent
from .database import count_projects as db_count_projects
from .database import (
    create_project,
    delete_project,
    get_linear_config,
    get_linear_links,
    get_plan_file,
    get_project,
    get_session,
    get_version,
    init_database,
    list_all_linear_links,
    list_versions,
    plan_file_counts_by_project,
    recent_plan_files,
)
from .database import list_plan_files as db_list_plan_files
from .database import list_projects as db_list_projects
from .exceptions import DatabaseError
from .git_integration import find_git_root, update_gitignore, validate_git_repo
from .linear_utils import generate_linear_issue_url
from .plan_ops import create_plan, record_new_version
from .storage import ensure_plan_directory_exists, load_plan_file
from .utils import format_relative_time, hash_content

# Initialize FastAPI app
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Flanner", description="Manage plan files with automatic versioning", version=__version__
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
def ensure_db() -> None:
    """Ensure database is initialized"""
    try:
        get_session()
    except DatabaseError:
        # path resolution is env-aware in init_database
        init_database()


# Tags/attributes kept when sanitizing rendered markdown. Everything markdown
# produces (including codehilite's span/class and heading ids) is allowed; the
# sanitizer strips <script>, event handlers, javascript: URLs, and <style>.
_SANITIZE_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_SANITIZE_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "span": {"class"},
    "pre": {"class"},
    "div": {"class"},
    "h1": {"id"},
    "h2": {"id"},
    "h3": {"id"},
    "h4": {"id"},
    "h5": {"id"},
    "h6": {"id"},
    "td": {"align"},
    "th": {"align"},
}


# Template filters
def markdown_filter(text: str | None) -> str:
    """Render markdown to sanitized HTML.

    The output is inserted with ``|safe``, so it is run through nh3 to strip any
    raw HTML that could execute (scripts, event handlers, javascript: URLs) while
    keeping the formatting and code-highlighting markup markdown emits.
    """
    if not text:
        return ""

    md = markdown.Markdown(
        extensions=["fenced_code", "codehilite", "tables", "toc", "nl2br"],
        extension_configs={"codehilite": {"css_class": "highlight", "linenums": False}},
    )
    return nh3.clean(md.convert(text), tags=_SANITIZE_TAGS, attributes=_SANITIZE_ATTRS)


# Add custom filters to Jinja2
templates.env.filters["markdown"] = markdown_filter
templates.env.filters["relative_time"] = format_relative_time
templates.env.filters["basename"] = lambda p: Path(p).name


# Stamp static assets so the browser refetches when they change. The newest
# mtime under static/ means an edit-then-restart busts the cache even within a
# release (the version string alone would not, since it only moves on release).
def _asset_version() -> str:
    try:
        newest = max(f.stat().st_mtime for f in STATIC_DIR.rglob("*") if f.is_file())
        return f"{__version__}-{int(newest)}"
    except ValueError:
        return __version__


templates.env.globals["asset_version"] = _asset_version()

_STATUS_LABELS = {400: "Bad Request", 404: "Not Found", 500: "Server Error"}


@app.exception_handler(HTTPException)
async def html_error_pages(request: Request, exc: HTTPException) -> Response:
    """Browsers get a styled error page; /api/* callers keep JSON."""
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": exc.status_code,
            "status_label": _STATUS_LABELS.get(exc.status_code, "Error"),
            "detail": exc.detail,
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def html_validation_pages(request: Request, exc: RequestValidationError) -> Response:
    """Bad query/form input (e.g. ?page=abc) gets a styled 400, not raw 422 JSON."""
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": exc.errors()}, status_code=422)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": 400,
            "status_label": "Bad Request",
            "detail": "That request had an invalid value. Check the address and try again.",
        },
        status_code=400,
    )


@app.exception_handler(Exception)
async def html_crash_page(request: Request, exc: Exception) -> Response:
    """Unexpected failures: log the traceback, never show one to the user."""
    logger.exception("Unhandled error on %s", request.url.path)
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "status_label": "Server Error",
            "detail": "Something went wrong on our side. The details are in the server log.",
        },
        status_code=500,
    )


# Rendering guard: markdown.convert on multi-MB documents takes seconds and,
# called from an async route, would freeze the event loop for every client.
MAX_RENDER_CHARS = 1_000_000
PAGE_SIZE = 50

# Rendered-HTML cache keyed by content hash; versions are immutable so a
# hash hit can never be stale. ponytail: in-process OrderedDict LRU is
# plenty for a single-user local tool.
_RENDER_CACHE_MAX = 64
_render_cache: OrderedDict[str, str] = OrderedDict()


def render_plan_html(content: str, content_hash: str | None) -> str:
    """Markdown -> HTML with an LRU cache on the version's content hash."""
    key = content_hash or hash_content(content)
    if key in _render_cache:
        _render_cache.move_to_end(key)
        return _render_cache[key]
    html = markdown_filter(content)
    _render_cache[key] = html
    if len(_render_cache) > _RENDER_CACHE_MAX:
        _render_cache.popitem(last=False)
    return html


# =============================================================================
# HTML PAGES
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Dashboard - show all projects"""
    ensure_db()
    session = get_session()

    # Aggregates in SQL; loading every plan file to count them is O(rows)
    # in Python and an N+1 query per project.
    total_projects = db_count_projects(session)
    total_plans = db_count_plan_files(session)
    updated_this_week = db_count_plan_files_recent(session, days=7)
    plan_counts = plan_file_counts_by_project(session)

    projects = db_list_projects(session, limit=12)

    recent_activity: list[dict[str, Any]] = [
        {"project": pf.project, "plan_file": pf, "updated_at": pf.updated_at}
        for pf in recent_plan_files(session, limit=10)
    ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "plan_counts": plan_counts,
            "request": request,
            "projects": projects,
            "total_projects": total_projects,
            "total_plans": total_plans,
            "updated_this_week": updated_this_week,
            "recent_activity": recent_activity,
        },
    )


@app.get("/projects", response_class=HTMLResponse)
async def projects_list(
    request: Request, page: int = 1, message: str | None = None
) -> HTMLResponse:
    """List projects, a page at a time"""
    ensure_db()
    session = get_session()
    success = {"deleted": "Project deleted."}.get(message or "")

    total = db_count_projects(session)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    projects = db_list_projects(session, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    plan_counts = plan_file_counts_by_project(session)

    return templates.TemplateResponse(
        request,
        "projects.html",
        {
            "request": request,
            "projects": projects,
            "plan_counts": plan_counts,
            "page": page,
            "pages": pages,
            "total": total,
            "success": success,
        },
    )


@app.get("/projects/new", response_class=HTMLResponse)
async def new_project_form(request: Request) -> HTMLResponse:
    """Show create project form"""
    return templates.TemplateResponse(request, "project_new.html", {"request": request})


@app.post("/projects/new")
async def create_project_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    project_root: str | None = Form(None),
    plan_directory: str = Form(".plans"),
) -> Response:
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
async def project_detail(request: Request, project_id: str, page: int = 1) -> HTMLResponse:
    """Show project detail with a page of plan files"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
        project = get_project(session, project_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    total = db_count_plan_files(session, project_uuid)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(1, page), pages)
    plan_files = db_list_plan_files(
        session, project_uuid, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )

    # Count Linear links per plan so the list can mark linked plans.
    linear_counts: dict[str, int] = {}
    for row in list_all_linear_links(session, project_uuid):
        key = str(row["plan_file_id"])
        linear_counts[key] = linear_counts.get(key, 0) + 1

    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "request": request,
            "project": project,
            "plan_files": plan_files,
            "page": page,
            "pages": pages,
            "total": total,
            "linear_counts": linear_counts,
        },
    )


@app.post("/projects/{project_id}/delete")
async def delete_project_post(project_id: str) -> RedirectResponse:
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
async def new_plan_form(request: Request, project_id: str) -> HTMLResponse:
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
) -> Response:
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

    if not project.project_root:
        # Previously crashed with TypeError (HTTP 500); surface the config problem instead
        raise HTTPException(status_code=400, detail="Project has no project_root configured")

    # Create plan record and initial version through the shared write path
    try:
        plan_file, _ = create_plan(
            session,
            project=project,
            name=name,
            content=content,
            description=description,
            created_by="user",
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

    return RedirectResponse(url=f"/plans/{plan_file.id}", status_code=303)


@app.get("/plans/{plan_file_id}", response_class=HTMLResponse)
async def plan_view(
    request: Request,
    plan_file_id: str,
    version: int | None = None,
    message: str | None = None,
) -> HTMLResponse:
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

    # Linked Linear issues (title/state are cached from the last link/refresh).
    linear_config = get_linear_config(session, plan_file.project_id)
    linear_links = [
        {
            "issue_id": link.linear_issue_id,
            "title": link.issue_title,
            "state": link.issue_state,
            "notes": link.notes,
            "url": generate_linear_issue_url(linear_config.workspace, link.linear_issue_id)
            if linear_config
            else None,
        }
        for link in get_linear_links(session, plan_file_uuid)
    ]

    # Load file content
    try:
        frontmatter_data, body = load_plan_file(version_obj.file_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"File not found at {version_obj.file_path}"
        ) from None

    # Render off the event loop; a large document must not stall other clients.
    render_capped = len(body) > MAX_RENDER_CHARS
    if render_capped:
        content_html = ""
    else:
        content_html = await run_in_threadpool(render_plan_html, body, version_obj.content_hash)

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
            "linear_links": linear_links,
            "content": body,
            "content_html": content_html,
            "render_capped": render_capped,
            "content_chars": len(body),
            "info": {
                "no_changes": "No changes detected - the content matches the current version, "
                "so a new version was not created."
            }.get(message or ""),
        },
    )


@app.get("/plans/{plan_file_id}/edit", response_class=HTMLResponse)
async def plan_edit(request: Request, plan_file_id: str) -> HTMLResponse:
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
) -> RedirectResponse:
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
    if not project:
        # Previously crashed with AttributeError (HTTP 500); explicit 404 instead
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.project_root:
        # Previously crashed with TypeError (HTTP 500); surface the config problem instead
        raise HTTPException(status_code=400, detail="Project has no project_root configured")

    # Get latest version
    latest_version = get_version(session, plan_file_uuid)
    if not latest_version:
        # Previously crashed with AttributeError (HTTP 500); explicit 404 instead
        raise HTTPException(status_code=404, detail="No versions found")

    # Check if content changed
    new_hash = hash_content(content)
    if latest_version.content_hash == new_hash:
        # No changes, redirect back to view
        return RedirectResponse(url=f"/plans/{plan_file_id}?message=no_changes", status_code=303)

    # Create new version (locked: refreshes, picks the next free number, commits)
    record_new_version(
        session,
        project=project,
        plan_file=plan_file,
        content=content,
        created_by="user",
        notes=notes,
    )

    return RedirectResponse(url=f"/plans/{plan_file_id}", status_code=303)


@app.get("/plans/{plan_file_id}/history", response_class=HTMLResponse)
async def plan_history(request: Request, plan_file_id: str) -> HTMLResponse:
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
async def api_list_projects() -> list[dict[str, Any]]:
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


@app.get("/api/search")
async def api_search_index() -> list[dict[str, str]]:
    """Flat index of projects and plans for the command palette."""
    ensure_db()
    session = get_session()
    items: list[dict[str, str]] = []
    for p in db_list_projects(session):
        items.append(
            {"type": "project", "name": p.name, "context": "", "url": f"/projects/{p.id}"}
        )
        for pf in p.plan_files:
            items.append(
                {"type": "plan", "name": pf.name, "context": p.name, "url": f"/plans/{pf.id}"}
            )
    return items


@app.get("/api/projects/{project_id}/plans")
async def api_list_plan_files(project_id: str) -> list[dict[str, Any]]:
    """API: List plan files for a project"""
    ensure_db()
    session = get_session()

    try:
        project_uuid = UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project ID") from None

    if not get_project(session, project_uuid):
        raise HTTPException(status_code=404, detail="Project not found")

    plan_files = db_list_plan_files(session, project_uuid)

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
async def api_get_plan(plan_file_id: str, version: int | None = None) -> dict[str, Any]:
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
async def api_delete_project(project_id: str) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Daemon IPC (PRD Phase 1). When this app is the long-running local process it
# is the single writer: other processes forward their write operations here
# instead of mutating shared state themselves. Operations are looked up in the
# shared service registry, so the daemon and an in-process caller run exactly
# the same code. The token is supplied by the CLI through FLANNER_IPC_TOKEN;
# without it IPC is off.
# ---------------------------------------------------------------------------


def _require_ipc_token(request: Request) -> None:
    expected = os.environ.get(ipc.TOKEN_ENV)
    if not expected:
        raise HTTPException(status_code=503, detail="IPC not enabled")
    supplied = request.headers.get("X-Flanner-Token", "")
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid IPC token")


@app.post("/ipc/call")
async def ipc_call(request: Request) -> JSONResponse:
    """Run one shared write operation on behalf of another process.

    Any non-200 response means the operation did not run, which is what lets
    the caller safely fall back to executing locally. A failure *inside* an
    operation is therefore reported as a 200 carrying an error payload, never
    as a 500 that a caller might retry and thereby apply twice.
    """
    _require_ipc_token(request)
    ensure_db()
    body = await request.json()
    op = str(body.get("op", ""))
    args = body.get("args") or {}
    fn = services.REGISTRY.get(op)
    if fn is None or not isinstance(args, dict):
        raise HTTPException(status_code=422, detail=f"Unknown IPC operation: {op}")
    try:
        return JSONResponse({"result": fn(**args)})
    except TypeError as e:  # bad arguments for this operation
        raise HTTPException(status_code=422, detail=str(e)) from None
    except Exception as e:
        logger.exception("IPC operation %s failed", op)
        return JSONResponse({"result": {"error": True, "message": str(e)}})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)  # local-only tool, no auth layer
