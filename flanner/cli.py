"""
CLI tool for Flanner

Provides command-line interface for managing the Flanner server and projects.
"""

import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import (
    ProjectModel,
    delete_project,
    get_project_by_name,
    get_session,
    init_database,
)
from .database import list_projects as db_list_projects
from .exceptions import FlannerError
from .git_integration import find_git_root, update_gitignore
from .storage import init_storage

console = Console()


def get_mcp_dir() -> Path:
    """Get Flanner data directory (override with FLANNER_HOME)"""
    return Path(os.environ.get("FLANNER_HOME", Path.home() / ".flanner"))


def get_pid_file() -> Path:
    """Get path to PID file"""
    return get_mcp_dir() / "server.pid"


@click.group()
@click.version_option(package_name="flanner")
@click.option("--verbose", is_flag=True, help="Show debug output")
@click.option("--quiet", is_flag=True, help="Only show errors")
def cli(verbose: bool, quiet: bool) -> None:
    """Flanner - Manage plan files for AI assistants"""
    level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.WARNING
    logging.basicConfig(
        level=level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )


@cli.command()
@click.option("--project-root", default=None, help="Project root path")
@click.option("--plan-dir", default=".plans", help="Plan directory name")
@click.option("--skip-claude", is_flag=True, help="Skip Claude Code integration")
@click.option(
    "--force-new-project", is_flag=True, help="Force create new project even if one exists"
)
def init(
    project_root: str | None, plan_dir: str, skip_claude: bool, force_new_project: bool
) -> None:
    """Initialize Flanner"""
    mcp_dir = get_mcp_dir()

    # Initialize storage
    init_storage(str(mcp_dir))

    # Initialize database
    db_path = mcp_dir / "data.db"
    init_database(str(db_path))

    console.print(f"OK Initialized Flanner at {mcp_dir}", style="green")
    console.print(f"OK Database created at {db_path}", style="green")

    # Register with Claude Desktop (unless skipped). Claude Code (the CLI) is
    # handled separately via .mcp.json in _setup_agent_integration below.
    if not skip_claude:
        console.print("\n[MCP] Registering MCP server with Claude Desktop...", style="cyan")
        from .claude_integration import auto_register_on_init

        success, message = auto_register_on_init()

        if success:
            console.print(f"OK {message}", style="green")
            console.print(
                "  You may need to restart Claude Code for changes to take effect", style="yellow"
            )
        else:
            console.print(f"WARN {message}", style="yellow")
            console.print(
                "  You can manually register later with: flanner register", style="white"
            )

    # If project root specified or can be auto-detected, handle project setup
    if project_root or (project_root := find_git_root(os.getcwd())):
        console.print(f"\nOK Detected git repository at: {project_root}", style="green")

        # Check if this project_root already has a project in database
        try:
            from .database import get_project_by_root

            session = get_session()
            existing_project = get_project_by_root(session, project_root)

            if existing_project and not force_new_project:
                console.print(f"OK Project already exists: {existing_project.name}", style="green")
                console.print(
                    f"  Plan directory: {existing_project.plan_directory}", style="white"
                )
                console.print(f"  Plan files: {len(existing_project.plan_files)}", style="white")
                console.print(
                    "\n  Tip: MCP server registration still completed above.", style="cyan"
                )
                console.print(
                    "  You can run 'flanner init' anytime to ensure everything is set up!",
                    style="cyan",
                )
            else:
                if existing_project and force_new_project:
                    console.print(
                        f"WARN Project '{existing_project.name}' already exists here",
                        style="yellow",
                    )
                    console.print(
                        "  Creating a new project anyway (--force-new-project)", style="yellow"
                    )

                # Prompt for project name
                project_name = click.prompt("Enter project name", default=Path(project_root).name)

                # Create project
                from .server import create_project_tool

                result = create_project_tool(
                    name=project_name, project_root=project_root, plan_directory=plan_dir
                )

                if result.get("error"):
                    console.print(f"ERROR Error: {result['message']}", style="red")
                else:
                    console.print(f"OK Created project: {project_name}", style="green")
                    console.print(f"OK Plan directory: {result['full_plan_path']}", style="green")
                    if result.get("gitignore_updated"):
                        console.print("OK Updated .gitignore to exclude plan files", style="green")
                    else:
                        console.print("OK .gitignore already excludes plan files", style="green")
        except Exception as e:
            console.print(f"WARN Could not check for existing project: {e}", style="yellow")
            console.print("  Skipping project creation to be safe", style="yellow")

        _setup_agent_integration(project_root)


def _setup_agent_integration(project_root: str) -> None:
    """Wire the CLAUDE.md block, guard-write hook, and skill for this repo."""
    from .agent_hooks import wire_agent_integration
    from .database import get_project_by_root

    try:
        project = get_project_by_root(get_session(), project_root)
        if not project:
            return
        console.print("\n[Agent] Setting up coding-agent integration...", style="cyan")
        for item in wire_agent_integration(project_root, project):
            console.print(f"OK Installed {item}", style="green")
    except Exception as e:
        console.print(f"WARN Could not set up agent integration: {e}", style="yellow")


@cli.group()
def hook() -> None:
    """Claude Code hook entry points (invoked by the harness, not by hand)."""


@hook.command("guard-write")
def guard_write() -> None:
    """PreToolUse guard: deny raw Writes into a flanner-managed plan directory."""
    from .agent_hooks import run_guard_write

    raw = sys.stdin.read()
    try:
        init_database()  # fresh hook process has no session yet
        output = run_guard_write(raw, get_session())
    except Exception:
        output = ""  # fail open: never block a write because the guard broke
    if output:
        click.echo(output)


@cli.command()
@click.option(
    "--port",
    type=int,
    default=lambda: int(os.environ.get("FLANNER_WEB_PORT", "8080")),
    help="Web server port (env: FLANNER_WEB_PORT)",
)
def start(port: int) -> None:
    """Start the MCP server"""
    pid_file = get_pid_file()

    # Check if already running
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            # Check if process is actually running
            os.kill(pid, 0)  # Doesn't kill, just checks if process exists
            console.print("ERROR Server is already running", style="yellow")
            console.print(f"  PID: {pid}", style="yellow")
            return
        except (OSError, ValueError):
            # Process not running, remove stale PID file
            pid_file.unlink()

    console.print("Starting MCP Server...", style="blue")

    # Note: In production, you would start the server in background
    # For now, we'll just show instructions
    console.print("\n" + "=" * 60, style="cyan")
    console.print("MCP SERVER READY", style="cyan bold")
    console.print("=" * 60 + "\n", style="cyan")

    console.print("Add to your Claude Code MCP settings:\n", style="white")

    import json

    from .claude_integration import get_local_server_config

    snippet = {"mcpServers": {"flanner": get_local_server_config()}}
    console.print(json.dumps(snippet, indent=2), style="yellow")

    console.print("\nOr run the server directly:", style="white")
    console.print("  flanner-mcp\n", style="yellow")


