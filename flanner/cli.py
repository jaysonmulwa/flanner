"""
CLI tool for Flanner

Provides command-line interface for managing the Flanner server and projects.
"""

import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import click
from rich.text import Text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import identity, tui
from .database import (
    ProjectModel,
    get_project_by_name,
    get_session,
    init_database,
)
from .database import list_projects as db_list_projects
from .exceptions import DatabaseError, FlannerError, StorageError
from .git_integration import find_git_root, update_gitignore
from .storage import init_storage

# One console for the whole CLI, carrying the palette in tui.THEME.
console = tui.console


def get_mcp_dir() -> Path:
    """Get Flanner data directory (override with FLANNER_HOME)"""
    return Path(os.environ.get("FLANNER_HOME", Path.home() / ".flanner"))


def get_pid_file() -> Path:
    """Get path to PID file"""
    return get_mcp_dir() / "server.pid"


class Sectioned(click.Group):
    """A help screen grouped by what a command is for.

    Thirty-four commands in one alphabetical list put `accept` beside
    `claude-info` and `diff` beside `devices`, which tells a reader nothing
    about which of them they need. The order they are declared in is no
    better; only the grouping carries meaning.

    The split that matters most is the first one: everything above the line
    works with no account and no network, and the sections below it talk to
    a control plane, to other machines, or to a third party. Somebody
    deciding whether flanner is safe to run on a private repository should
    be able to see that from the help text.
    """

    #: Title, then the commands under it, in the order a person meets them
    #: rather than alphabetically. A command missing from here still shows —
    #: see `format_commands` — because a help screen that silently omits a
    #: command is worse than one that is untidy.
    SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "Start here",
            ("init", "list", "web", "doctor"),
        ),
        (
            "Plans on this machine (no account, no network)",
            ("sync", "history", "diff", "config", "delete", "setup-gitignore"),
        ),
        (
            "Is a plan still true (local, reads your git history)",
            ("freshness", "why"),
        ),
        (
            "Review and retire (signed locally; syncs only if you have a team)",
            ("review", "retire"),
        ),
        (
            "Your team (talks to the control plane)",
            ("login", "accept", "whoami", "logout", "invite", "members", "devices", "join"),
        ),
        (
            "Syncing with other machines",
            ("peer", "mesh"),
        ),
        (
            "Agent integration",
            ("setup", "register", "unregister", "claude-info", "start", "stop", "status"),
        ),
        (
            "Issue trackers (talks to Jira or Linear)",
            ("jira", "linear"),
        ),
    )

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        listed: set[str] = set()
        for title, names in self.SECTIONS:
            rows = []
            for name in names:
                command = self.get_command(ctx, name)
                if command is None or command.hidden:
                    continue
                listed.add(name)
                rows.append((name, (command.get_short_help_str(66))))
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)

        # Anything added since this list was written. Falling back rather
        # than dropping it: a new command should look out of place here,
        # not vanish.
        rest = [
            (name, self.get_command(ctx, name).get_short_help_str(66))  # type: ignore[union-attr]
            for name in sorted(self.list_commands(ctx))
            if name not in listed and not getattr(self.get_command(ctx, name), "hidden", False)
        ]
        if rest:
            with formatter.section("Other"):
                formatter.write_dl(rest)


@click.group(cls=Sectioned)
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
    console.print()
    tui.ok("MCP server ready")
    console.print()

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

    from .claude_integration import check_server_status

    rows: list[tuple[str, Any]] = []

    # Server
    running_pid: int | None = None
    if pid_file.exists():
        try:
            candidate = int(pid_file.read_text())
            os.kill(candidate, 0)
            running_pid = candidate
        except (OSError, ValueError):
            pid_file.unlink()
    if running_pid is not None:
        server = tui.dot("ok", label="running")
        server.append(f"  (pid {running_pid})", style="muted")
    else:
        server = tui.dot("unknown", label="stopped")
        server.append("  start it with ", style="muted")
        server.append("flanner start", style="accent")
    rows.append(("MCP server", server))

    # Database
    if db_path.exists():
        rows.append(("Database", Text(str(db_path), style="value")))
        try:
            init_database(str(db_path))
            session = get_session()
            projects = db_list_projects(session)
            total_plans = sum(len(p.plan_files) for p in projects)
            # Its own row rather than appended to the path: the path is long
            # enough to push the counts off the edge of an 80-column terminal,
            # and the counts are the part worth reading.
            catalog = Text()
            catalog.append(
                f"{len(projects)} project{'' if len(projects) == 1 else 's'}", style="value"
            )
            catalog.append(f"  {tui.MIDDOT}  ", style="muted")
            catalog.append(f"{total_plans} plan{'' if total_plans == 1 else 's'}", style="value")
            rows.append(("Catalog", catalog))
        except (FlannerError, SQLAlchemyError):
            rows.append(("Catalog", Text("unreadable", style="bad")))
    else:
        rows.append(("Database", Text("not initialized yet", style="warn")))

    # Claude Code
    claude_status = check_server_status()
    if claude_status["registered"] and claude_status["config_valid"]:
        registered = tui.dot("ok", label="registered")
    elif claude_status["registered"]:
        registered = tui.dot("warn", label="registered")
        registered.append("  config is out of date", style="warn")
    else:
        registered = tui.dot("unknown", label="not registered")
        registered.append("  run ", style="muted")
        registered.append("flanner register", style="accent")
    rows.append(("Claude Code", registered))
    rows.append(("Config file", Text(str(claude_status["config_path"]), style="muted")))

    console.print()
    console.print(tui.fields(rows))
    if claude_status.get("action_needed"):
        console.print()
        tui.warn(str(claude_status["action_needed"]))
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

    session = _require_session()

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

        if not proj.plan_files:
            console.print()
            tui.note(f"No plans in {project} yet. Your agents will fill this in.")
            console.print()
            return

        # The id column is gone: a uuid nobody types was eating a third of the
        # width and then being truncated anyway. The name is what every other
        # command takes as an argument.
        listing = tui.table("Plan", ("Ver", {"justify": "right"}), "Updated by", "Updated")
        for pf in proj.plan_files:
            listing.add_row(
                Text(f"{pf.name}.md", style="value"),
                Text(f"v{pf.current_version}", style="muted"),
                Text(getattr(pf, "created_by", None) or "user", style="muted"),
                Text(
                    pf.updated_at.strftime("%Y-%m-%d %H:%M") if pf.updated_at else "never",
                    style="muted",
                ),
            )
        console.print()
        console.print(listing)
        console.print()
        count = len(proj.plan_files)
        tui.note(f"{count} plan{'' if count == 1 else 's'} in {project}")
        console.print()
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

        listing = tui.table(
            "Project", ("Plans", {"justify": "right"}), "Plan directory", "Created"
        )
        for p in projects:
            listing.add_row(
                Text(p.name, style="value"),
                Text(str(len(p.plan_files)), style="muted"),
                Text(p.plan_directory, style="code"),
                Text(
                    p.created_at.strftime("%Y-%m-%d") if p.created_at else "never", style="muted"
                ),
            )
        console.print()
        table = listing

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

    session = _require_session()

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

    session = _require_session()

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

    session = _require_session()

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
    project_root, plan_directory = project.project_root, project.plan_directory
    _write("delete_project", project_id=str(project.id))
    console.print(f"\nOK Project '{project_name}' deleted successfully", style="green")
    console.print(
        "  Note: Plan files on disk were NOT deleted. You may want to manually remove:",
        style="cyan",
    )
    if project_root:
        console.print(f"  {project_root}/{plan_directory}/", style="cyan")


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

    _require_store()

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
        try:
            proc = subprocess.run(  # noqa: S603 (fixed argv, no shell, no untrusted input)
                [claude_bin, "mcp", "add", "-s", "user", "flanner", "--", "flanner-mcp"],
                capture_output=True,
                text=True,
                # Every other subprocess call in the package is bounded; this
                # one was not. `claude` is somebody else's binary, and if it
                # blocks on a prompt or a network call it takes `flanner init`
                # down with it — during the one command a new user runs first.
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            console.print("WARN Claude Code: registration timed out after 30s", style="yellow")
            console.print(
                "  Add it manually:  claude mcp add -s user flanner -- flanner-mcp", style="white"
            )
            return
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
    console.print()

    from .claude_integration import get_claude_config_info, registration_instructions

    info = get_claude_config_info()

    registered = (
        tui.dot("ok", label="registered")
        if info["server_registered"]
        else tui.dot("unknown", label="not registered")
    )
    console.print(
        tui.fields(
            [
                ("MCP server", registered),
                ("Config file", Text(str(info["config_path"]), style="value")),
                ("File exists", Text("yes" if info["config_exists"] else "no", style="muted")),
                ("MCP servers", Text(str(info["total_servers"]), style="muted")),
            ]
        )
    )

    if info["our_server_config"]:
        console.print()
        tui.note("Current configuration")
        import json

        console.print(json.dumps(info["our_server_config"], indent=2), style="white")
    else:
        console.print()
        tui.note("Not registered. Run flanner register to add it.")
        console.print(registration_instructions(), style="white")


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
    console.print()

    if dry_run:
        tui.note("Dry run. Nothing will be written.")

    session = _require_session()
    init_storage(str(get_mcp_dir()))

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

    # Only the counts that actually happened. A row of zeroes buries the one
    # number worth reading, which is usually "imported".
    summary = Text()
    summary.append(f"{totals['scanned']} scanned", style="value")
    for label, key, style in (
        ("imported", "imported", "ok"),
        ("skipped", "skipped", "muted"),
        ("errors", "error", "bad"),
    ):
        if totals[key]:
            summary.append(f"  {tui.MIDDOT}  ", style="muted")
            summary.append(f"{totals[key]} {label}", style=style)
    console.print()
    console.print(summary)
    console.print()

    if dry_run and totals["imported"] > 0:
        tui.note("Nothing written. Run flanner sync to apply.")


_REVIEW_STYLES = {
    "open": "cyan",
    "accepted": "green",
    "superseded": "blue",
    "rejected": "red",
    "changes_requested": "yellow",
    "withdrawn": "dim",
    "stale": "dark_orange",
}


def _note_authorization(authorization: Any) -> None:
    """Say which regime a review answer came from, wherever one is given.

    Both the status view and the moment of deciding need this, and they must
    not word it differently: somebody who saw one and then the other would
    reasonably read the difference as meaning something.

    The reason comes from the resolution rather than being restated here, so
    there is one sentence to keep true instead of two.
    """
    if not authorization.enforced:
        console.print(f"review here is advisory: {authorization.reason}", style="dim")
    elif not authorization.roles:
        console.print(f"WARN cannot authorize review: {authorization.reason}", style="yellow")


@cli.group()
def review() -> None:
    """Propose plans for review and record decisions"""


def _resolve_plan(
    session: Session, project: str | None, plan_name: str
) -> tuple[ProjectModel, Any]:
    """Find a project and one of its plans, or exit 1 explaining which failed."""
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        _no_project(project)
    plan_file = next((p for p in proj.plan_files if p.name == plan_name), None)
    if plan_file is None:
        console.print(f"ERROR Plan '{plan_name}' not found in '{proj.name}'", style="red")
        raise SystemExit(1)
    return proj, plan_file


@review.command("propose")
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
@click.option("--message", default="", help="Note for reviewers")
@click.option("--actor", default=None, help="Who is proposing (defaults to your entitlement)")
def review_propose(plan_name: str, project: str | None, message: str, actor: str | None) -> None:
    """Offer a plan's newest version for review"""
    from .review import propose

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, plan_name)
    try:
        result = propose(session, project=proj, plan_file=plan_file, message=message, actor=actor)
    except (ValueError, PermissionError) as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    console.print(f"\nOK Proposed '{plan_name}' for review", style="green")
    console.print(f"  proposal: {result.event.event_id}", style="cyan")
    console.print(f"  version:  {result.event.payload['target_artifact_id']}", style="dim")


@review.command("decide")
@click.argument("plan_name")
@click.argument(
    "decision", type=click.Choice(["approve", "reject", "request_changes", "withdraw"])
)
@click.option("--proposal", default=None, help="Proposal id (defaults to the only open one)")
@click.option("--project", default=None, help="Project name")
@click.option("--actor", default=None, help="Who is deciding (defaults to your entitlement)")
def review_decide(
    plan_name: str, decision: str, proposal: str | None, project: str | None, actor: str | None
) -> None:
    """Approve, reject, request changes on, or withdraw a proposal"""
    from . import authz
    from .review import decide, status

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, plan_name)

    if proposal is None:
        open_ones = [
            view
            for view in status(session, plan_file=plan_file, project=proj).proposals.values()
            if view.state in ("open", "stale", "changes_requested")
        ]
        if len(open_ones) != 1:
            console.print(
                f"ERROR {len(open_ones)} proposals are open; name one with --proposal.",
                style="red",
            )
            raise SystemExit(1)
        proposal = open_ones[0].proposal_id

    try:
        result = decide(
            session,
            project=proj,
            plan_file=plan_file,
            proposal_id=proposal,
            action=decision,
            actor=actor,
        )
    except ValueError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    console.print(f"\nOK Recorded {decision} on '{plan_name}'", style="green")
    if result.advanced_baseline:
        console.print("  the accepted baseline now points at this version", style="green")
    else:
        console.print(f"  baseline unchanged: {result.reason}", style="yellow")

    # Said here and not only in `review status`, because this is the moment
    # that reads as an authorization. Somebody can approve without ever
    # having run status, and "Recorded approve" on its own does not
    # distinguish a decision that binds from one that is a rehearsal.
    _note_authorization(authz.resolve(proj))


def _print_comments(session: Session, plan_file: Any) -> None:
    """Notes teammates left, with whether each still finds its text."""
    from .anchors import AMBIGUOUS, STRANDED, Anchor, resolve
    from .assurance import load_comments
    from .database import get_version
    from .storage import load_plan_file

    notes = load_comments(session, str(plan_file.id))
    if not notes:
        return

    current = get_version(session, plan_file.id, None)
    body = ""
    if current is not None:
        try:
            _, body = load_plan_file(current.file_path)
        except (FileNotFoundError, OSError):
            body = ""

    console.print()
    heading = Text()
    heading.append(f"{len(notes)} comment{'' if len(notes) == 1 else 's'}", style="value")
    console.print(heading)
    console.print()
    listing = tui.table(
        "By", ("On", {"overflow": "fold"}), ("Note", {"overflow": "fold"}), "Anchor"
    )
    for event in notes:
        payload = event.payload
        anchor_data = payload.get("anchor") or {}
        state = resolve(Anchor.from_dict(anchor_data), body) if body else None
        if state is None:
            mark = Text("unknown", style="muted")
        elif state.status == STRANDED:
            mark = Text("lost its place", style="bad")
        elif state.status == AMBIGUOUS:
            mark = Text("several matches", style="warn")
        elif state.status == "moved":
            mark = Text("text changed", style="warn")
        else:
            mark = Text("anchored", style="ok")
        listing.add_row(
            Text(str(event.actor or "unknown"), style="muted"),
            Text(str(anchor_data.get("quote") or "")[:40], style="muted"),
            Text(str(payload.get("body") or ""), style="value"),
            mark,
        )
    console.print(listing)
    console.print()


def _print_external_review(session: Session, plan_file: Any) -> None:
    """Notes imported from outside, kept apart from the proposals.

    Separate because they did not come from a device this team can verify,
    and must not read as though they had.
    """
    from .assurance import load_external_reviews

    imported = load_external_reviews(session, str(plan_file.id))
    if not imported:
        return
    total = sum(len(e.payload.get("notes") or []) for e in imported)
    console.print()
    heading = Text()
    heading.append(f"{total} note{'' if total == 1 else 's'} from outside", style="value")
    heading.append("   unverified", style="warn")
    console.print(heading)
    console.print()
    outside = tui.table("From", ("On", {"overflow": "fold"}), ("Note", {"overflow": "fold"}))
    for event in imported:
        who = str(event.payload.get("reviewer") or "unnamed")
        for note in event.payload.get("notes") or []:
            outside.add_row(
                Text(who, style="muted"),
                Text(str(note.get("quote", ""))[:38], style="muted"),
                Text(str(note.get("body", "")), style="value"),
            )
    console.print(outside)
    console.print()


@review.command("status")
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
def review_status(plan_name: str, project: str | None) -> None:
    """Show a plan's proposals and its accepted baseline"""
    from . import authz
    from .review import status

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, plan_name)
    state = status(session, plan_file=plan_file, project=proj)

    _note_authorization(authz.resolve(proj))

    if state.conflicted:
        console.print(
            "WARN the accepted baseline is contested; merge before implementing", style="red"
        )
    elif state.accepted_artifact_id:
        console.print(f"accepted: {state.accepted_artifact_id}", style="green")
    else:
        console.print("accepted: nothing approved yet", style="yellow")

    if not state.proposals:
        console.print("\nNo proposals recorded.", style="dim")
        # Outside review can exist with no proposal at all, and is the
        # whole point of having sent a packet, so it is not skipped here.
        _print_comments(session, plan_file)
        _print_external_review(session, plan_file)
        return

    table = tui.table("Proposal", "State", "Proposer", "Approvals")
    for view in state.proposals.values():
        style = _REVIEW_STYLES.get(view.state, "white")
        table.add_row(
            view.proposal_id[:19] + "...",
            f"[{style}]{view.state}[/{style}]",
            view.proposer,
            ", ".join(view.approvals) or "--",
        )
    console.print(table)

    _print_comments(session, plan_file)
    _print_external_review(session, plan_file)


_FINDING_STYLES = {
    "missing_file": "red",
    "hash_mismatch": "yellow",
    "unreadable_file": "red",
    "orphan_file": "cyan",
    "unknown_plan": "yellow",
    "stale_current_version": "cyan",
    "no_project_root": "red",
    "signature_invalid": "red",
    "artifact_missing": "red",
    "unverified_signer": "blue",
}


@dataclass(frozen=True)
class EnrollmentCheck:
    """One statement about where this device and project stand with a team.

    Separate from a reconcile finding because the two answer different
    questions. A finding is about a plan file; this is about whether team
    features can work here at all, which is the question somebody actually
    has when review or sync is not behaving.
    """

    code: str
    level: str  # ok | info | action | problem
    detail: str
    fix: str = ""


_ENROLLMENT_STYLES = {"ok": "green", "info": "dim", "action": "yellow", "problem": "red"}


def _clock_check(endpoint: str) -> list[EnrollmentCheck]:
    """Compare this machine's clock against the control plane's.

    Peers refuse each other's requests once their clocks are further apart
    than `device_auth.MAX_SKEW`, and the refusal arrives mid-sync with a
    number rather than a cause. This turns it into a line read during setup.

    The control plane is used as the reference because it is the one clock
    both devices already agree to talk to, and its `Date` header comes free
    with a request the device makes anyway. Silent when the network is
    unavailable: a diagnostic that fails because a laptop is on a train
    should say nothing, not raise an alarm.
    """
    import email.utils
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone

    from .device_auth import MAX_SKEW

    url = endpoint.rstrip("/") + "/health"
    if not url.startswith(("http://", "https://")):
        return []
    try:
        request = urllib.request.Request(url, method="HEAD")  # noqa: S310 - scheme checked above
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - scheme checked before the call
            served = response.headers.get("Date")
    except (urllib.error.URLError, OSError, ValueError):
        return []
    if not served:
        return []

    try:
        theirs = email.utils.parsedate_to_datetime(served)
    except (TypeError, ValueError):
        return []
    if theirs.tzinfo is None:
        theirs = theirs.replace(tzinfo=timezone.utc)

    drift = abs((datetime.now(timezone.utc) - theirs).total_seconds())
    allowed = MAX_SKEW.total_seconds()
    if drift <= allowed / 2:
        return []
    level = "problem" if drift > allowed else "action"
    return [
        EnrollmentCheck(
            "clock_drift",
            level,
            f"This machine's clock is {int(drift)}s from the server's. Peers refuse "
            f"each other past {int(allowed)}s, so syncing will fail.",
            "w32tm /resync /force   # Windows, as administrator",
        )
    ]