@cli.command()
def stop() -> None:
    """Stop the MCP server"""
    pid_file = get_pid_file()

    if not pid_file.exists():
        console.print("ERROR Server is not running", style="yellow")
        return

    try:
        pid = int(pid_file.read_text())
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink()
        console.print("OK Server stopped", style="green")
    except (OSError, ValueError) as e:
        console.print(f"ERROR Error stopping server: {e}", style="red")
        # Remove stale PID file
        if pid_file.exists():
            pid_file.unlink()


@cli.command()
def status() -> None:
    """Show server status"""
    pid_file = get_pid_file()
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    console.print("\n" + "=" * 60, style="cyan")
    console.print("FLANNER STATUS", style="cyan bold")
    console.print("=" * 60 + "\n", style="cyan")

    # Server status
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            os.kill(pid, 0)
            console.print("Server Status: Running", style="green")
            console.print(f"PID: {pid}", style="green")
        except (OSError, ValueError):
            console.print("Server Status: Stopped", style="yellow")
            pid_file.unlink()
    else:
        console.print("Server Status: Stopped", style="yellow")

    # Database status
    if db_path.exists():
        console.print(f"\nDatabase: {db_path}", style="white")

        # Get project count
        try:
            init_database(str(db_path))
            session = get_session()
            projects = db_list_projects(session)
            console.print(f"Projects: {len(projects)}", style="white")

            # Show total plan files
            total_plans = sum(len(p.plan_files) for p in projects)
            console.print(f"Total Plan Files: {total_plans}", style="white")
        except (FlannerError, SQLAlchemyError):
            console.print("Database: Unable to read", style="red")
    else:
        console.print("\nDatabase: Not initialized", style="yellow")

    # Claude Code Integration Status
    console.print("\n" + "-" * 60, style="cyan")
    console.print("CLAUDE CODE INTEGRATION", style="cyan bold")
    console.print("-" * 60, style="cyan")

    from .claude_integration import check_server_status

    claude_status = check_server_status()

    console.print(f"\nConfig Path: {claude_status['config_path']}", style="white")

    if claude_status["registered"]:
        if claude_status["config_valid"]:
            console.print("MCP Server: Registered & Valid", style="green")
        else:
            console.print("MCP Server: Registered (config outdated)", style="yellow")
            console.print(f"  {claude_status['message']}", style="yellow")
    else:
        console.print("MCP Server: Not Registered", style="yellow")

    if claude_status.get("action_needed"):
        console.print(f"\nAction needed: {claude_status['action_needed']}", style="yellow")

    console.print()


@cli.command("list")
@click.option("--project", default=None, help="Project name")
@click.option(
    "--output",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
def list_cmd(project: str | None, output: str) -> None:
    """List all projects or plan files"""
    import json as json_module

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    if project:
        # List plan files for a specific project
        proj = get_project_by_name(session, project)
        if not proj:
            console.print(f"ERROR Project '{project}' not found", style="red")
            raise SystemExit(1)

        if output == "json":
            click.echo(
                json_module.dumps(
                    [
                        {
                            "id": str(pf.id),
                            "name": pf.name,
                            "version": pf.current_version,
                            "updated_at": pf.updated_at.isoformat() if pf.updated_at else None,
                        }
                        for pf in proj.plan_files
                    ]
                )
            )
            return

        console.print(f"\nPlan files for project: {project}\n", style="cyan bold")

        if not proj.plan_files:
            console.print("No plan files yet.", style="yellow")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID", style="dim")
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Updated")

        for pf in proj.plan_files:
            table.add_row(
                str(pf.id),
                pf.name,
                f"v{pf.current_version}",
                pf.updated_at.strftime("%Y-%m-%d %H:%M") if pf.updated_at else "N/A",
            )

        console.print(table)
    else:
        # List all projects
        projects = db_list_projects(session)

        if output == "json":
            click.echo(
                json_module.dumps(
                    [
                        {
                            "id": str(p.id),
                            "name": p.name,
                            "plan_directory": p.plan_directory,
                            "plan_files": len(p.plan_files),
                            "created_at": p.created_at.isoformat() if p.created_at else None,
                        }
                        for p in projects
                    ]
                )
            )
            return

        if not projects:
            console.print("No projects yet. Run 'flanner init' to create one.", style="yellow")
            return

        console.print("\nProjects:\n", style="cyan bold")

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID", style="dim")
        table.add_column("Name")
        table.add_column("Plan Dir")
        table.add_column("# Plans")
        table.add_column("Created")

        for p in projects:
            table.add_row(
                str(p.id),
                p.name,
                p.plan_directory,
                str(len(p.plan_files)),
                p.created_at.strftime("%Y-%m-%d") if p.created_at else "N/A",
            )

        console.print(table)
        console.print()


@cli.command()
@click.argument("project_name")
@click.option("--project-root", default=None, help="New project root path")
@click.option("--plan-dir", default=None, help="New plan directory")
@click.option("--auto-gitignore", default=None, type=bool, help="Enable/disable auto .gitignore")
def config(
    project_name: str, project_root: str | None, plan_dir: str | None, auto_gitignore: bool | None
) -> None:
    """Configure a project's settings"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        raise SystemExit(1)

    # Use server tool to update
    from .server import configure_project_tool

    result = configure_project_tool(
        # str(): the tool expects a string UUID; passing the raw uuid.UUID crashed in UUID()
        project_id=str(project.id),
        project_root=project_root,
        plan_directory=plan_dir,
        auto_gitignore=auto_gitignore,
    )

    if result.get("error"):
        console.print(f"ERROR Error: {result['message']}", style="red")
    else:
        console.print(f"OK Project '{project_name}' updated", style="green")
        if plan_dir:
            console.print(f"  Plan directory: {plan_dir}", style="white")
        if auto_gitignore is not None:
            console.print(f"  Auto .gitignore: {auto_gitignore}", style="white")


@cli.command()
@click.argument("project_name")
def setup_gitignore(project_name: str) -> None:
    """Manually update .gitignore for a project"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        raise SystemExit(1)

    if not project.project_root:
        console.print("ERROR Project has no project_root configured", style="red")
        raise SystemExit(1)

    # Update .gitignore
    pattern = project.plan_directory.rstrip("/") + "/"
    updated = update_gitignore(project.project_root, pattern, comment="Flanner")

    if updated:
        console.print(f"OK Added '{pattern}' to .gitignore", style="green")
    else:
        console.print(f"  '{pattern}' already in .gitignore", style="yellow")


@cli.command()
@click.argument("project_name")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def delete(project_name: str, force: bool) -> None:
    """Delete a project and all its plan files"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        raise SystemExit(1)

    # Show project info
    console.print("\nProject to delete:", style="yellow")
    console.print(f"  Name: {project.name}", style="white")
    console.print(f"  Root: {project.project_root}", style="white")
    console.print(f"  Plan files: {len(project.plan_files)}", style="white")

    # Confirm deletion
    if not force:
        if not click.confirm(
            "\nAre you sure you want to delete this project? This cannot be undone."
        ):
            console.print("Cancelled", style="yellow")
            return

    # Delete project (cascade deletes plan files and versions)
    if delete_project(session, project.id):
        console.print(f"\nOK Project '{project_name}' deleted successfully", style="green")
        console.print(
            "  Note: Plan files on disk were NOT deleted. You may want to manually remove:",
            style="cyan",
        )
        if project.project_root:
            console.print(f"  {project.project_root}/{project.plan_directory}/", style="cyan")
    else:
        console.print("ERROR Failed to delete project", style="red")


def _port_in_use(host: str, port: int) -> bool:
    """True if binding (host, port) fails because something already holds it."""
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


def _open_browser_when_ready(host: str, port: int) -> None:
    """Open the browser once the server accepts connections (background thread)."""
    import socket
    import threading
    import time
    import webbrowser

    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host  # noqa: S104 (comparison, not a bind)

    def _wait_and_open() -> None:
        for _ in range(100):  # up to ~10s
            with socket.socket() as probe:
                if probe.connect_ex((target, port)) == 0:
                    break
            time.sleep(0.1)
        webbrowser.open(f"http://{target}:{port}")

    threading.Thread(target=_wait_and_open, daemon=True).start()


@cli.command()
@click.option(
    "--port",
    type=int,
    default=lambda: int(os.environ.get("FLANNER_WEB_PORT", "8080")),
    help="Web server port (env: FLANNER_WEB_PORT)",
)
@click.option("--host", default="127.0.0.1", help="Web server host")
@click.option("--open-browser", is_flag=True, help="Open browser automatically")
def web(port: int, host: str, open_browser: bool) -> None:
    """Launch web interface"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"WARN Binding {host} exposes the web UI beyond localhost. It has no "
            "authentication; anyone who can reach this address can read and edit "
            "your plans. Use 127.0.0.1 unless you have put auth in front of it.",
            style="yellow",
        )

    if _port_in_use(host, port):
        console.print(f"ERROR Port {port} is already in use on {host}.", style="red")
        console.print("  Start on a different port, for example:", style="yellow")
        console.print(f"    flanner web --port {port + 1}", style="white")
        console.print("  Or set a default port for future runs:", style="yellow")
        console.print("    PowerShell:  $env:FLANNER_WEB_PORT = '8090'", style="white")
        console.print("    bash/zsh:    export FLANNER_WEB_PORT=8090", style="white")
        console.print(
            f"  (something may already be serving at http://{host}:{port})", style="white"
        )
        raise SystemExit(1)

    console.print("\nStarting Flanner Web Interface...\n", style="cyan bold")
    console.print(f"  Server:    http://{host}:{port}", style="green")
    console.print(f"  Dashboard: http://{host}:{port}/", style="green")
    console.print(f"  Projects:  http://{host}:{port}/projects", style="green")
    console.print("\n  Press CTRL+C to stop the server\n", style="yellow")

    # Open the browser once the server is actually accepting connections, on a
    # background thread so it never delays or blocks startup.
    if open_browser:
        _open_browser_when_ready(host, port)

    # Start web server. While it runs it is the local write daemon: advertise
    # it (port + token) so the stdio MCP server forwards writes here instead of
    # mutating shared state from a second process (PRD Phase 1).
    from . import ipc

    token = ipc.new_token()
    os.environ[ipc.TOKEN_ENV] = token
    ipc.write_daemon_info(port, token)
    try:
        import uvicorn

        from .web import app

        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        console.print("\n\nOK Web server stopped", style="green")
    except Exception as e:
        console.print(f"\nERROR Error starting web server: {e}", style="red")
    finally:
        ipc.clear_daemon_info()