def _enrollment_report(project: Any) -> list[EnrollmentCheck]:
    """Where this device stands: enrolled, entitled, granted, and bound.

    Four separate things, in the order they gate each other. Reporting them
    apart matters because the failures look identical from the outside — a
    push that does nothing is the same silence whether the device was never
    enrolled, the entitlement lapsed, an admin has not granted a workspace
    yet, or this repository was never joined to one.

    The last check is the one worth having. A project bound to a workspace
    the account may not enter is invisible in every other command: `whoami`
    lists the grants, `join` reports the binding, and neither notices that
    they disagree.
    """
    from . import entitlements
    from . import session as cache

    bound = getattr(project, "workspace_id", None)
    current = cache.load()

    if current is None:
        checks = [
            EnrollmentCheck(
                "not_enrolled",
                "info",
                "Not enrolled with a team. Local plan work needs no account.",
                "flanner accept <token> --as your-handle",
            )
        ]
        if bound:
            checks.append(
                EnrollmentCheck(
                    "bound_without_account",
                    "problem",
                    f"This project is bound to workspace {bound}, but the device is not "
                    "enrolled, so review here counts for nobody.",
                    "flanner accept <token> --as your-handle",
                )
            )
        return checks

    checks = [
        EnrollmentCheck(
            "enrolled",
            "ok",
            f"Enrolled as {current.user_id} in {current.organization_id}.",
        )
    ]
    checks.extend(_clock_check(current.endpoint))

    verdict = current.status()
    # Against the module's own constant, not a literal. The first version of
    # this compared with "VALID" while the constant is "valid", so a healthy
    # entitlement was reported as being in grace — a doctor that lies about
    # the thing it exists to check.
    if verdict.status == entitlements.VALID:
        checks.append(EnrollmentCheck("entitlement_valid", "ok", "Entitlement is current."))
    elif verdict.usable:
        checks.append(
            EnrollmentCheck(
                "entitlement_grace",
                "action",
                f"Entitlement is in grace: {verdict.reason or 'not renewed recently'}. "
                "Reads still work; pushing is refused until it renews.",
                "flanner whoami --refresh",
            )
        )
    else:
        checks.append(
            EnrollmentCheck(
                "entitlement_expired",
                "problem",
                f"Entitlement is {verdict.status}: {verdict.reason or 'no longer valid'}. "
                "Team features are off until it renews.",
                "flanner whoami --refresh",
            )
        )

    capabilities = verdict.claims.workspace_capabilities if verdict.claims else ()
    granted = {c.workspace_id: c.role for c in capabilities}
    if not granted:
        checks.append(
            EnrollmentCheck(
                "no_grants",
                "action",
                "No workspace access granted yet. An admin has to grant it, and it "
                "arrives when the entitlement next renews.",
                "flanner whoami --refresh",
            )
        )
    elif not bound:
        listed = ", ".join(sorted(granted))
        checks.append(
            EnrollmentCheck(
                "not_bound",
                "action",
                f"This project is not bound to a workspace, so review here does not "
                f"count for the team. You may enter: {listed}.",
                "flanner join <workspace-id>",
            )
        )
    elif bound in granted:
        checks.append(
            EnrollmentCheck(
                "bound",
                "ok",
                f"Bound to {bound} as {granted[bound]}.",
            )
        )
    else:
        listed = ", ".join(sorted(granted))
        checks.append(
            EnrollmentCheck(
                "bound_without_grant",
                "problem",
                f"Bound to workspace {bound}, which this account may not enter. "
                f"Access covers: {listed}. Either an admin revoked it, or the id is wrong.",
                "flanner join <workspace-id>  # or --clear to unbind",
            )
        )
    return checks


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
        _no_project(project)

    findings = reconcile_project(session, proj, repair=repair)
    enrollment = _enrollment_report(proj)

    if output == "json":
        # An object, not the bare array this used to print. The array could
        # only ever describe plan files, and "this device is not enrolled" is
        # not a plan file. A caller reading the enrollment state should not
        # have to filter it out of a list of missing-file findings.
        click.echo(
            json_module.dumps(
                {
                    "project": proj.name,
                    "catalog": [
                        {
                            "kind": f.kind,
                            "plan": f.plan,
                            "detail": f.detail,
                            "path": f.path,
                            "repairable": f.repairable,
                        }
                        for f in findings
                    ],
                    "enrollment": [
                        {"code": c.code, "level": c.level, "detail": c.detail, "fix": c.fix}
                        for c in enrollment
                    ],
                },
                indent=2,
            )
        )
        return

    if not findings:
        console.print(
            f"OK Catalog, files, and signatures all agree for '{proj.name}'", style="green"
        )
        _print_enrollment(enrollment)
        return

    table = tui.table("Issue", "Plan", "Detail")
    for finding in findings:
        style = _FINDING_STYLES.get(finding.kind, "white")
        table.add_row(f"[{style}]{finding.kind}[/{style}]", finding.plan, finding.detail)
    console.print(table)

    unchecked = [f for f in findings if f.informational]
    if unchecked and len(unchecked) == len(findings):
        console.print(
            f"\nNo problems found. {len(unchecked)} item(s) could not be verified on "
            "this device; the note above says why.",
            style="green",
        )
        _print_enrollment(enrollment)
        return

    if repair:
        fixed = sum(1 for f in findings if f.repairable)
        console.print(f"\nRepaired {fixed} of {len(findings)} findings.", style="green")
        remaining = [f for f in findings if not f.repairable and not f.informational]
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

    _print_enrollment(enrollment)


def _print_enrollment(checks: list[EnrollmentCheck]) -> None:
    """The team half of the report, printed whichever way the catalog went.

    Always printed, including when everything is fine. A check that only
    appears on failure cannot be used to confirm success, and confirming
    success is most of what somebody wants after running four setup commands.
    """
    console.print("\nTeam")
    for check in checks:
        style = _ENROLLMENT_STYLES.get(check.level, "white")
        console.print(f"  {check.detail}", style=style)
        if check.fix:
            console.print(f"    {tui.command(check.fix)}", style="dim")


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
        _no_project(project)

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

    # One plan named: the full case for the verdict, which is what someone
    # asking about a single plan wants. The table below is for scanning.
    if plan_name and len(results) == 1:
        plan, version_obj, evidence = results[0]
        console.print()
        headline = Text()
        headline.append_text(tui.dot(evidence["status"]))
        headline.append("  ")
        headline.append(f"{plan.name}.md", style="value")
        headline.append(f"  v{version_obj.version}", style="muted")
        console.print(headline)
        console.print()
        for reason in evidence["reasons"]:
            if evidence["status"] == "stale":
                tui.bad(reason)
            else:
                tui.warn(reason)
        if not evidence["reasons"]:
            tui.ok("nothing has drifted since this was written")

        detail = [
            ("Anchor", "anchored_at_commit"),
            ("Commits since", "commits_since_anchor"),
            ("Age (days)", "age_days"),
            ("Dead refs", "invalid_refs"),
            ("Cited paths", "referenced_paths"),
            ("Cited symbols", "referenced_symbols"),
        ]
        rows = []
        for label, key in detail:
            got = evidence.get(key)
            if got in (None, [], ""):
                continue
            shown = ", ".join(str(x) for x in got) if isinstance(got, list) else str(got)
            rows.append((label, Text(shown, style="code")))
        if rows:
            console.print()
            console.print(tui.fields(rows))
        console.print()
        return

    listing = tui.table(
        "Plan",
        ("Ver", {"justify": "right"}),
        "Status",
        ("Evidence", {"overflow": "fold"}),
    )
    counts: dict[str, int] = {}
    for plan, version_obj, evidence in results:
        status = evidence["status"]
        counts[status] = counts.get(status, 0) + 1
        listing.add_row(
            Text(f"{plan.name}.md", style="value"),
            Text(f"v{version_obj.version}", style="muted"),
            tui.dot(status),
            Text(evidence["reasons"][0] if evidence["reasons"] else "", style="muted"),
        )
    console.print()
    console.print(listing)
    console.print()

    summary = tui.tally(counts)
    worst = next((s for s in reversed(tui.FRESHNESS_ORDER) if counts.get(s)), None)
    if worst and worst != "fresh":
        example = next(p.name for p, _, e in results if e["status"] == worst)
        summary.append(f"  {tui.DASH} run ", style="muted")
        summary.append(f"flanner freshness {example}", style="accent")
        summary.append(" for the full evidence", style="muted")
    console.print(summary)
    console.print()


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
    from .jira_utils import is_valid_jira_url, normalize_jira_url

    _require_store()

    # Validate JIRA URL
    if not is_valid_jira_url(url):
        console.print(f"ERROR Invalid JIRA URL format: {url}", style="red")
        console.print("  Expected format: https://company.atlassian.net", style="yellow")
        raise SystemExit(1)

    session = _require_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        raise SystemExit(1)

    # Create or update JIRA config
    try:
        normalized_url = normalize_jira_url(url)
        result = _write(
            "configure_jira",
            project_id=str(project.id),
            jira_url=normalized_url,
            jira_project_key=project_key,
        )

        console.print(
            f"\nOK JIRA configuration updated for project '{project_name}'", style="green"
        )
        console.print(f"  JIRA URL: {result['jira_url']}", style="white")
        if result.get("jira_project_key"):
            console.print(f"  Default Project Key: {result['jira_project_key']}", style="white")
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
    from .database import get_jira_config
    from .jira_utils import format_jira_issue_key, generate_jira_issue_url, is_valid_jira_issue_key

    _require_store()

    # Validate issue key
    formatted_issue = format_jira_issue_key(issue)
    if not is_valid_jira_issue_key(formatted_issue):
        console.print(f"ERROR Invalid JIRA issue key format: {issue}", style="red")
        console.print(
            "  Expected format: PROJECT-123 (uppercase letters, dash, numbers)", style="yellow"
        )
        raise SystemExit(1)

    session = _require_session()

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
        _write(
            "link_plan_to_jira",
            plan_file_id=str(plan_file.id),
            jira_issue_key=formatted_issue,
            issue_type=issue_type,
            notes=notes,
            created_by="user",
        )

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
    from .jira_utils import format_jira_issue_key

    session = _require_session()

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

    # Unlink. A missing link is a warning here, not a failure, so these go
    # through dispatch directly rather than the exit-on-error helper.
    from .services import dispatch

    try:
        if unlink_all or not issue:
            result = dispatch("unlink_jira_issue", {"plan_file_id": str(plan_file.id)})
            if result.get("error"):
                console.print(f"ERROR {result['message']}", style="red")
                raise SystemExit(1)
            count = result.get("count", 0)
            if count > 0:
                console.print(
                    f"\nOK Unlinked {count} JIRA issue(s) from '{plan_name}'", style="green"
                )
            else:
                console.print(f"\n No JIRA links found for '{plan_name}'", style="yellow")
        else:
            formatted_issue = format_jira_issue_key(issue)
            result = dispatch(
                "unlink_jira_issue",
                {"plan_file_id": str(plan_file.id), "jira_issue_key": formatted_issue},
            )
            if result.get("success"):
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

    session = _require_session()

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

        table = tui.table("Plan File", "JIRA Issue", "Type", "Created")

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

    session = _require_session()

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


def _write(op: str, **args: Any) -> dict[str, Any]:
    """Run one write operation through the shared service layer.

    Routes to the local daemon when one is running, so the CLI cannot mutate
    shared state behind its back (PRD Phase 1 single-writer discipline), and
    executes in-process otherwise. Reports the operation's own message and
    exits 1 on failure, so every CLI write fails the same way.
    """
    from .services import dispatch

    result = dispatch(op, args)
    if result.get("error"):
        console.print(f"ERROR {result['message']}", style="red")
        raise SystemExit(1)
    return result


def _ensure_store() -> None:
    """Create the machine-wide store if it is not there yet.

    Idempotent. `init` does this too, along with adopting a repository and
    registering the MCP server; this is only the part every command needs.
    """
    mcp_dir = get_mcp_dir()
    init_storage(str(mcp_dir))
    init_database(str(mcp_dir / "data.db"))


def _require_store() -> None:
    """Refuse, in one voice, when this machine has no store yet.

    Eleven commands wrote this refusal out themselves, so improving it
    meant improving it eleven times, and the wording had already drifted
    from what `init` actually does.
    """
    if (get_mcp_dir() / "data.db").exists():
        return
    # Naming the command was not enough. People reach this by following our
    # own instructions, so it says what the command does and that hitting
    # it once is expected.
    tui.bad("This machine has no flanner store yet.")
    tui.note("`flanner init` creates it, and adopts the repository you run it in.")
    tui.hint(f"  {tui.command('flanner init')}")
    raise SystemExit(1)


def _open_store() -> None:
    """Make the local catalog usable in this process, or refuse.

    Separate from `_require_session` for the commands that hand `get_session`
    to something else — a background thread, or a server that opens one per
    request — rather than opening one here. `peer serve` did neither and so
    initialised nothing, which its own catch-up thread then discovered.
    """
    _require_store()
    init_database(str(get_mcp_dir() / "data.db"))


def _require_session() -> Session:
    """Open the flanner database, or refuse if there is not one yet."""
    _open_store()
    return get_session()