@cli.command()
def setup() -> None:
    """Make flanner available in every project (global, one-time).

    Registers the MCP server for Claude Desktop and Claude Code (user scope),
    and adds a short nudge to ~/.claude/CLAUDE.md so Claude offers to adopt a
    repo (initialize_project_tool / flanner init) when you write a plan doc in a
    project that is not yet flanner-managed.
    """
    console.print("\n[Setup] Making flanner available across all projects...\n", style="cyan bold")

    # 1. Claude Desktop (single global config).
    from .claude_integration import auto_register_on_init

    ok, message = auto_register_on_init()
    console.print(
        f"{'OK' if ok else 'WARN'} Claude Desktop: {message}", style="green" if ok else "yellow"
    )

    # 2. Claude Code, user scope (via its own CLI so its config is written safely).
    import shutil
    import subprocess

    claude_bin = shutil.which("claude")
    if claude_bin:
        proc = subprocess.run(  # noqa: S603 (fixed argv, no shell, no untrusted input)
            [claude_bin, "mcp", "add", "-s", "user", "flanner", "--", "flanner-mcp"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            console.print("OK Claude Code: registered flanner-mcp at user scope", style="green")
        else:
            detail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or [""]
            console.print(f"WARN Claude Code: {detail[0]}", style="yellow")
            console.print(
                "  Add it manually:  claude mcp add -s user flanner -- flanner-mcp", style="white"
            )
    else:
        console.print(
            "- Claude Code CLI not found. To use flanner there globally, run:", style="yellow"
        )
        console.print("    claude mcp add -s user flanner -- flanner-mcp", style="white")

    # 3. Global adoption nudge.
    from .agent_hooks import upsert_global_nudge

    changed = upsert_global_nudge()
    where = str(Path.home() / ".claude" / "CLAUDE.md")
    console.print(
        f"OK Global nudge {'added to' if changed else 'already in'} {where}", style="green"
    )

    console.print(
        "\nRestart Claude Desktop and start a fresh Claude Code session to pick up the changes.",
        style="cyan",
    )


@cli.command()
@click.option("--force", is_flag=True, help="Force update if already registered")
@click.option("--type", "server_type", default="local", help="Server type: local or cloud")
@click.option("--url", default=None, help="Server URL (for cloud type)")
@click.option("--api-key", default=None, help="API key (for cloud type)")
def register(force: bool, server_type: str, url: str | None, api_key: str | None) -> None:
    """Register MCP server with Claude Code"""
    console.print("\n[MCP] Registering MCP server with Claude Code...\n", style="cyan")

    from .claude_integration import get_claude_config_path, register_mcp_server

    # Check if Claude Code config exists
    config_path = get_claude_config_path()
    if not config_path:
        console.print("ERROR Could not find Claude Code configuration path", style="red")
        console.print("  Please ensure Claude Code is installed", style="yellow")
        return

    # Validate cloud server parameters
    if server_type == "cloud" and not url:
        console.print("ERROR Cloud server requires --url parameter", style="red")
        raise SystemExit(1)

    # Register server
    success, message = register_mcp_server(
        server_type=server_type, server_url=url, api_key=api_key, force=force
    )

    if success:
        console.print(f"OK {message}", style="green")
        console.print(f"\nConfig location: {config_path}", style="white")

        if server_type == "local":
            project_dir = Path(__file__).resolve().parent.parent
            console.print(f"Project directory: {project_dir}", style="white")

        console.print("\nNext steps:", style="cyan")
        console.print("  1. Restart Claude Code to load the new MCP server", style="white")
        console.print(
            "  2. Check Claude Code's MCP settings to verify registration", style="white"
        )
        console.print(
            "  3. Test by asking Claude to list projects or create a plan", style="white"
        )
    else:
        console.print(f"ERROR {message}", style="red")


@cli.command()
def unregister() -> None:
    """Unregister MCP server from Claude Code"""
    console.print("\n[MCP] Unregistering MCP server from Claude Code...\n", style="cyan")

    from .claude_integration import unregister_mcp_server

    # Confirm
    if not click.confirm("Are you sure you want to unregister the MCP server?"):
        console.print("Cancelled", style="yellow")
        return

    success, message = unregister_mcp_server()

    if success:
        console.print(f"OK {message}", style="green")
        console.print("\n  Restart Claude Code for changes to take effect", style="yellow")
    else:
        console.print(f"ERROR {message}", style="red")


@cli.command()
def claude_info() -> None:
    """Show Claude Code integration information"""
    console.print("\n" + "=" * 60, style="cyan")
    console.print("CLAUDE CODE INTEGRATION INFO", style="cyan bold")
    console.print("=" * 60 + "\n", style="cyan")

    from .claude_integration import get_claude_config_info, print_registration_instructions

    info = get_claude_config_info()

    console.print(f"Configuration Path: {info['config_path']}", style="white")
    console.print(f"Config File Exists: {info['config_exists']}", style="white")
    console.print(
        f"Server Registered: {info['server_registered']}",
        style="green" if info["server_registered"] else "yellow",
    )
    console.print(f"Total MCP Servers: {info['total_servers']}", style="white")

    if info["our_server_config"]:
        console.print("\nCurrent Configuration:", style="cyan")
        import json

        console.print(json.dumps(info["our_server_config"], indent=2), style="white")
    else:
        console.print("\nNot registered. Use 'flanner register' to add.", style="yellow")
        print_registration_instructions()


def _sync_file(session: Session, proj: ProjectModel, file_path: Path, dry_run: bool) -> str:
    """Import one plan file into the database. Returns 'imported', 'skipped', or 'error'."""
    from uuid import UUID

    from .database import PlanFileModel, VersionModel, get_plan_file, get_version
    from .frontmatter import parse_frontmatter, validate_frontmatter
    from .utils import hash_content, utcnow

    file_name = file_path.name
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    fm_data, body = parse_frontmatter(content)

    if not fm_data.get("mcp_plan_file"):
        console.print(f"  SKIP {file_name} - Not an MCP plan file", style="yellow")
        return "skipped"
    if not validate_frontmatter(fm_data):
        console.print(f"  ERROR {file_name} - Invalid frontmatter", style="red")
        return "error"

    plan_file_id = UUID(fm_data["plan_file_id"])
    plan_name = fm_data["plan_name"]
    version = fm_data["version"]
    created_by = fm_data.get("created_by", "unknown")

    existing = get_plan_file(session, plan_file_id)
    if existing:
        old_version = existing.current_version
        if version <= old_version:
            console.print(
                f"  SKIP {file_name} - Version {version} already in database "
                f"(current: v{old_version})",
                style="white",
            )
            return "skipped"
        if dry_run:
            console.print(
                f"  WOULD UPDATE {file_name} (plan: {plan_name}, v{old_version} -> v{version})",
                style="green",
            )
            return "imported"
        if get_version(session, plan_file_id, version):
            console.print(f"  SKIP {file_name} - Version {version} already exists", style="white")
            return "skipped"

        session.add(
            VersionModel(
                plan_file_id=plan_file_id,
                version=version,
                file_path=str(file_path),
                content_hash=hash_content(body),
                created_by=created_by,
                created_at=utcnow(),
                notes=f"Imported version {version}",
            )
        )
        existing.current_version = version
        existing.updated_at = utcnow()
        session.commit()
        console.print(
            f"  OK UPDATED {file_name} (plan: {plan_name}, v{old_version} -> v{version})",
            style="green",
        )
        return "imported"

    if dry_run:
        console.print(
            f"  WOULD IMPORT {file_name} (plan: {plan_name}, version: {version})", style="green"
        )
        return "imported"

    # Create PlanFileModel directly (it has a specific UUID from frontmatter)
    session.add(
        PlanFileModel(
            id=plan_file_id,
            project_id=proj.id,
            name=plan_name,
            description=f"Imported from {file_name}",
            current_version=version,
            auto_version=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    session.add(
        VersionModel(
            plan_file_id=plan_file_id,
            version=version,
            file_path=str(file_path),
            content_hash=hash_content(body),
            created_by=created_by,
            created_at=utcnow(),
            notes=f"Imported version {version}",
        )
    )
    session.commit()
    console.print(
        f"  OK IMPORTED {file_name} (plan: {plan_name}, version: {version})", style="green"
    )
    return "imported"


def _sync_project(
    session: Session, proj: ProjectModel, dry_run: bool, totals: dict[str, int]
) -> None:
    """Sync every plan file in one project's plan directory."""
    console.print(f"\nProject: {proj.name}", style="cyan bold")
    console.print(f"Plan directory: {proj.project_root}/{proj.plan_directory}", style="white")

    if not proj.project_root:
        # Previously crashed with TypeError; skip the misconfigured project instead
        console.print("  Project has no project_root configured", style="yellow")
        return

    plan_dir = Path(proj.project_root) / proj.plan_directory
    if not plan_dir.exists():
        console.print("  Plan directory doesn't exist yet", style="yellow")
        return

    md_files = sorted(plan_dir.rglob("*.md"))  # recurse: plan names may be subpaths
    if not md_files:
        console.print("  No plan files found", style="yellow")
        return

    console.print(f"  Found {len(md_files)} file(s)\n", style="white")
    for file_path in md_files:
        totals["scanned"] += 1
        try:
            outcome = _sync_file(session, proj, file_path, dry_run)
        except Exception as e:
            session.rollback()
            console.print(f"  ERROR {file_path.name} - {e}", style="red")
            outcome = "error"
        totals[outcome] += 1


@cli.command()
@click.option(
    "--project", default=None, help="Project name to sync (syncs all projects if not specified)"
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be imported without actually importing"
)
def sync(project: str | None, dry_run: bool) -> None:
    """Scan .plans directory and import existing plan files into database"""
    console.print("\n" + "=" * 60, style="cyan")
    console.print("SYNC PLAN FILES", style="cyan bold")
    console.print("=" * 60 + "\n", style="cyan")

    if dry_run:
        console.print("[DRY RUN MODE - No changes will be made]\n", style="yellow")

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"
    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    init_storage(str(mcp_dir))
    session = get_session()

    if project:
        project_model = get_project_by_name(session, project)
        if not project_model:
            console.print(f"ERROR Project '{project}' not found", style="red")
            raise SystemExit(1)
        projects = [project_model]
    else:
        projects = db_list_projects(session)

    if not projects:
        console.print(
            "No projects found. Create a project first with 'flanner init'", style="yellow"
        )
        return

    totals = {"scanned": 0, "imported": 0, "skipped": 0, "error": 0}
    for proj in projects:
        _sync_project(session, proj, dry_run, totals)

    console.print("\n" + "=" * 60, style="cyan")
    console.print("SYNC SUMMARY", style="cyan bold")
    console.print("=" * 60, style="cyan")
    console.print(f"Files scanned: {totals['scanned']}", style="white")
    console.print(f"Files imported: {totals['imported']}", style="green")
    console.print(f"Files skipped: {totals['skipped']}", style="yellow")
    console.print(f"Errors: {totals['error']}", style="red")
    console.print()

    if dry_run and totals["imported"] > 0:
        console.print("Run without --dry-run to actually import the files", style="cyan")


_FINDING_STYLES = {
    "missing_file": "red",
    "hash_mismatch": "yellow",
    "unreadable_file": "red",
    "orphan_file": "cyan",
    "unknown_plan": "yellow",
    "stale_current_version": "cyan",
    "no_project_root": "red",
}


@cli.command()
@click.option("--project", default=None, help="Project name")
@click.option("--repair", is_flag=True, help="Adopt orphan files and fix stale version counters")
@click.option(
    "--output",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
def doctor(project: str | None, repair: bool, output: str) -> None:
    """Check the catalog against the plan files on disk"""
    import json as json_module

    from .reconcile import reconcile_project

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        console.print(
            "ERROR Project not found. Run from inside a project or pass --project.", style="red"
        )
        raise SystemExit(1)

    findings = reconcile_project(session, proj, repair=repair)

    if output == "json":
        click.echo(
            json_module.dumps(
                [
                    {
                        "kind": f.kind,
                        "plan": f.plan,
                        "detail": f.detail,
                        "path": f.path,
                        "repairable": f.repairable,
                    }
                    for f in findings
                ],
                indent=2,
            )
        )
        return

    if not findings:
        console.print(f"OK Catalog and plan files agree for '{proj.name}'", style="green")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Issue")
    table.add_column("Plan")
    table.add_column("Detail")
    for finding in findings:
        style = _FINDING_STYLES.get(finding.kind, "white")
        table.add_row(f"[{style}]{finding.kind}[/{style}]", finding.plan, finding.detail)
    console.print(table)

    if repair:
        fixed = sum(1 for f in findings if f.repairable)
        console.print(f"\nRepaired {fixed} of {len(findings)} findings.", style="green")
        remaining = [f for f in findings if not f.repairable]
        if remaining:
            console.print(
                f"{len(remaining)} need a human: files are missing or were edited outside "
                "flanner, so no automatic fix is safe.",
                style="yellow",
            )
    elif any(f.repairable for f in findings):
        console.print(
            "\nRun 'flanner doctor --repair' to fix the repairable ones.", style="yellow"
        )


_FRESHNESS_STYLES = {"fresh": "green", "aging": "yellow", "suspect": "dark_orange", "stale": "red"}


@cli.command()
@click.argument("plan_name", required=False)
@click.option("--project", default=None, help="Project name")
@click.option(
    "--output",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
def freshness(plan_name: str | None, project: str | None, output: str) -> None:
    """Freshness status for plans, with the evidence behind it"""
    import json as json_module

    from .database import get_version
    from .database import list_plan_files as db_list_plan_files
    from .freshness import compute_freshness
    from .storage import load_plan_file

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        console.print(
            "ERROR Project not found. Run from inside a project or pass --project.", style="red"
        )
        raise SystemExit(1)

    plans = db_list_plan_files(session, proj.id)
    if plan_name:
        plans = [p for p in plans if p.name == plan_name]
        if not plans:
            console.print(f"ERROR Plan '{plan_name}' not found in '{proj.name}'", style="red")
            raise SystemExit(1)
    if not plans:
        console.print(f"No plan files found for project '{proj.name}'", style="yellow")
        return
    if not proj.project_root:
        console.print(f"ERROR Project '{proj.name}' has no project_root configured", style="red")
        raise SystemExit(1)

    results: list[tuple[Any, Any, dict[str, Any]]] = []
    for plan in plans:
        version_obj = get_version(session, plan.id, None)
        if not version_obj:
            continue
        try:
            _, body = load_plan_file(version_obj.file_path)
        except FileNotFoundError:
            results.append(
                (plan, version_obj, {"status": "stale", "reasons": ["plan file missing on disk"]})
            )
            continue
        evidence = compute_freshness(proj.project_root, body, version_obj.created_at)
        results.append((plan, version_obj, evidence))

    if output == "json":
        click.echo(
            json_module.dumps(
                [{"plan": p.name, "version": v.version, **e} for p, v, e in results],
                indent=2,
            )
        )
        return

    if plan_name and len(results) == 1:
        plan, version_obj, evidence = results[0]
        style = _FRESHNESS_STYLES.get(evidence["status"], "white")
        console.print(f"\n{plan.name} v{version_obj.version}: ", style="bold", end="")
        console.print(evidence["status"], style=f"bold {style}")
        for reason in evidence["reasons"]:
            console.print(f"  - {reason}")
        for key in (
            "anchored_at_commit",
            "referenced_paths",
            "referenced_symbols",
            "invalid_refs",
            "commits_since_anchor",
            "churn_scope",
            "age_days",
        ):
            if evidence.get(key) not in (None, []):
                console.print(f"  {key}: {evidence[key]}", style="dim")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Plan")
    table.add_column("Ver")
    table.add_column("Status")
    table.add_column("Evidence")
    for plan, version_obj, evidence in results:
        style = _FRESHNESS_STYLES.get(evidence["status"], "white")
        table.add_row(
            plan.name,
            f"v{version_obj.version}",
            f"[{style}]{evidence['status']}[/{style}]",
            evidence["reasons"][0] if evidence["reasons"] else "",
        )
    console.print(table)


@cli.group()
def jira() -> None:
    """JIRA integration commands"""
    pass


@jira.command("config")
@click.argument("project_name")
@click.option("--url", required=True, help="JIRA base URL (e.g., https://company.atlassian.net)")
@click.option("--project-key", default=None, help="Default JIRA project key (e.g., PROJ)")
def jira_config(project_name: str, url: str, project_key: str | None) -> None:
    """Configure JIRA integration for a project"""
    from .database import create_jira_config
    from .jira_utils import is_valid_jira_url, normalize_jira_url

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    # Validate JIRA URL
    if not is_valid_jira_url(url):
        console.print(f"ERROR Invalid JIRA URL format: {url}", style="red")
        console.print("  Expected format: https://company.atlassian.net", style="yellow")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        raise SystemExit(1)

    # Create or update JIRA config
    try:
        normalized_url = normalize_jira_url(url)
        jira_config = create_jira_config(session, project.id, normalized_url, project_key)

        console.print(
            f"\nOK JIRA configuration updated for project '{project_name}'", style="green"
        )
        console.print(f"  JIRA URL: {jira_config.jira_url}", style="white")
        if jira_config.jira_project_key:
            console.print(f"  Default Project Key: {jira_config.jira_project_key}", style="white")
    except Exception as e:
        console.print(f"ERROR Failed to configure JIRA: {e}", style="red")


@jira.command("link")
@click.argument("plan_name")
@click.option("--issue", required=True, help="JIRA issue key (e.g., PROJ-123)")
@click.option("--type", "issue_type", default=None, help="Issue type (Epic, Story, Task, etc.)")
@click.option("--notes", default=None, help="Notes about the link")
@click.option(
    "--project", default=None, help="Project name (uses current directory if not specified)"
)
def jira_link(
    plan_name: str, issue: str, issue_type: str | None, notes: str | None, project: str | None
) -> None:
    """Link a plan file to a JIRA issue"""
    from .database import create_jira_link, get_jira_config
    from .jira_utils import format_jira_issue_key, generate_jira_issue_url, is_valid_jira_issue_key

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    # Validate issue key
    formatted_issue = format_jira_issue_key(issue)
    if not is_valid_jira_issue_key(formatted_issue):
        console.print(f"ERROR Invalid JIRA issue key format: {issue}", style="red")
        console.print(
            "  Expected format: PROJECT-123 (uppercase letters, dash, numbers)", style="yellow"
        )
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get project
    if project:
        proj = get_project_by_name(session, project)
    else:
        # Try to find project from current directory
        from .database import get_project_by_root

        git_root = find_git_root(os.getcwd())
        if git_root:
            proj = get_project_by_root(session, git_root)
        else:
            proj = None

    if not proj:
        console.print(
            "ERROR Project not found. Specify --project or run from project directory", style="red"
        )
        raise SystemExit(1)

    # Find plan file
    plan_file = None
    for pf in proj.plan_files:
        if pf.name == plan_name:
            plan_file = pf
            break

    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found in project '{proj.name}'", style="red")
        console.print(
            f"  Available plans: {', '.join([p.name for p in proj.plan_files])}", style="yellow"
        )
        raise SystemExit(1)

    # Create link
    try:
        create_jira_link(session, plan_file.id, formatted_issue, issue_type, notes)

        console.print(f"\nOK Linked '{plan_name}' to {formatted_issue}", style="green")

        # Show URL if JIRA config exists
        jira_config = get_jira_config(session, proj.id)
        if jira_config:
            url = generate_jira_issue_url(jira_config.jira_url, formatted_issue)
            console.print(f"  URL: {url}", style="cyan")

        if issue_type:
            console.print(f"  Type: {issue_type}", style="white")
        if notes:
            console.print(f"  Notes: {notes}", style="white")

    except ValueError as e:
        console.print(f"ERROR {e}", style="red")
    except Exception as e:
        console.print(f"ERROR Failed to create link: {e}", style="red")


@jira.command("unlink")
@click.argument("plan_name")
@click.option(
    "--issue", default=None, help="JIRA issue key to unlink (unlinks all if not specified)"
)
@click.option("--all", "unlink_all", is_flag=True, help="Unlink all JIRA issues")
@click.option("--project", default=None, help="Project name")
def jira_unlink(plan_name: str, issue: str | None, unlink_all: bool, project: str | None) -> None:
    """Unlink a plan file from JIRA issue(s)"""
    from .database import delete_all_jira_links, delete_jira_link_by_key
    from .jira_utils import format_jira_issue_key

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get project
    if project:
        proj = get_project_by_name(session, project)
    else:
        from .database import get_project_by_root

        git_root = find_git_root(os.getcwd())
        if git_root:
            proj = get_project_by_root(session, git_root)
        else:
            proj = None

    if not proj:
        console.print("ERROR Project not found", style="red")
        raise SystemExit(1)

    # Find plan file
    plan_file = None
    for pf in proj.plan_files:
        if pf.name == plan_name:
            plan_file = pf
            break

    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found", style="red")
        raise SystemExit(1)

    # Unlink
    try:
        if unlink_all or not issue:
            count = delete_all_jira_links(session, plan_file.id)
            if count > 0:
                console.print(
                    f"\nOK Unlinked {count} JIRA issue(s) from '{plan_name}'", style="green"
                )
            else:
                console.print(f"\n No JIRA links found for '{plan_name}'", style="yellow")
        else:
            formatted_issue = format_jira_issue_key(issue)
            deleted = delete_jira_link_by_key(session, plan_file.id, formatted_issue)
            if deleted:
                console.print(f"\nOK Unlinked '{plan_name}' from {formatted_issue}", style="green")
            else:
                console.print(f"\nERROR Link to {formatted_issue} not found", style="yellow")

    except Exception as e:
        console.print(f"ERROR Failed to unlink: {e}", style="red")


@jira.command("links")
@click.option("--project", default=None, help="Project name (shows all projects if not specified)")
def jira_links(project: str | None) -> None:
    """List all JIRA links"""
    from .database import get_jira_config, list_all_jira_links

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get projects
    if project:
        proj = get_project_by_name(session, project)
        if not proj:
            console.print(f"ERROR Project '{project}' not found", style="red")
            raise SystemExit(1)
        projects = [proj]
    else:
        projects = db_list_projects(session)

    if not projects:
        console.print("No projects found", style="yellow")
        return

    for proj in projects:
        links = list_all_jira_links(session, proj.id)

        if not links:
            if len(projects) == 1:
                console.print(f"\nNo JIRA links found for project '{proj.name}'", style="yellow")
            continue

        console.print(f"\n{proj.name}:", style="cyan bold")

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Plan File")
        table.add_column("JIRA Issue")
        table.add_column("Type")
        table.add_column("Created")

        # Get JIRA config for URL generation
        get_jira_config(session, proj.id)

        for link in links:
            issue_key = link["jira_issue_key"]
            table.add_row(
                link["plan_file_name"],
                issue_key,
                link["jira_issue_type"] or "--",
                link["created_at"].strftime("%Y-%m-%d") if link["created_at"] else "N/A",
            )

        console.print(table)


@jira.command("show")
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
def jira_show(plan_name: str, project: str | None) -> None:
    """Show detailed JIRA links for a plan file"""
    from .database import get_jira_config, get_jira_links
    from .jira_utils import generate_jira_issue_url

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)

    init_database(str(db_path))
    session = get_session()

    # Get project
    if project:
        proj = get_project_by_name(session, project)
    else:
        from .database import get_project_by_root

        git_root = find_git_root(os.getcwd())
        if git_root:
            proj = get_project_by_root(session, git_root)
        else:
            proj = None

    if not proj:
        console.print("ERROR Project not found", style="red")
        raise SystemExit(1)

    # Find plan file
    plan_file = None
    for pf in proj.plan_files:
        if pf.name == plan_name:
            plan_file = pf
            break

    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found", style="red")
        raise SystemExit(1)

    # Get links
    links = get_jira_links(session, plan_file.id)

    if not links:
        console.print(f"\nNo JIRA links found for '{plan_name}'", style="yellow")
        return

    console.print(f"\nPlan: {plan_name}", style="cyan bold")
    console.print("JIRA Links:\n", style="white")

    # Get JIRA config
    jira_config = get_jira_config(session, proj.id)

    for link in links:
        console.print(f"  - {link.jira_issue_key}", style="green")
        if link.jira_issue_type:
            console.print(f"    Type: {link.jira_issue_type}", style="white")

        if jira_config:
            url = generate_jira_issue_url(jira_config.jira_url, link.jira_issue_key)
            console.print(f"    URL: {url}", style="cyan")

        if link.notes:
            console.print(f"    Notes: {link.notes}", style="white")

        linked_at = link.created_at.strftime("%Y-%m-%d %H:%M") if link.created_at else "N/A"
        console.print(
            f"    Linked: {linked_at} by {link.created_by}",
            style="dim",
        )
        console.print()


def _require_session() -> Session:
    """Open the flanner database or exit 1 if it isn't initialized."""
    db_path = get_mcp_dir() / "data.db"
    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        raise SystemExit(1)
    init_database(str(db_path))
    return get_session()


def _resolve_project_or_cwd(session: Session, project: str | None) -> ProjectModel | None:
    """Find a project by name, or by the git root of the current directory."""
    if project:
        return get_project_by_name(session, project)
    from .database import get_project_by_root

    git_root = find_git_root(os.getcwd())
    return get_project_by_root(session, git_root) if git_root else None


@cli.group()
def linear() -> None:
    """Linear integration commands"""
    pass


@linear.command("config")
@click.argument("project_name")
@click.option("--workspace", required=True, help="Linear workspace slug or URL (e.g. acme)")
def linear_config(project_name: str, workspace: str) -> None:
    """Configure Linear integration for a project"""
    from .database import create_linear_config
    from .linear_utils import is_valid_linear_workspace, normalize_linear_workspace

    if not is_valid_linear_workspace(workspace):
        console.print(f"ERROR Invalid Linear workspace: {workspace}", style="red")
        console.print("  Expected a slug like 'acme' or a linear.app URL", style="yellow")
        raise SystemExit(1)

    session = _require_session()
    proj = get_project_by_name(session, project_name)
    if not proj:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        raise SystemExit(1)

    try:
        slug = normalize_linear_workspace(workspace)
        config = create_linear_config(session, proj.id, slug)
        console.print(
            f"\nOK Linear configuration updated for project '{project_name}'", style="green"
        )
        console.print(f"  Workspace: {config.workspace}", style="white")
    except Exception as e:
        console.print(f"ERROR Failed to configure Linear: {e}", style="red")


@linear.command("auth")
def linear_auth() -> None:
    """Verify LINEAR_API_KEY and print the MCP server config snippet.

    Reads the key from the environment only (never a flag, never stored), checks
    it against Linear, and shows the config block to give the AI agent the same
    access.
    """
    import json

    from .claude_integration import get_local_server_config
    from .exceptions import LinearError
    from .linear_api import fetch_viewer, get_api_key

    api_key = get_api_key()
    if not api_key:
        console.print("ERROR LINEAR_API_KEY is not set", style="red")
        console.print(
            "  Create a personal API key at https://linear.app/settings/api, then set it:",
            style="yellow",
        )
        console.print('  PowerShell:  setx LINEAR_API_KEY "lin_api_..."', style="white")
        console.print("  bash/zsh:    export LINEAR_API_KEY=lin_api_...", style="white")
        raise SystemExit(1)

    try:
        viewer = fetch_viewer(api_key)
    except LinearError as e:
        console.print(f"ERROR Linear rejected the key: {e}", style="red")
        raise SystemExit(1) from e

    who = viewer.get("name") or "unknown user"
    email = viewer.get("email")
    console.print(f"\nOK Authenticated with Linear as {who}", style="green")
    if email:
        console.print(f"  {email}", style="white")

    console.print(
        "\nYour terminal is ready. To give the AI agent (MCP server) the same access,",
        style="white",
    )
    console.print(
        "add LINEAR_API_KEY to its env in your Claude Code MCP settings:\n", style="white"
    )
    config = get_local_server_config()
    config["env"] = {"LINEAR_API_KEY": "lin_api_...  (paste your key)"}
    console.print(json.dumps({"mcpServers": {"flanner": config}}, indent=2), style="yellow")


@linear.command("link")
@click.argument("plan_name")
@click.option("--issue", required=True, help="Linear issue id (e.g., ENG-123)")
@click.option("--notes", default=None, help="Notes about the link")
@click.option("--project", default=None, help="Project name (uses current directory if omitted)")
@click.option("--no-verify", "no_verify", is_flag=True, help="Skip Linear API verification")
@click.option("--attach-url", default=None, help="URL to attach to the Linear issue")
def linear_link_cmd(
    plan_name: str,
    issue: str,
    notes: str | None,
    project: str | None,
    no_verify: bool,
    attach_url: str | None,
) -> None:
    """Link a plan file to a Linear issue.

    With LINEAR_API_KEY set, the issue is verified and its title/state cached
    (unless --no-verify). A missing issue aborts; a network error links anyway.
    """
    from .database import create_linear_link, get_linear_config
    from .exceptions import LinearError
    from .linear_api import attach_url_to_issue, fetch_issue_by_identifier, get_api_key
    from .linear_utils import (
        format_linear_issue_id,
        generate_linear_issue_url,
        is_valid_linear_issue_id,
    )

    issue_id = format_linear_issue_id(issue)
    if not is_valid_linear_issue_id(issue_id):
        console.print(f"ERROR Invalid Linear issue id: {issue}", style="red")
        console.print("  Expected format: ENG-123 (team key, dash, number)", style="yellow")
        raise SystemExit(1)

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        console.print(
            "ERROR Project not found. Specify --project or run from project directory", style="red"
        )
        raise SystemExit(1)

    plan_file = next((pf for pf in proj.plan_files if pf.name == plan_name), None)
    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found in project '{proj.name}'", style="red")
        raise SystemExit(1)

    api_key = get_api_key()
    issue_title: str | None = None
    issue_state: str | None = None
    if not no_verify and api_key:
        try:
            fetched = fetch_issue_by_identifier(issue_id, api_key)
            if fetched is None:
                console.print(f"ERROR Linear issue {issue_id} not found", style="red")
                raise SystemExit(1)
            issue_title = fetched["title"]
            issue_state = fetched["state"]
            if attach_url and fetched.get("id"):
                attach_url_to_issue(fetched["id"], attach_url, plan_file.name, api_key)
                console.print(f"  Attached {attach_url} to {issue_id}", style="white")
        except LinearError as e:
            console.print(f"  WARN {e}; linking without verification", style="yellow")

    try:
        create_linear_link(session, plan_file.id, issue_id, issue_title, issue_state, notes)
        console.print(f"\nOK Linked '{plan_name}' to {issue_id}", style="green")

        config = get_linear_config(session, proj.id)
        if config:
            url = generate_linear_issue_url(config.workspace, issue_id)
            console.print(f"  URL: {url}", style="cyan")
        if issue_title:
            console.print(f"  Issue: [{issue_state}] {issue_title}", style="white")
        if notes:
            console.print(f"  Notes: {notes}", style="white")
    except ValueError as e:
        console.print(f"ERROR {e}", style="red")
    except Exception as e:
        console.print(f"ERROR Failed to create link: {e}", style="red")


@linear.command("unlink")
@click.argument("plan_name")
@click.option("--issue", default=None, help="Issue id to unlink (unlinks all if omitted)")
@click.option("--all", "unlink_all", is_flag=True, help="Unlink all Linear issues")
@click.option("--project", default=None, help="Project name")
def linear_unlink(
    plan_name: str, issue: str | None, unlink_all: bool, project: str | None
) -> None:
    """Unlink a plan file from Linear issue(s)"""
    from .database import delete_all_linear_links, delete_linear_link_by_id
    from .linear_utils import format_linear_issue_id

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        console.print("ERROR Project not found", style="red")
        raise SystemExit(1)

    plan_file = next((pf for pf in proj.plan_files if pf.name == plan_name), None)
    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found", style="red")
        raise SystemExit(1)

    try:
        if unlink_all or not issue:
            count = delete_all_linear_links(session, plan_file.id)
            if count > 0:
                console.print(
                    f"\nOK Unlinked {count} Linear issue(s) from '{plan_name}'", style="green"
                )
            else:
                console.print(f"\n No Linear links found for '{plan_name}'", style="yellow")
        else:
            issue_id = format_linear_issue_id(issue)
            if delete_linear_link_by_id(session, plan_file.id, issue_id):
                console.print(f"\nOK Unlinked '{plan_name}' from {issue_id}", style="green")
            else:
                console.print(f"\nERROR Link to {issue_id} not found", style="yellow")
    except Exception as e:
        console.print(f"ERROR Failed to unlink: {e}", style="red")


@linear.command("links")
@click.option("--project", default=None, help="Project name (shows all projects if omitted)")
def linear_links(project: str | None) -> None:
    """List all Linear links"""
    from .database import list_all_linear_links

    session = _require_session()
    if project:
        proj = get_project_by_name(session, project)
        if not proj:
            console.print(f"ERROR Project '{project}' not found", style="red")
            raise SystemExit(1)
        projects = [proj]
    else:
        projects = db_list_projects(session)

    if not projects:
        console.print("No projects found", style="yellow")
        return

    for proj in projects:
        links = list_all_linear_links(session, proj.id)
        if not links:
            if len(projects) == 1:
                console.print(f"\nNo Linear links found for project '{proj.name}'", style="yellow")
            continue

        console.print(f"\n{proj.name}:", style="cyan bold")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Plan File")
        table.add_column("Linear Issue")
        table.add_column("State")
        table.add_column("Created")
        for link in links:
            table.add_row(
                link["plan_file_name"],
                link["linear_issue_id"],
                link["issue_state"] or "--",
                link["created_at"].strftime("%Y-%m-%d") if link["created_at"] else "N/A",
            )
        console.print(table)


@linear.command("show")
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
def linear_show(plan_name: str, project: str | None) -> None:
    """Show detailed Linear links for a plan file"""
    from .database import get_linear_config, get_linear_links
    from .linear_utils import generate_linear_issue_url

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        console.print("ERROR Project not found", style="red")
        raise SystemExit(1)

    plan_file = next((pf for pf in proj.plan_files if pf.name == plan_name), None)
    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found", style="red")
        raise SystemExit(1)

    links = get_linear_links(session, plan_file.id)
    if not links:
        console.print(f"\nNo Linear links found for '{plan_name}'", style="yellow")
        return

    console.print(f"\nPlan: {plan_name}", style="cyan bold")
    console.print("Linear Links:\n", style="white")
    config = get_linear_config(session, proj.id)
    for link in links:
        console.print(f"  - {link.linear_issue_id}", style="green")
        if link.issue_state or link.issue_title:
            console.print(
                f"    Issue: [{link.issue_state or '?'}] {link.issue_title or ''}", style="white"
            )
        if config:
            url = generate_linear_issue_url(config.workspace, link.linear_issue_id)
            console.print(f"    URL: {url}", style="cyan")
        if link.notes:
            console.print(f"    Notes: {link.notes}", style="white")


@linear.command("refresh")
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
def linear_refresh(plan_name: str, project: str | None) -> None:
    """Re-fetch title/state from Linear for a plan's links (needs LINEAR_API_KEY)"""
    from .database import get_linear_links, update_linear_link_cache
    from .exceptions import LinearError
    from .linear_api import fetch_issue_by_identifier, get_api_key

    api_key = get_api_key()
    if not api_key:
        console.print("ERROR LINEAR_API_KEY is not set; nothing to refresh", style="red")
        raise SystemExit(1)

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        console.print("ERROR Project not found", style="red")
        raise SystemExit(1)

    plan_file = next((pf for pf in proj.plan_files if pf.name == plan_name), None)
    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found", style="red")
        raise SystemExit(1)

    links = get_linear_links(session, plan_file.id)
    if not links:
        console.print(f"\nNo Linear links found for '{plan_name}'", style="yellow")
        return

    console.print(f"\nRefreshing {len(links)} link(s) for '{plan_name}':", style="cyan")
    for link in links:
        try:
            fetched = fetch_issue_by_identifier(link.linear_issue_id, api_key)
            if fetched is None:
                console.print(f"  {link.linear_issue_id}: not found", style="yellow")
                continue
            update_linear_link_cache(session, link.id, fetched["title"], fetched["state"])
            console.print(
                f"  {link.linear_issue_id}: [{fetched['state']}] {fetched['title']}", style="green"
            )
        except LinearError as e:
            console.print(f"  {link.linear_issue_id}: {e}", style="red")


if __name__ == "__main__":
    cli()