def _no_project(project: str | None) -> NoReturn:
    """Explain which lookup failed, then exit.

    Telling somebody to "pass --project" when they just passed --project is
    the kind of message that makes a tool feel like it is not listening. The
    name they gave is the useful thing to echo back.

    Three different situations used to share one message. Standing in a
    repository that has simply never been adopted is by far the most common,
    and it was being told to "run this from inside a project" — advice to go
    somewhere else, when the answer is to adopt where you already are. It is
    what somebody following the join sequence meets if they reach for
    `flanner join` before `flanner init`.
    """
    if project:
        console.print(f"ERROR No project named '{project}'.", style="red")
        tui.hint("Run flanner list to see the projects this machine knows about.")
        raise SystemExit(1)

    git_root = find_git_root(os.getcwd())
    if git_root:
        console.print("ERROR This repository has not been adopted by flanner yet.", style="red")
        tui.note(f"Found a git repository at {git_root}, but no project for it.")
        tui.hint(f"  {tui.command('flanner init')}   adopt it, then run this again")
    else:
        console.print("ERROR Not inside a git repository.", style="red")
        tui.note("flanner works per repository, and finds one by looking for its git root.")
        tui.hint("Change to a repository first, or name a project with --project.")
    raise SystemExit(1)


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
        result = _write("configure_linear", project_id=str(proj.id), workspace=slug)
        console.print(
            f"\nOK Linear configuration updated for project '{project_name}'", style="green"
        )
        console.print(f"  Workspace: {result['workspace']}", style="white")
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
    from .linear_utils import (
        format_linear_issue_id,
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

    # Issue verification, URL attachment, and the write all happen in the
    # shared service, so the CLI and the MCP tool cannot drift apart; it
    # reports back whatever it managed to observe.
    result = _write(
        "link_plan_to_linear",
        plan_file_id=str(plan_file.id),
        linear_issue_id=issue_id,
        notes=notes,
        verify=not no_verify,
        attach_url=attach_url,
        created_by="user",
    )

    if result.get("warning"):
        console.print(f"  WARN {result['warning']}", style="yellow")
    elif attach_url and result.get("issue_title"):
        console.print(f"  Attached {attach_url} to {issue_id}", style="white")

    console.print(f"\nOK Linked '{plan_name}' to {issue_id}", style="green")
    if result.get("linear_url"):
        console.print(f"  URL: {result['linear_url']}", style="cyan")
    if result.get("issue_title"):
        console.print(f"  Issue: [{result['issue_state']}] {result['issue_title']}", style="white")
    if notes:
        console.print(f"  Notes: {notes}", style="white")


@linear.command("unlink")
@click.argument("plan_name")
@click.option("--issue", default=None, help="Issue id to unlink (unlinks all if omitted)")
@click.option("--all", "unlink_all", is_flag=True, help="Unlink all Linear issues")
@click.option("--project", default=None, help="Project name")
def linear_unlink(
    plan_name: str, issue: str | None, unlink_all: bool, project: str | None
) -> None:
    """Unlink a plan file from Linear issue(s)"""
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

    # As with jira unlink, a missing link is a warning rather than a failure,
    # so this uses dispatch directly instead of the exit-on-error helper.
    from .services import dispatch

    try:
        if unlink_all or not issue:
            result = dispatch("unlink_linear_issue", {"plan_file_id": str(plan_file.id)})
            if result.get("error"):
                console.print(f"ERROR {result['message']}", style="red")
                raise SystemExit(1)
            count = result.get("count", 0)
            if count > 0:
                console.print(
                    f"\nOK Unlinked {count} Linear issue(s) from '{plan_name}'", style="green"
                )
            else:
                console.print(f"\n No Linear links found for '{plan_name}'", style="yellow")
        else:
            issue_id = format_linear_issue_id(issue)
            result = dispatch(
                "unlink_linear_issue",
                {"plan_file_id": str(plan_file.id), "linear_issue_id": issue_id},
            )
            if result.get("success"):
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
        table = tui.table("Plan File", "Linear Issue", "State", "Created")
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


# --- account -----------------------------------------------------------------
# Team features need a signed entitlement; local plan work never does. These
# commands are the only ones in the CLI that talk to the control plane.

_ENTITLEMENT_STYLE = {
    "valid": "green",
    "in_grace": "yellow",
    "expired": "red",
    "untrusted_key": "red",
    "bad_signature": "red",
    "malformed": "dim",
}


@cli.command()
@click.argument("code")
@click.option("--endpoint", default=None, help="Control plane URL (defaults to Flanner Mesh)")
@click.option("--label", default=None, help="Name for this device (defaults to the hostname)")
def login(code: str, endpoint: str | None, label: str | None) -> None:
    """Enroll this device with an enrollment code from your team console"""
    from . import account
    from . import session as session_cache

    try:
        current = account.login(
            code, endpoint=endpoint or session_cache.DEFAULT_ENDPOINT, label=label
        )
    except account.SessionError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    console.print(f"OK Enrolled as {current.user_id} ({current.device_id})", style="green")
    _print_entitlement(current)


@cli.command()
def logout() -> None:
    """Forget this device's session (the device keeps its identity)"""
    from . import session as account

    if account.clear():
        console.print("OK Signed out on this device", style="green")
        console.print(
            "This device is still enrolled. Revoke it from the team console to end its access.",
            style="dim",
        )
    else:
        console.print("Not signed in on this device", style="dim")


@cli.command()
@click.option("--refresh", "do_refresh", is_flag=True, help="Renew the entitlement first")
def whoami(do_refresh: bool) -> None:
    """Show this device's identity and what it is currently entitled to"""
    from . import account
    from . import identity as device
    from . import session as cache

    console.print(f"Device  {device.device_id()}")
    _print_store()

    current: cache.Session | None
    if do_refresh:
        # An explicit --refresh is a request, not a heuristic. ensure_fresh
        # would skip the call while the held entitlement is still valid,
        # which is exactly when someone runs this to pick up a new grant.
        try:
            current = account.refresh()
        except account.SessionError as e:
            console.print(f"WARN could not renew: {e}", style="yellow")
            current = cache.load()
    else:
        current = cache.load()
    if current is None:
        console.print("Account not signed in", style="dim")
        console.print("Local plan work needs no account. Run 'flanner login' to join a team.")
        return

    console.print(f"Account {current.user_id} in {current.organization_id}")
    console.print(f"Server  {current.endpoint}")
    _print_entitlement(current)


def _print_store() -> None:
    """What this device holds, and the fact that it never sheds it.

    The Settings page in the web UI has said this since retirement landed.
    The CLI had not, and a CLI-only user is the common case, so the decision
    to keep everything was invisible to the people living with it.

    Skipped rather than reported as zero when there is no database. A fresh
    install holding nothing is a different claim from a store that has been
    measured, and "0 B" would read as the second.
    """
    from .database import list_artifacts

    db_path = get_mcp_dir() / "data.db"
    if not db_path.exists():
        return

    init_database(str(db_path))
    rows = list_artifacts(get_session())
    held = sum(len(row.payload or "") for row in rows)

    console.print(f"Holds   {len(rows)} artifacts, {tui.size(held)}")
    console.print(
        "        never pruned; retiring a plan hides it and erases nothing",
        style="dim",
    )


def _print_entitlement(current: Any) -> None:
    """Report what the held entitlement allows, and where it stands."""
    verdict = current.status()
    style = _ENTITLEMENT_STYLE.get(verdict.status, "dim")
    console.print(f"Access  {verdict.status}", style=style)
    if verdict.reason:
        console.print(f"        {verdict.reason}", style=style)
    if verdict.claims is None:
        return

    console.print(f"Expires {verdict.claims.expires_at}")
    capabilities = verdict.claims.workspace_capabilities
    if not capabilities:
        console.print("No workspace access granted yet", style="dim")
        return
    table = tui.table("Workspace", "Role")
    for capability in capabilities:
        table.add_row(capability.workspace_id, capability.role)
    console.print(table)


@cli.command()
@click.argument("workspace_id", required=False)
@click.option("--project", default=None, help="Project name (uses current directory if omitted)")
@click.option("--clear", "clear_binding", is_flag=True, help="Leave the workspace")
@click.option(
    "--no-adopt",
    is_flag=True,
    help="Do not bring existing plans into the workspace",
)
def join(
    workspace_id: str | None, project: str | None, clear_binding: bool, no_adopt: bool
) -> None:
    """Bind a project to a control-plane workspace, making review binding

    Run `flanner init` first in a repository flanner has not seen before:
    joining binds an existing project, and does not create one.

    Until a project joins one, review runs but authorizes nothing. After it
    joins, roles come from the signed entitlement this device holds.

    Deliberately not exposed over MCP: joining or leaving a workspace changes
    who may approve a plan, which is not a decision an agent should make on
    the user's behalf.
    """
    if not workspace_id and not clear_binding:
        tui.bad("Give a workspace id, or --clear to leave.")
        # The id is not guessable and nothing prints it by accident, so a
        # refusal that does not name where to find one leaves somebody
        # searching a console they may not have access to.
        _print_workspaces_hint()
        raise SystemExit(1)

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        _no_project(project)

    if clear_binding:
        proj.workspace_id = None
        session.commit()
        console.print(f"OK '{proj.name}' left its workspace", style="green")
        console.print("Review still runs here, but it authorizes nothing.", style="dim")
        return

    from .plan_ops import adopt_into_workspace

    # The guard at the top requires one of a workspace id or --clear, and
    # --clear has returned by now. Repeated as a real check rather than an
    # assertion, which `python -O` would strip.
    if not workspace_id:
        console.print("ERROR Give a workspace id.", style="red")
        raise SystemExit(1)

    proj.workspace_id = workspace_id
    session.commit()
    console.print(f"OK '{proj.name}' joined workspace {workspace_id}", style="green")

    if no_adopt:
        console.print("Existing plans stay local and will not sync, as asked.", style="yellow")
        console.print(
            "      Run 'flanner join' again without --no-adopt to bring them across.",
            style="dim",
        )
        return

    # A workspace id is inside the signed envelope, so joining cannot move
    # what was written before it. Each plan's current content is signed
    # afresh into the workspace instead, as a root there.
    report = adopt_into_workspace(session, project=proj, workspace_id=workspace_id)
    if report.moved:
        console.print(
            f"Brought {report.moved} plan(s) into the workspace: " + ", ".join(report.adopted),
            style="green",
        )
        console.print(
            "      Their current content syncs from now on. Earlier history"
            " stays on this machine, because it was signed for a workspace"
            " nobody else can verify.",
            style="dim",
        )
    if report.already_there:
        console.print(f"already in this workspace: {len(report.already_there)}", style="dim")
    for name, why in report.skipped:
        console.print(f"skipped {name}: {why}", style="dim")

    from . import authz

    authorization = authz.resolve(proj)
    if authorization.roles:
        console.print(f"You hold: {authorization.roles[authorization.actor]}", style="green")
    else:
        console.print(f"No access yet: {authorization.reason}", style="yellow")


def _print_workspaces_hint() -> None:
    """Name the workspaces this device may enter, or say why there are none.

    Three surfaces already hold this — `whoami`, the local Mesh page and the
    console — and the one place somebody is standing when they need it
    listed none of them.
    """
    from . import session as cache

    current = cache.load()
    if current is None:
        tui.note("This device is not signed in, so it holds no workspace access.")
        tui.hint(f"Run {tui.command('flanner login <code>')} with an invitation first.")
        return

    claims = current.status().claims
    grants = getattr(claims, "workspace_capabilities", ()) if claims else ()
    if not grants:
        tui.note("Your account has no workspace access yet.")
        tui.hint("An admin grants it from the console, then run")
        tui.hint(f"  {tui.command('flanner whoami --refresh')} to pick it up.")
        return

    tui.note("Workspaces this device may enter:")
    table = tui.table("Workspace", "Role")
    for grant in grants:
        table.add_row(grant.workspace_id, grant.role)
    console.print(table)
    tui.hint(f"Also shown by {tui.command('flanner whoami')} and on the Mesh page.")


@cli.command()
@click.argument("token")
@click.option("--as", "user_id", required=True, help="The user id to join as")
@click.option("--endpoint", default=None, help="Control plane URL (defaults to Flanner Mesh)")
@click.option("--label", default=None, help="Name for this device (defaults to the hostname)")
def accept(token: str, user_id: str, endpoint: str | None, label: str | None) -> None:
    """Accept an invitation, joining a team and enrolling this device"""
    from . import account
    from . import session as session_cache

    try:
        current = account.accept_invitation(
            token,
            user_id=user_id,
            endpoint=endpoint or session_cache.DEFAULT_ENDPOINT,
            label=label,
        )
    except account.SessionError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    # The store is machine-wide, and accepting an invitation is the moment
    # this machine commits to being used with a team. Creating it here
    # removes the failure everybody hit: the next instruction we print is
    # `flanner join`, and until now that refused because nothing had made a
    # database yet.
    #
    # It does not make `init` unnecessary. `join` also needs a project, and
    # a project is per repository — so `init` still runs once per repo, and
    # the message below says so.
    _ensure_store()

    console.print(f"OK Joined as {current.user_id} ({current.device_id})", style="green")
    _print_entitlement(current)
    console.print(
        "\nThis machine is ready. In each repository you want to share plans\n"
        "from, run:\n"
        "    flanner init          adopts that repository\n"
        "    flanner join <id>     binds it to a workspace\n\n"
        "The workspace ids are listed above, and by 'flanner whoami'.",
        style="dim",
    )


@cli.group()
def devices() -> None:
    """Manage the machines enrolled under your account"""


@devices.command("list")
def devices_list() -> None:
    """Show every machine enrolled under your account"""
    from . import account

    enrolled = _console_call(account.list_devices)
    if not enrolled:
        console.print("No devices enrolled.", style="dim")
        return

    table = tui.table("Device", "Name", "Enrolled", "Last seen")
    for device in enrolled:
        here = " (this one)" if device.get("this_device") else ""
        table.add_row(
            device["device_id"] + here,
            device.get("label") or "-",
            (device.get("enrolled_at") or "")[:10],
            (device.get("last_seen_at") or "never")[:10],
        )
    console.print(table)


@devices.command("add")
def devices_add() -> None:
    """Mint a code to enroll another machine under your account"""
    from . import account

    code, expires_at = _console_call(account.request_enrollment_code)
    console.print("Run this on the other machine:", style="dim")
    console.print(f"\n  flanner login {code}\n", style="cyan")
    console.print(f"The code expires at {expires_at} and works once.", style="dim")


@devices.command("revoke")
@click.argument("device_id")
def devices_revoke(device_id: str) -> None:
    """Retire a machine, so it stops receiving entitlements"""
    from . import account
    from . import identity as this

    _console_call(account.revoke_device, device_id)
    console.print(f"OK {device_id} revoked", style="green")
    if device_id == this.device_id():
        console.print("That was this machine. Run 'flanner logout' here too.", style="yellow")
    console.print("Entitlements it already holds stay valid until they expire.", style="dim")


@cli.command()
@click.argument("email")
@click.option("--admin", is_flag=True, help="Invite as an organization admin")
def invite(email: str, admin: bool) -> None:
    """Invite someone to your organization (admins only)"""
    from . import account

    token = _console_call(account.invite_member, email, admin=admin)
    console.print(f"OK Invited {email}", style="green")
    console.print("\nSend them this:", style="dim")
    console.print(f"\n  flanner accept {token} --as <their-user-id>\n", style="cyan")
    console.print("An invitation costs no seat until it is accepted.", style="dim")


@cli.command()
def members() -> None:
    """List your organization's members and seat count (admins only)"""
    from . import account

    result = _console_call(account.list_members)
    console.print(f"Seats in use: {result.get('seats', 0)}\n")

    table = tui.table("Member", "Email", "Role", "State")
    for member in result.get("members") or []:
        style = "dim" if member["state"] != "active" else None
        table.add_row(
            member.get("user_id") or "(not joined)",
            member.get("email") or "-",
            member["role"],
            member["state"],
            style=style,
        )
    console.print(table)


def _console_call(action: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a control-plane call, reporting a refusal rather than a traceback."""
    from . import account

    try:
        return action(*args, **kwargs)
    except account.SessionError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None


@cli.command("retire")
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name (uses current directory if omitted)")
@click.option("--reason", default="", help="Why, recorded with the claim")
@click.option("--restore", is_flag=True, help="Undo a retirement instead")
@click.option("--yes", is_flag=True, help="Skip the confirmation")
def retire_plan(
    plan_name: str, project: str | None, reason: str, restore: bool, yes: bool
) -> None:
    """Ask peers to stop showing a plan, or show it again with --restore

    Not a deletion, and the command is not named one. Nothing is erased:
    every version stays in the history, every signature still verifies, and
    a teammate who was offline when you ran this keeps the content until
    they next sync. What travels is a claim that other devices honour.
    """
    from . import review as review_module
    from .assurance import retirement

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, plan_name)

    standing = retirement(session, str(plan_file.id))
    if not restore and standing.retired:
        tui.note(f"{plan_name} is already retired.")
        return
    if restore and not standing.retired:
        tui.note(f"{plan_name} is not retired.")
        return

    if not yes and not restore:
        tui.warn(f"This asks every peer to hide {tui.code(plan_name)}.")
        tui.note("Nothing is erased. Anyone already holding it keeps the bytes,")
        tui.note("and a device that never receives this claim keeps showing it.")
        if not click.confirm("Record the claim?", default=False):
            tui.note("Nothing recorded.")
            return

    try:
        review_module.retire(
            session, project=proj, plan_file=plan_file, reason=reason, restore=restore
        )
    except PermissionError as e:
        tui.bad(str(e))
        raise SystemExit(1) from None

    if restore:
        tui.ok(f"{plan_name} is visible again")
    else:
        tui.ok(f"{plan_name} retired")
        # Said on the way out as well as at the prompt, because --yes skips
        # the prompt entirely and a script is exactly where somebody would
        # assume this deleted something.
        tui.note("Nothing was erased. Anyone already holding it keeps the bytes.")
        tui.hint(f"Undo with {tui.command(f'flanner retire {plan_name} --restore')}")


@cli.group()
def peer() -> None:
    """Sync plans directly with another device"""


def _keyring_refresher() -> Any:
    """Fetch this organisation's device keys and hand back a fresh resolver.

    The composition root is the only place allowed to join these two: a
    reachability test asserts that `peer` cannot reach `account`, so the
    network call is passed in from here rather than imported down there.

    A resolver is returned rather than nothing, because the one the serving
    path already holds is bound to the session as it was before the fetch
    and would still not know the key we just learned.
    """
    from . import account
    from . import session as cache

    def refresh() -> Any:
        account.fetch_device_keys()
        renewed = cache.load()
        return renewed.resolve_device_key if renewed is not None else None

    return refresh


def _catch_up_in_background(dial: Any) -> None:
    """Pull from known peers while the server is already answering.

    In a thread on purpose. Catching up means dialling machines that are
    mostly asleep, and doing that before binding would make start-up time a
    function of how many colleagues have shut their laptops.
    """
    import threading

    from . import peer as peer_transport
    from . import session as cache

    def run() -> None:
        with get_session() as session:
            workspaces = sorted(
                {p.workspace_id for p in db_list_projects(session) if p.workspace_id}
            )
            if not workspaces:
                return

            def report(device_id: str, _workspace: str, result: Any) -> None:
                if result.accepted:
                    tui.ok(f"caught up {len(result.accepted)} from {tui.code(device_id[:12])}")

            peer_transport.catch_up(session, workspaces, cache.load, dial=dial, on_result=report)

    threading.Thread(target=run, daemon=True).start()


@peer.command("serve")
@click.option("--host", default="0.0.0.0", help="Address to listen on (--http only)")  # noqa: S104 - opt-in --http flag; the default path binds nothing
@click.option("--port", default=None, type=int, help="Port to listen on (--http only)")
@click.option(
    "--http",
    is_flag=True,
    help="Listen on a port instead, for peers already on the same network",
)
def peer_serve(host: str, port: int | None, http: bool) -> None:
    """Serve this device's catalog to authorised peers

    Reachable without a listening port, a forwarded port or administrator
    rights: this device dials out and answers on that connection. Nothing is
    served to a caller who cannot produce a signed request and a matching
    entitlement, so being reachable grants nothing on its own.
    """
    import uvicorn

    from . import identity as device_identity
    from . import peer as peer_transport
    from . import peer_iroh
    from . import session as cache

    # Before anything else: this command hands `get_session` to a background
    # thread and to the request handler rather than opening one itself, so
    # nothing here would otherwise initialise the database. The thread then
    # died on its first query while the server reported itself as serving.
    _open_store()

    if cache.load() is None:
        console.print("ERROR Not signed in, so no peer can be authorised.", style="red")
        console.print("Run 'flanner login' first.", style="dim")
        raise SystemExit(1)

    if not http:
        endpoint = peer_iroh.shared_endpoint()
        try:
            endpoint.ready()
        except peer_transport.PeerError as e:
            console.print(f"ERROR {e}", style="red")
            console.print("Use 'flanner peer serve --http' to listen on a port.", style="dim")
            raise SystemExit(1) from None
        console.print("Serving plans to authorised peers.", style="green")
        console.print(f"This device: {device_identity.device_id()}", style="dim")
        console.print(
            "Peers pull with 'flanner peer pull <device-id>'. No port is open.",
            style="dim",
        )
        _catch_up_in_background(
            lambda device_id, workspace_id: peer_iroh.peer_for(device_id, workspace_id, cache.load)
        )
        try:
            endpoint.serve(get_session, cache.load, _keyring_refresher())
        except KeyboardInterrupt:
            endpoint.close()
        return

    listen_on = port or peer_transport.DEFAULT_PORT
    console.print(f"Serving plans to authorised peers on {host}:{listen_on}", style="green")
    console.print(
        "Callers need a signed request and an entitlement for the workspace.", style="dim"
    )
    # Over HTTP a peer is named by address, and this device knows device ids
    # rather than addresses, so there is nobody to dial. Catching up here is
    # a manual `flanner peer pull <address>`.
    uvicorn.run(
        # get_session is already a factory returning a context-managed
        # Session, which is exactly the shape the app wants.
        peer_transport.create_peer_app(get_session, cache.load, _keyring_refresher()),
        host=host,
        port=listen_on,
        log_level="warning",
    )


@peer.command("status")
@click.argument("device_id", required=False)
def peer_status(device_id: str | None) -> None:
    """Show how this device is reachable, or how it reaches one peer

    With no argument, what a peer sees when it tries to reach this machine.
    With a device id, whether the connection to that machine goes direct or
    through a relay. Both work; a relay is slower, and that difference is
    invisible until someone is waiting for a sync.
    """
    from . import peer as peer_transport
    from . import peer_iroh
    from . import session as cache

    try:
        if device_id:
            route = peer_iroh.route_to(device_id, cache.load)
            console.print(f"Peer       {route.device_id}")
            console.print(
                f"Connection {route.connection}",
                style="yellow" if route.relayed else "green",
            )
            if route.address:
                console.print(f"Path       {route.address}")
            if route.rtt_ms:
                console.print(f"Round trip {route.rtt_ms} ms")
            if route.relayed:
                console.print(
                    "Relayed, so slower. Usually a firewall that refuses to be\n"
                    "punched through. Nothing is broken and nothing is exposed.",
                    style="dim",
                )
            return

        status = peer_iroh.local_status(cache.load)
    except peer_transport.PeerError as e:
        console.print(f"ERROR {e}", style="red")
        if not peer_iroh.available():
            console.print(
                "Everything else works. Only reaching a peer that has no address needs it.",
                style="dim",
            )
        raise SystemExit(1) from None

    console.print(f"This device {status.device_id}", style="green")
    console.print("Peers reach it with 'flanner peer pull <device-id>'.", style="dim")
    if status.home_relay:
        console.print(f"Home relay  {status.home_relay}")
    if status.configured_relay:
        console.print(f"Own relay   {status.configured_relay}")
    for address in status.addresses:
        console.print(f"Address     {address}")
    console.print(
        "Addresses are how peers try to reach this machine directly.\n"
        "No port is listening: this device dials out and answers there.",
        style="dim",
    )
    _print_arrivals()


def _print_arrivals(limit: int = 8) -> None:
    """What teammates' work reached this device most recently.

    The whole notification system, and deliberately so: there is no service
    that could tell anybody a plan changed without also telling us, and
    knowing which plans a team touches is exactly the metadata this design
    refuses to hold. A list you can look at when you want one is what is
    left, and it turns out to be enough.
    """
    from .database import recent_arrivals
    from .utils import format_relative_time

    # `peer status` answers about reachability and works before this device
    # has a catalog at all, so an uninitialised database is a normal state
    # here rather than an error.
    if not (get_mcp_dir() / "data.db").exists():
        return
    session = _require_session()
    arrivals = recent_arrivals(session, exclude_device_id=identity.device_id(), limit=limit)
    if not arrivals:
        console.print("\nNothing has arrived from a teammate yet.", style="dim")
        return

    console.print()
    table = tui.table("Arrived", "What", "From")
    for artifact in arrivals:
        table.add_row(
            format_relative_time(artifact.received_at) if artifact.received_at else "unknown",
            artifact.artifact_type,
            # Not truncated. A device id is twenty characters and two
            # teammates' ids share a prefix, so shortening it turns the one
            # column that says who into a column that says nothing.
            artifact.actor_device_id,
        )
    console.print(table)


@peer.command("pull")
@click.argument("address")
@click.option("--project", default=None, help="Project name (uses current directory if omitted)")
def peer_pull(address: str, project: str | None) -> None:
    """Pull whatever a peer holds for this project's workspace that we lack

    Give a device id to reach a peer wherever it is, or an http address for
    one already on this network.
    """
    from . import peer as peer_transport
    from . import peer_iroh
    from . import session as cache

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        _no_project(project)
    if not proj.workspace_id:
        console.print("ERROR This project has not joined a workspace.", style="red")
        console.print("Run 'flanner join <workspace-id>' first.", style="dim")
        raise SystemExit(1)

    try:
        remote = peer_iroh.peer_for(address, proj.workspace_id, cache.load)
    except peer_transport.PeerError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    report = peer_transport.pull(session, address, proj.workspace_id, cache.load, remote=remote)

    console.print(f"accepted: {len(report.accepted)}", style="green")
    if report.already_held:
        console.print(f"already held: {len(report.already_held)}", style="dim")
    for artifact_id, reason in report.rejected:
        console.print(f"REJECTED {artifact_id}: {reason}", style="red")
    if not report.ok:
        raise SystemExit(1)


def _sync_report(report: Any, *, verb: str, idle: str) -> None:
    """One shape for both directions, so pull and push read the same."""
    if report.accepted:
        tui.ok(f"{verb} {len(report.accepted)}")
    else:
        tui.note(idle)
    if report.already_held:
        tui.note(f"{len(report.already_held)} already there")
    for artifact_id, reason in report.rejected:
        tui.bad(f"{tui.code(artifact_id[:12])} {reason}")
    if not report.ok:
        raise SystemExit(1)


@peer.command("push")
@click.argument("address")
@click.option("--project", default=None, help="Project name (uses current directory if omitted)")
def peer_push(address: str, project: str | None) -> None:
    """Send a peer whatever it lacks for this project's workspace

    The peer decides what it will take. It checks every artifact against
    its author's key, refuses anything your role does not cover, and may
    decline pushes entirely — all of which show up here as refusals rather
    than as failures.

    Nothing is queued for a peer that is offline. They pick it up on their
    next pull.
    """
    from . import peer as peer_transport
    from . import peer_iroh
    from . import session as cache

    session = _require_session()
    proj = _resolve_project_or_cwd(session, project)
    if not proj:
        _no_project(project)
    if not proj.workspace_id:
        tui.bad("This project has not joined a workspace.")
        tui.hint(f"Run {tui.command('flanner join <workspace-id>')} first.")
        raise SystemExit(1)

    try:
        remote = peer_iroh.peer_for(address, proj.workspace_id, cache.load)
    except peer_transport.PeerError as e:
        tui.bad(str(e))
        raise SystemExit(1) from None

    report = peer_transport.push(session, address, proj.workspace_id, cache.load, remote=remote)
    _sync_report(report, verb="sent", idle="nothing to send")


@cli.group()
def mesh() -> None:
    """Join and inspect the private network this team's devices share"""


def _runtime(url: str = "") -> Any:
    """The mesh client wrapper.

    The CLI is a composition root, so this is the one place in the client
    package that names a provider. Everything else speaks `flanner.mesh`,
    and a test allows exactly this file and the adapter itself.
    """
    from .mesh_netbird import NetBirdRuntime

    return NetBirdRuntime(management_url=url)


@mesh.command("status")
def mesh_status() -> None:
    """Show whether this device is on the team's private network"""
    runtime = _runtime()
    if not runtime.installed():
        console.print("Mesh client not installed.", style="dim")
        console.print(
            "You almost certainly do not need one. 'flanner peer pull' reaches\n"
            "devices wherever they are, without a VPN or administrator rights.\n"
            "Run 'flanner peer status' to see how this machine is reached.",
            style="dim",
        )
        return

    status = runtime.status()
    console.print(
        f"Network {'connected' if status.enrolled else 'not connected'}",
        style="green" if status.enrolled else "yellow",
    )
    if status.message:
        console.print(f"        {status.message}", style="dim")
    for endpoint in status.endpoints:
        console.print(f"Address {endpoint}")

    peers = runtime.peers()
    if not peers:
        return
    table = tui.table("Peer address", "Connection")
    for peer in peers:
        table.add_row(peer.endpoint, peer.connection)
    console.print(table)
    console.print(
        "Addresses only. Who a peer is gets settled by the signed handshake,\n"
        "never by the network.",
        style="dim",
    )


@mesh.command("join")
def mesh_join() -> None:
    """Join the private network, using a credential from the control plane"""
    from . import account
    from .exceptions import MeshError
    from .mesh import Enrollment

    runtime = _runtime()
    if not runtime.installed():
        console.print("ERROR The mesh client is not installed on this machine.", style="red")
        raise SystemExit(1)

    try:
        offered = account.mesh_credential()
    except account.SessionError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    if offered is None:
        console.print("This team has no managed network.", style="yellow")
        console.print(
            "Nothing to join; syncing over your existing network still works.", style="dim"
        )
        return

    credential, management_url, expires_at = offered
    try:
        _runtime(management_url).enroll(
            Enrollment(credential=credential, device_id="", expires_at=expires_at)
        )
    except MeshError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    console.print("OK Joined the team's private network", style="green")


@mesh.command("leave")
def mesh_leave() -> None:
    """Disconnect this device from the private network"""
    runtime = _runtime()
    if not runtime.installed():
        console.print("Mesh client not installed.", style="dim")
        return
    runtime.leave()
    console.print("OK Disconnected", style="green")
    console.print("Plans and local work are untouched.", style="dim")


# --- history, diff and why ------------------------------------------------------
#
# Three commands the mockups show. They read what is already recorded - the
# version rows and the files they point at - so none of them needs a network
# call or a git checkout.


def _version_bodies(versions: list[Any]) -> dict[int, str]:
    """The text of each version, skipping any whose file has gone missing."""
    from .storage import load_plan_file

    bodies: dict[int, str] = {}
    for version in versions:
        try:
            bodies[version.version] = load_plan_file(version.file_path)[1]
        except (FileNotFoundError, OSError):
            continue
    return bodies


def _churn(before: str | None, after: str) -> tuple[int, int]:
    """Lines added and removed between two versions.

    The first version counts as all-added: it did not replace anything, and
    reporting +0 for a plan somebody just wrote would be a lie of omission.
    """
    import difflib

    if before is None:
        return len(after.splitlines()), 0
    added = removed = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _section_of(lines: list[str], index: int) -> str:
    """The nearest markdown heading at or above a line.

    A hunk header reading `@@ ## Rollout @@` says where you are in the
    document. The line numbers difflib offers instead are true and useless.
    """
    for i in range(min(index, len(lines) - 1), -1, -1):
        if lines[i].startswith("#"):
            return lines[i].strip()
    return ""


@review.command("comment")
@click.argument("plan_name")
@click.option("--on", "quote", required=True, help="The text to attach the note to")
@click.option("-m", "--message", required=True, help="The note")
@click.option("--project", default=None, help="Project name")
@click.option("--version", "wanted", default=None, type=int, help="Version to comment on")
def review_comment(
    plan_name: str, quote: str, message: str, project: str | None, wanted: int | None
) -> None:
    """Leave a note against a quotation in a plan

    The note is anchored to what it quotes, not to a line number, so it
    survives the plan being edited above it. If the quoted text is later
    rewritten the note says it lost its place rather than sliding onto a
    sentence nobody meant.
    """
    from . import review as review_module

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, plan_name)
    try:
        review_module.comment(
            session,
            project=proj,
            plan_file=plan_file,
            quote=quote,
            body=message,
            version=wanted,
        )
    except ValueError as e:
        console.print(f"ERROR {e}", style="red")
        raise SystemExit(1) from None

    console.print()
    tui.ok("Comment recorded")
    console.print()
    console.print(
        tui.fields(
            [
                ("On", Text(anchors_clip(quote), style="code")),
                ("Note", Text(message, style="value")),
            ]
        )
    )
    console.print()
    tui.hint(f"See it with flanner review status {plan_file.name}")
    console.print()


def anchors_clip(text: str) -> str:
    from .anchors import clip

    return clip(text)


@review.command("pack")
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
@click.option("--version", "wanted", default=None, type=int, help="Version to pack")
@click.option("--output", default=None, help="Where to write it")
@click.option("--no-fonts", is_flag=True, help="Leave the typefaces out, for a smaller file")
def review_pack(
    plan_name: str, project: str | None, wanted: int | None, output: str | None, no_fonts: bool
) -> None:
    """Write a plan as one file somebody outside the team can annotate

    No server, no upload, no account: the recipient opens the file and marks
    it up. They send back the JSON it exports and you run `flanner review
    import` on it.

    The packet carries the plan and nothing else. Notes your own team has
    left stay where they are.
    """
    from pathlib import Path as _Path

    from . import packet as packet_module
    from .database import get_version
    from .storage import load_plan_file

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, plan_name)
    version = get_version(session, plan_file.id, wanted)
    if version is None:
        console.print(f"ERROR v{wanted} of '{plan_file.name}' does not exist", style="red")
        raise SystemExit(1)
    try:
        _, body = load_plan_file(version.file_path)
    except FileNotFoundError:
        console.print(f"ERROR v{version.version} is no longer on disk", style="red")
        raise SystemExit(1) from None

    built = packet_module.build(
        plan_name=plan_file.name,
        version=version.version,
        body=body,
        project_name=proj.name,
        authored_at=version.created_at,
        embed_fonts=not no_fonts,
    )
    target = _Path(output or f"{plan_file.name}.v{version.version}.review.html")
    target.write_text(built.html, encoding="utf-8")

    console.print()
    tui.ok(f"Wrote [value]{target}[/value]")
    console.print()
    console.print(
        tui.fields(
            [
                ("Plan", Text(f"{plan_file.name}.md  v{version.version}", style="value")),
                ("Size", Text(f"{built.kib} KiB", style="value")),
                ("Sections", Text(str(len(built.headings)), style="muted")),
                ("Contains", Text("this plan only, no team review", style="muted")),
            ]
        )
    )
    console.print()
    tui.hint("Send that file to your reviewer. They need nothing installed.")
    tui.hint(f"When it comes back: flanner review import <file> --project {proj.name}")
    console.print()


@review.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--project", default=None, help="Project name")
@click.option("--plan", "plan_override", default=None, help="Attach to this plan instead")
def review_import(path: str, project: str | None, plan_override: str | None) -> None:
    """Take back the notes a review packet exported

    The reviewer had no device key, so nothing they wrote is signed by them.
    This device signs that it received the notes: a claim about where they
    came from, never about who wrote them. They are recorded as unverified
    and shown that way.
    """
    import json as json_module
    from pathlib import Path as _Path

    from . import review as review_module

    try:
        payload = json_module.loads(_Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        console.print(f"ERROR could not read that review: {e}", style="red")
        raise SystemExit(1) from None

    header = payload.get("packet") or {}
    notes = payload.get("notes") or []
    reviewer = str(payload.get("reviewer") or "").strip() or "an unnamed reviewer"
    named = plan_override or str(header.get("plan") or "")
    if not named:
        console.print("ERROR that file does not say which plan it belongs to", style="red")
        raise SystemExit(1)
    if not notes:
        console.print()
        tui.note("That review has no notes in it. Nothing to record.")
        console.print()
        return

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, named)

    packed_at = header.get("version")
    current = plan_file.current_version
    if packed_at and packed_at != current:
        tui.warn(
            f"Written against v{packed_at}; this plan is now at v{current}. "
            "Notes are recorded against what was reviewed."
        )

    review_module.import_external(
        session,
        project=proj,
        plan_file=plan_file,
        reviewer=reviewer,
        notes=notes,
        reviewed_version=packed_at if isinstance(packed_at, int) else None,
        source=str(payload.get("source") or "packet"),
    )

    console.print()
    tui.ok(f"Recorded {len(notes)} note{'' if len(notes) == 1 else 's'} from {reviewer}")
    console.print()
    listing = tui.table("On", ("Note", {"overflow": "fold"}))
    for note in notes[:12]:
        listing.add_row(
            Text(str(note.get("quote", ""))[:44], style="muted"),
            Text(str(note.get("body", "")), style="value"),
        )
    console.print(listing)
    if len(notes) > 12:
        console.print()
        tui.note(f"{len(notes) - 12} more not shown.")
    console.print()
    tui.warn("Unverified: the reviewer has no device key, so nothing here is signed by them.")
    console.print()


@cli.command()
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
@click.option("--limit", default=0, type=int, help="Show only the newest N versions")
def history(plan_name: str, project: str | None, limit: int) -> None:
    """Every version of a plan, newest first"""
    from .database import get_linear_links, list_versions
    from .utils import format_relative_time

    session = _require_session()
    proj, plan_file = _resolve_plan(session, project, plan_name)

    versions = list_versions(session, plan_file.id)
    if not versions:
        console.print()
        tui.note(f"{plan_file.name} has no versions recorded.")
        console.print()
        return

    bodies = _version_bodies(versions)
    linked = {link.plan_file_id: link for link in get_linear_links(session, plan_file.id)}

    listing = tui.table(
        ("Ver", {"justify": "right"}),
        "When",
        "By",
        ("Change", {"justify": "right"}),
        "Note",
    )
    shown = versions[:limit] if limit > 0 else versions
    for version in shown:
        previous = bodies.get(version.version - 1)
        added, removed = _churn(previous, bodies.get(version.version, ""))

        change = Text()
        change.append(f"+{added}", style="ok")
        if removed:
            change.append(f" -{removed}", style="bad")

        note = Text(version.notes or "", style="muted")
        if version.version == 1 and not version.notes:
            note = Text("created", style="muted")
        if linked and version.version == plan_file.current_version:
            note.append("  linked ", style="muted")
            note.append(next(iter(linked.values())).linear_issue_id, style="accent")

        listing.add_row(
            Text(f"v{version.version}", style="value"),
            Text(
                format_relative_time(version.created_at) if version.created_at else "unknown",
                style="muted",
            ),
            Text(version.created_by or "user", style="muted"),
            change,
            note,
        )

    console.print()
    console.print(listing)
    console.print()
    total = len(versions)
    tui.note(f"{total} version{'' if total == 1 else 's'} of {plan_file.name} in {proj.name}")
    if total > 1:
        newest, older = versions[0].version, versions[1].version
        tui.hint(f"Compare with flanner diff {plan_file.name} v{older} v{newest}")
    console.print()


@cli.command()
@click.argument("plan_name")
@click.argument("from_version", required=False)
@click.argument("to_version", required=False)
@click.option("--project", default=None, help="Project name")
def diff(
    plan_name: str, from_version: str | None, to_version: str | None, project: str | None
) -> None:
    """What changed between two versions of a plan

    With no versions given, compares the last two. Versions may be written
    as `3` or `v3`.
    """
    import difflib

    from .database import list_versions

    session = _require_session()
    _, plan_file = _resolve_plan(session, project, plan_name)
    versions = list_versions(session, plan_file.id)
    numbers = sorted(v.version for v in versions)

    if len(numbers) < 2 and not (from_version and to_version):
        console.print()
        tui.note(f"{plan_file.name} has only one version, so there is nothing to compare.")
        console.print()
        return

    def parse(raw: str | None, fallback: int) -> int:
        if raw is None:
            return fallback
        try:
            return int(raw.lstrip("vV"))
        except ValueError:
            console.print(f"ERROR '{raw}' is not a version number", style="red")
            raise SystemExit(1) from None

    # The defaults are only meaningful when both versions were omitted, and
    # numbers[-2] raises on a single-version plan, so they are not computed
    # unless they are needed.
    left = parse(from_version, numbers[-2] if from_version is None else 0)
    right = parse(to_version, numbers[-1] if to_version is None else 0)
    bodies = _version_bodies(versions)
    for wanted in (left, right):
        if wanted not in bodies:
            console.print(f"ERROR v{wanted} of '{plan_file.name}' is not on disk", style="red")
            raise SystemExit(1)

    before = bodies[left].splitlines()
    after = bodies[right].splitlines()

    console.print()
    header = Text()
    header.append(f"{plan_file.name}.md", style="value")
    header.append(f"  v{left} ", style="muted")
    header.append(tui.ARROW, style="muted")
    header.append(f" v{right}", style="muted")
    console.print(header)
    console.print()

    printed = False
    for group in difflib.SequenceMatcher(None, before, after).get_grouped_opcodes(3):
        printed = True
        section = _section_of(after, group[0][3])
        rule = Text()
        rule.append("@@ ", style="muted")
        rule.append(section or f"lines {group[0][3] + 1}-{group[-1][4]}", style="accent")
        rule.append(" @@", style="muted")
        console.print(rule)
        for tag, i1, i2, j1, j2 in group:
            if tag in ("replace", "delete"):
                for line in before[i1:i2]:
                    console.print(Text(f"- {line}", style="bad"))
            if tag in ("replace", "insert"):
                for line in after[j1:j2]:
                    console.print(Text(f"+ {line}", style="ok"))
            if tag == "equal":
                for line in before[i1:i2]:
                    console.print(Text(f"  {line}", style="muted"))
        console.print()

    if not printed:
        tui.note("No differences. The two versions have identical text.")
        console.print()
        return

    added, removed = _churn(bodies[left], bodies[right])
    summary = Text()
    summary.append(f"+{added}", style="ok")
    summary.append(" added", style="muted")
    if removed:
        summary.append(f"  {tui.MIDDOT}  ", style="muted")
        summary.append(f"-{removed}", style="bad")
        summary.append(" removed", style="muted")
    console.print(summary)
    console.print()


@cli.command()
@click.argument("plan_name")
@click.option("--project", default=None, help="Project name")
@click.pass_context
def why(ctx: click.Context, plan_name: str, project: str | None) -> None:
    """Why a plan is judged fresh, aging, suspect or stale

    The same evidence `flanner freshness <plan>` prints. Kept as its own
    command because "why is this stale" is the question people actually
    have, and it is not obvious that a command called freshness answers it.
    """
    ctx.invoke(freshness, plan_name=plan_name, project=project, output="table")


# --- worked examples ----------------------------------------------------------
#
# `--project TEXT` tells a reader the flag exists and nothing about what goes
# in it. `--project checkout-service` answers that, so these use real-looking
# values rather than <placeholders>.
#
# Attached to the built commands in one pass rather than as a decorator on
# each, so the whole set is readable together and a test can check every key
# against a command that exists.

EXAMPLES: dict[str, tuple[str, ...]] = {
    "init": (
        "flanner init                     adopt the repository you are in",
        "flanner init --plan-dir docs/plans",
        "flanner init --skip-claude       do not register the MCP server",
    ),
    "list": (
        "flanner list                     every project on this machine",
        "flanner list --project checkout-service",
    ),
    "web": (
        "flanner web                      the local UI on 8080",
        "flanner web --port 8090 --open-browser",
    ),
    "doctor": ("flanner doctor                   catalog against the files on disk",),
    "sync": (
        "flanner sync                     import .plans files already there",
        "flanner sync --dry-run           show what it would import",
    ),
    "history": ("flanner history payment-webhooks --project checkout-service",),
    "diff": (
        "flanner diff payment-webhooks 3 5 --project checkout-service",
        "flanner diff payment-webhooks 5  against the version before it",
    ),
    "freshness": (
        "flanner freshness                every plan, worst first",
        "flanner freshness payment-webhooks",
        "flanner freshness --output json  for a script",
    ),
    "why": ("flanner why payment-webhooks --project checkout-service",),
    "config": ("flanner config checkout-service --plan-dir docs/plans",),
    "delete": ("flanner delete old-service --force",),
    "setup-gitignore": ("flanner setup-gitignore checkout-service",),
    "retire": (
        "flanner retire anchor-demo --reason 'superseded by v2'",
        "flanner retire anchor-demo --restore",
    ),
    "login": (
        "flanner login K7QP2M4X           code from your team console",
        "flanner login K7QP2M4X --endpoint https://app.flanner.io",
    ),
    "accept": (
        "flanner accept Aitkkm46g6MXnk15 --as jayson \\",
        "        --endpoint https://app.flanner.io",
    ),
    "whoami": (
        "flanner whoami                   identity, access, workspaces",
        "flanner whoami --refresh         renew, to pick up a new grant now",
    ),
    "logout": ("flanner logout",),
    "invite": ("flanner invite raj@acme.test",),
    "members": ("flanner members",),
    "join": (
        "flanner join ws_f24dca1f15b391e1 bind this repository",
        "flanner join                     lists the ids you may use",
        "flanner join --clear             leave the workspace",
    ),
    "review status": ("flanner review status payment-webhooks --project checkout-service",),
    "review propose": ("flanner review propose payment-webhooks --message 'retry budget raised'",),
    "review decide": (
        "flanner review decide payment-webhooks --accept",
        "flanner review decide payment-webhooks --reject",
    ),
    "review comment": (
        "flanner review comment payment-webhooks \\",
        "        --on 'The retry budget is three attempts' \\",
        "        -m 'Is three enough under load?'",
    ),
    "review pack": ("flanner review pack payment-webhooks --out review.html",),
    "review import": ("flanner review import payment-webhooks --from review.notes.json",),
    "peer serve": (
        "flanner peer serve               reachable with no open port",
        "flanner peer serve --http --port 8776",
    ),
    "peer pull": (
        "flanner peer pull dev_7ab74afd93b09861",
        "flanner peer pull http://192.168.1.20:8776",
    ),
    "peer push": ("flanner peer push dev_7ab74afd93b09861",),
    "peer status": (
        "flanner peer status              how peers reach this machine",
        "flanner peer status dev_7ab74afd93b09861",
    ),
    "devices list": ("flanner devices list",),
    "devices revoke": ("flanner devices revoke dev_7ab74afd93b09861",),
    "mesh status": ("flanner mesh status",),
}


def _attach_examples(group: click.Group, prefix: str = "") -> None:
    r"""Hang the worked examples off each command's help screen.

    The leading ``\b`` is click's marker for "do not rewrap what follows".
    Without it the examples are reflowed into a paragraph, which turns a
    column of commands into prose and loses the alignment that makes them
    scannable.
    """
    for name, command in group.commands.items():
        path = f"{prefix}{name}"
        lines = EXAMPLES.get(path)
        if lines:
            command.epilog = "Examples:\n\n\b\n" + "\n".join(f"  {line}" for line in lines)
        if isinstance(command, click.Group):
            _attach_examples(command, f"{path} ")


def main() -> None:
    """Entry point, and the only place an exit code is decided for a fault.

    Three codes, so a script can tell the two failures apart:

    * 0 -- it worked
    * 1 -- you asked for something that cannot be done (no such project, no
      access, a workspace id that is not yours). Fix the command and retry.
    * 2 -- the machine underneath failed (disk, permissions, a store that
      will not open). Retrying the same command will not help.

    Click already produces 0 and 1. Without this wrapper the second kind
    arrived as a traceback, which tells a user nothing and a script less: an
    unreadable store and a typo in a project name exited identically.
    """
    try:
        cli.main(standalone_mode=True)
    except (DatabaseError, StorageError, OSError) as e:
        console.print(f"ERROR {e}", style="red")
        tui.note("A failure on this machine, not a problem with the command itself.")
        raise SystemExit(2) from None


_attach_examples(cli)
