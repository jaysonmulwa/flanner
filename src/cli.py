"""
CLI tool for Flanner

Provides command-line interface for managing the Flanner server and projects.
"""

import os
import sys
import subprocess
import signal
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from .database import (
    init_database, get_session,
    get_project_by_name, list_projects as db_list_projects,
    delete_project
)
from .git_integration import find_git_root, update_gitignore
from .storage import init_storage, ensure_plan_directory_exists

console = Console()


def get_mcp_dir() -> Path:
    """Get Flanner data directory"""
    return Path.home() / ".flanner"


def get_pid_file() -> Path:
    """Get path to PID file"""
    return get_mcp_dir() / "server.pid"


@click.group()
def cli():
    """Flanner - Manage plan files for AI assistants"""
    pass


@cli.command()
@click.option('--project-root', default=None, help='Project root path')
@click.option('--plan-dir', default='.plans', help='Plan directory name')
@click.option('--skip-claude', is_flag=True, help='Skip Claude Code integration')
@click.option('--force-new-project', is_flag=True, help='Force create new project even if one exists')
def init(project_root, plan_dir, skip_claude, force_new_project):
    """Initialize Flanner"""
    mcp_dir = get_mcp_dir()

    # Initialize storage
    init_storage(str(mcp_dir))

    # Initialize database
    db_path = mcp_dir / "data.db"
    init_database(str(db_path))

    console.print(f"OK Initialized Flanner at {mcp_dir}", style="green")
    console.print(f"OK Database created at {db_path}", style="green")

    # Register with Claude Code (unless skipped)
    if not skip_claude:
        console.print("\n[MCP] Registering MCP server with Claude Code...", style="cyan")
        from .claude_integration import auto_register_on_init
        success, message = auto_register_on_init()

        if success:
            console.print(f"OK {message}", style="green")
            console.print("  You may need to restart Claude Code for changes to take effect", style="yellow")
        else:
            console.print(f"⚠ {message}", style="yellow")
            console.print("  You can manually register later with: flanner register", style="white")

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
                console.print(f"  Plan directory: {existing_project.plan_directory}", style="white")
                console.print(f"  Plan files: {len(existing_project.plan_files)}", style="white")
                console.print(f"\n  Tip: MCP server registration still completed above.", style="cyan")
                console.print(f"  You can run 'flanner init' anytime to ensure everything is set up!", style="cyan")
            else:
                if existing_project and force_new_project:
                    console.print(f"⚠ Project '{existing_project.name}' already exists for this directory", style="yellow")
                    console.print(f"  Creating a new project anyway (--force-new-project)", style="yellow")

                # Prompt for project name
                project_name = click.prompt("Enter project name", default=Path(project_root).name)

                # Create project
                from .server import create_project_tool
                result = create_project_tool(
                    name=project_name,
                    project_root=project_root,
                    plan_directory=plan_dir
                )

                if result.get('error'):
                    console.print(f"ERROR Error: {result['message']}", style="red")
                else:
                    console.print(f"OK Created project: {project_name}", style="green")
                    console.print(f"OK Plan directory: {result['full_plan_path']}", style="green")
                    if result.get('gitignore_updated'):
                        console.print(f"OK Updated .gitignore to exclude plan files", style="green")
        except Exception as e:
            console.print(f"⚠ Could not check for existing project: {e}", style="yellow")
            console.print(f"  Skipping project creation to be safe", style="yellow")


@cli.command()
@click.option('--port', default=8080, help='Web server port')
def start(port):
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
    console.print("\n" + "="*60, style="cyan")
    console.print("MCP SERVER READY", style="cyan bold")
    console.print("="*60 + "\n", style="cyan")

    console.print("Add to your Claude Code MCP settings:\n", style="white")

    src_path = Path(__file__).parent.parent / "src"
    console.print(f"""{{
  "mcpServers": {{
    "plan-manager": {{
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "{Path.cwd()}"
    }}
  }}
}}""", style="yellow")

    console.print("\nOr run the server directly:", style="white")
    console.print("  python -m src.server\n", style="yellow")


@cli.command()
def stop():
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
def status():
    """Show server status"""
    pid_file = get_pid_file()
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    console.print("\n" + "="*60, style="cyan")
    console.print("FLANNER STATUS", style="cyan bold")
    console.print("="*60 + "\n", style="cyan")

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
        except:
            console.print("Database: Unable to read", style="red")
    else:
        console.print(f"\nDatabase: Not initialized", style="yellow")

    # Claude Code Integration Status
    console.print("\n" + "-"*60, style="cyan")
    console.print("CLAUDE CODE INTEGRATION", style="cyan bold")
    console.print("-"*60, style="cyan")

    from .claude_integration import check_server_status
    claude_status = check_server_status()

    console.print(f"\nConfig Path: {claude_status['config_path']}", style="white")

    if claude_status['registered']:
        if claude_status['config_valid']:
            console.print("MCP Server: Registered & Valid", style="green")
        else:
            console.print("MCP Server: Registered (config outdated)", style="yellow")
            console.print(f"  {claude_status['message']}", style="yellow")
    else:
        console.print("MCP Server: Not Registered", style="yellow")

    if claude_status.get('action_needed'):
        console.print(f"\nAction needed: {claude_status['action_needed']}", style="yellow")

    console.print()


@cli.command()
@click.option('--project', default=None, help='Project name')
def list(project):
    """List all projects or plan files"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    init_database(str(db_path))
    session = get_session()

    if project:
        # List plan files for a specific project
        proj = get_project_by_name(session, project)
        if not proj:
            console.print(f"ERROR Project '{project}' not found", style="red")
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
                pf.updated_at.strftime("%Y-%m-%d %H:%M") if pf.updated_at else "N/A"
            )

        console.print(table)
    else:
        # List all projects
        projects = db_list_projects(session)

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
                p.created_at.strftime("%Y-%m-%d") if p.created_at else "N/A"
            )

        console.print(table)
        console.print()


@cli.command()
@click.argument('project_name')
@click.option('--project-root', default=None, help='New project root path')
@click.option('--plan-dir', default=None, help='New plan directory')
@click.option('--auto-gitignore', default=None, type=bool, help='Enable/disable auto .gitignore')
def config(project_name, project_root, plan_dir, auto_gitignore):
    """Configure a project's settings"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        return

    # Use server tool to update
    from .server import configure_project_tool
    result = configure_project_tool(
        project_id=project.id,
        project_root=project_root,
        plan_directory=plan_dir,
        auto_gitignore=auto_gitignore
    )

    if result.get('error'):
        console.print(f"ERROR Error: {result['message']}", style="red")
    else:
        console.print(f"OK Project '{project_name}' updated", style="green")
        if plan_dir:
            console.print(f"  Plan directory: {plan_dir}", style="white")
        if auto_gitignore is not None:
            console.print(f"  Auto .gitignore: {auto_gitignore}", style="white")


@cli.command()
@click.argument('project_name')
def setup_gitignore(project_name):
    """Manually update .gitignore for a project"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        return

    if not project.project_root:
        console.print(f"ERROR Project has no project_root configured", style="red")
        return

    # Update .gitignore
    pattern = project.plan_directory.rstrip('/') + '/'
    updated = update_gitignore(project.project_root, pattern, comment="Flanner")

    if updated:
        console.print(f"OK Added '{pattern}' to .gitignore", style="green")
    else:
        console.print(f"  '{pattern}' already in .gitignore", style="yellow")


@cli.command()
@click.argument('project_name')
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
def delete(project_name, force):
    """Delete a project and all its plan files"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        return

    # Show project info
    console.print(f"\nProject to delete:", style="yellow")
    console.print(f"  Name: {project.name}", style="white")
    console.print(f"  Root: {project.project_root}", style="white")
    console.print(f"  Plan files: {len(project.plan_files)}", style="white")

    # Confirm deletion
    if not force:
        if not click.confirm("\nAre you sure you want to delete this project? This cannot be undone."):
            console.print("Cancelled", style="yellow")
            return

    # Delete project (cascade deletes plan files and versions)
    if delete_project(session, project.id):
        console.print(f"\nOK Project '{project_name}' deleted successfully", style="green")
        console.print(f"  Note: Plan files on disk were NOT deleted. You may want to manually remove:", style="cyan")
        if project.project_root:
            console.print(f"  {project.project_root}/{project.plan_directory}/", style="cyan")
    else:
        console.print(f"ERROR Failed to delete project", style="red")


@cli.command()
@click.option('--port', default=8080, help='Web server port')
@click.option('--host', default='127.0.0.1', help='Web server host')
@click.option('--open-browser', is_flag=True, help='Open browser automatically')
def web(port, host, open_browser):
    """Launch web interface"""
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    console.print(f"\n🚀 Starting Flanner Web Interface...\n", style="cyan bold")
    console.print(f"  Server:    http://{host}:{port}", style="green")
    console.print(f"  Dashboard: http://{host}:{port}/", style="green")
    console.print(f"  Projects:  http://{host}:{port}/projects", style="green")
    console.print(f"\n  Press CTRL+C to stop the server\n", style="yellow")

    # Open browser if requested
    if open_browser:
        import webbrowser
        import time
        time.sleep(1)  # Give server time to start
        webbrowser.open(f"http://{host}:{port}")

    # Start web server
    try:
        import uvicorn
        from .web import app
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        console.print("\n\nOK Web server stopped", style="green")
    except Exception as e:
        console.print(f"\nERROR Error starting web server: {e}", style="red")


@cli.command()
@click.option('--force', is_flag=True, help='Force update if already registered')
@click.option('--type', 'server_type', default='local', help='Server type: local or cloud')
@click.option('--url', default=None, help='Server URL (for cloud type)')
@click.option('--api-key', default=None, help='API key (for cloud type)')
def register(force, server_type, url, api_key):
    """Register MCP server with Claude Code"""
    console.print("\n[MCP] Registering MCP server with Claude Code...\n", style="cyan")

    from .claude_integration import register_mcp_server, get_claude_config_path

    # Check if Claude Code config exists
    config_path = get_claude_config_path()
    if not config_path:
        console.print("ERROR Could not find Claude Code configuration path", style="red")
        console.print("  Please ensure Claude Code is installed", style="yellow")
        return

    # Validate cloud server parameters
    if server_type == 'cloud' and not url:
        console.print("ERROR Cloud server requires --url parameter", style="red")
        return

    # Register server
    success, message = register_mcp_server(
        server_type=server_type,
        server_url=url,
        api_key=api_key,
        force=force
    )

    if success:
        console.print(f"OK {message}", style="green")
        console.print(f"\nConfig location: {config_path}", style="white")

        if server_type == 'local':
            project_dir = Path(__file__).resolve().parent.parent
            console.print(f"Project directory: {project_dir}", style="white")

        console.print("\nNext steps:", style="cyan")
        console.print("  1. Restart Claude Code to load the new MCP server", style="white")
        console.print("  2. Check Claude Code's MCP settings to verify registration", style="white")
        console.print("  3. Test by asking Claude to list projects or create a plan", style="white")
    else:
        console.print(f"ERROR {message}", style="red")


@cli.command()
def unregister():
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
def claude_info():
    """Show Claude Code integration information"""
    console.print("\n" + "="*60, style="cyan")
    console.print("CLAUDE CODE INTEGRATION INFO", style="cyan bold")
    console.print("="*60 + "\n", style="cyan")

    from .claude_integration import get_claude_config_info, print_registration_instructions

    info = get_claude_config_info()

    console.print(f"Configuration Path: {info['config_path']}", style="white")
    console.print(f"Config File Exists: {info['config_exists']}", style="white")
    console.print(f"Server Registered: {info['server_registered']}", style="green" if info['server_registered'] else "yellow")
    console.print(f"Total MCP Servers: {info['total_servers']}", style="white")

    if info['our_server_config']:
        console.print("\nCurrent Configuration:", style="cyan")
        import json
        console.print(json.dumps(info['our_server_config'], indent=2), style="white")
    else:
        console.print("\nNot registered. Use 'flanner register' to add.", style="yellow")
        print_registration_instructions()


@cli.command()
@click.option('--project', default=None, help='Project name to sync (syncs all projects if not specified)')
@click.option('--dry-run', is_flag=True, help='Show what would be imported without actually importing')
def sync(project, dry_run):
    """Scan .plans directory and import existing plan files into database"""
    from pathlib import Path
    from uuid import UUID
    from .frontmatter import parse_frontmatter, validate_frontmatter
    from .utils import hash_content
    from .database import get_plan_file, PlanFileModel, VersionModel
    from datetime import datetime

    console.print("\n" + "="*60, style="cyan")
    console.print("SYNC PLAN FILES", style="cyan bold")
    console.print("="*60 + "\n", style="cyan")

    if dry_run:
        console.print("[DRY RUN MODE - No changes will be made]\n", style="yellow")

    # Initialize database and storage
    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    init_database(str(db_path))
    init_storage(str(mcp_dir))
    session = get_session()

    # Get projects to sync
    from .database import list_projects as db_list_projects

    if project:
        project_model = get_project_by_name(session, project)
        if not project_model:
            console.print(f"ERROR Project '{project}' not found", style="red")
            return
        projects = [project_model]
    else:
        projects = db_list_projects(session)

    if not projects:
        console.print("No projects found. Create a project first with 'flanner init'", style="yellow")
        return

    total_scanned = 0
    total_imported = 0
    total_skipped = 0
    total_errors = 0

    for proj in projects:
        console.print(f"\nProject: {proj.name}", style="cyan bold")
        console.print(f"Plan directory: {proj.project_root}/{proj.plan_directory}", style="white")

        # Get plan directory path
        plan_dir = Path(proj.project_root) / proj.plan_directory

        if not plan_dir.exists():
            console.print(f"  Plan directory doesn't exist yet", style="yellow")
            continue

        # Scan for .md files
        md_files = [f for f in plan_dir.glob("*.md")]

        if not md_files:
            console.print(f"  No plan files found", style="yellow")
            continue

        console.print(f"  Found {len(md_files)} file(s)\n", style="white")

        for file_path in md_files:
            total_scanned += 1
            file_name = file_path.name

            try:
                # Read file content
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Parse frontmatter
                fm_data, body = parse_frontmatter(content)

                if not fm_data.get('mcp_plan_file'):
                    console.print(f"  SKIP {file_name} - Not an MCP plan file", style="yellow")
                    total_skipped += 1
                    continue

                # Validate frontmatter
                if not validate_frontmatter(fm_data):
                    console.print(f"  ERROR {file_name} - Invalid frontmatter", style="red")
                    total_errors += 1
                    continue

                # Extract metadata
                plan_file_id = UUID(fm_data['plan_file_id'])
                plan_name = fm_data['plan_name']
                version = fm_data['version']
                created_by = fm_data.get('created_by', 'unknown')

                # Check if plan file exists in database
                existing_plan_file = get_plan_file(session, plan_file_id)

                if existing_plan_file:
                    # Plan file exists - check if this is a new version
                    if version <= existing_plan_file.current_version:
                        console.print(f"  SKIP {file_name} - Version {version} already in database (current: v{existing_plan_file.current_version})", style="white")
                        total_skipped += 1
                        continue

                    # This is a newer version - import it
                    if dry_run:
                        console.print(f"  WOULD UPDATE {file_name} (plan: {plan_name}, v{existing_plan_file.current_version} -> v{version})", style="green")
                        total_imported += 1
                    else:
                        # Check if this specific version already exists
                        from .database import get_version
                        existing_version = get_version(session, plan_file_id, version)

                        if existing_version:
                            console.print(f"  SKIP {file_name} - Version {version} already exists", style="white")
                            total_skipped += 1
                            continue

                        # Create new version record
                        content_hash = hash_content(body)
                        version_record = VersionModel(
                            plan_file_id=plan_file_id,
                            version=version,
                            file_path=str(file_path),
                            content_hash=content_hash,
                            created_by=created_by,
                            created_at=datetime.utcnow(),
                            notes=f"Imported version {version}"
                        )
                        session.add(version_record)

                        # Update plan file's current_version
                        existing_plan_file.current_version = version
                        existing_plan_file.updated_at = datetime.utcnow()

                        session.commit()

                        console.print(f"  OK UPDATED {file_name} (plan: {plan_name}, v{existing_plan_file.current_version} -> v{version})", style="green")
                        total_imported += 1
                else:
                    # New plan file - import it
                    if dry_run:
                        console.print(f"  WOULD IMPORT {file_name} (plan: {plan_name}, version: {version})", style="green")
                        total_imported += 1
                    else:
                        # Create PlanFileModel directly (since it has a specific UUID from frontmatter)
                        plan_file = PlanFileModel(
                            id=plan_file_id,
                            project_id=proj.id,
                            name=plan_name,
                            description=f"Imported from {file_name}",
                            current_version=version,
                            auto_version=True,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow()
                        )
                        session.add(plan_file)

                        # Create version record
                        content_hash = hash_content(body)
                        version_record = VersionModel(
                            plan_file_id=plan_file_id,
                            version=version,
                            file_path=str(file_path),
                            content_hash=content_hash,
                            created_by=created_by,
                            created_at=datetime.utcnow(),
                            notes=f"Imported version {version}"
                        )
                        session.add(version_record)

                        session.commit()

                        console.print(f"  OK IMPORTED {file_name} (plan: {plan_name}, version: {version})", style="green")
                        total_imported += 1

            except Exception as e:
                console.print(f"  ERROR {file_name} - {str(e)}", style="red")
                total_errors += 1

    # Summary
    console.print("\n" + "="*60, style="cyan")
    console.print("SYNC SUMMARY", style="cyan bold")
    console.print("="*60, style="cyan")
    console.print(f"Files scanned: {total_scanned}", style="white")
    console.print(f"Files imported: {total_imported}", style="green")
    console.print(f"Files skipped: {total_skipped}", style="yellow")
    console.print(f"Errors: {total_errors}", style="red")
    console.print()

    if dry_run and total_imported > 0:
        console.print("Run without --dry-run to actually import the files", style="cyan")


@cli.group()
def jira():
    """JIRA integration commands"""
    pass


@jira.command('config')
@click.argument('project_name')
@click.option('--url', required=True, help='JIRA base URL (e.g., https://company.atlassian.net)')
@click.option('--project-key', default=None, help='Default JIRA project key (e.g., PROJ)')
def jira_config(project_name, url, project_key):
    """Configure JIRA integration for a project"""
    from .jira_utils import is_valid_jira_url, normalize_jira_url
    from .database import create_jira_config

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    # Validate JIRA URL
    if not is_valid_jira_url(url):
        console.print(f"ERROR Invalid JIRA URL format: {url}", style="red")
        console.print("  Expected format: https://company.atlassian.net", style="yellow")
        return

    init_database(str(db_path))
    session = get_session()

    # Get project
    project = get_project_by_name(session, project_name)
    if not project:
        console.print(f"ERROR Project '{project_name}' not found", style="red")
        return

    # Create or update JIRA config
    try:
        normalized_url = normalize_jira_url(url)
        jira_config = create_jira_config(
            session,
            project.id,
            normalized_url,
            project_key
        )

        console.print(f"\nOK JIRA configuration updated for project '{project_name}'", style="green")
        console.print(f"  JIRA URL: {jira_config.jira_url}", style="white")
        if jira_config.jira_project_key:
            console.print(f"  Default Project Key: {jira_config.jira_project_key}", style="white")
    except Exception as e:
        console.print(f"ERROR Failed to configure JIRA: {e}", style="red")


@jira.command('link')
@click.argument('plan_name')
@click.option('--issue', required=True, help='JIRA issue key (e.g., PROJ-123)')
@click.option('--type', 'issue_type', default=None, help='Issue type (Epic, Story, Task, etc.)')
@click.option('--notes', default=None, help='Notes about the link')
@click.option('--project', default=None, help='Project name (uses current directory if not specified)')
def jira_link(plan_name, issue, issue_type, notes, project):
    """Link a plan file to a JIRA issue"""
    from .jira_utils import is_valid_jira_issue_key, format_jira_issue_key, generate_jira_issue_url
    from .database import create_jira_link, get_jira_config

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    # Validate issue key
    formatted_issue = format_jira_issue_key(issue)
    if not is_valid_jira_issue_key(formatted_issue):
        console.print(f"ERROR Invalid JIRA issue key format: {issue}", style="red")
        console.print("  Expected format: PROJECT-123 (uppercase letters, dash, numbers)", style="yellow")
        return

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
        console.print("ERROR Project not found. Specify --project or run from project directory", style="red")
        return

    # Find plan file
    plan_file = None
    for pf in proj.plan_files:
        if pf.name == plan_name:
            plan_file = pf
            break

    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found in project '{proj.name}'", style="red")
        console.print(f"  Available plans: {', '.join([p.name for p in proj.plan_files])}", style="yellow")
        return

    # Create link
    try:
        jira_link = create_jira_link(
            session,
            plan_file.id,
            formatted_issue,
            issue_type,
            notes
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


@jira.command('unlink')
@click.argument('plan_name')
@click.option('--issue', default=None, help='JIRA issue key to unlink (unlinks all if not specified)')
@click.option('--all', 'unlink_all', is_flag=True, help='Unlink all JIRA issues')
@click.option('--project', default=None, help='Project name')
def jira_unlink(plan_name, issue, unlink_all, project):
    """Unlink a plan file from JIRA issue(s)"""
    from .jira_utils import format_jira_issue_key
    from .database import delete_jira_link_by_key, delete_all_jira_links

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

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
        return

    # Find plan file
    plan_file = None
    for pf in proj.plan_files:
        if pf.name == plan_name:
            plan_file = pf
            break

    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found", style="red")
        return

    # Unlink
    try:
        if unlink_all or not issue:
            count = delete_all_jira_links(session, plan_file.id)
            if count > 0:
                console.print(f"\nOK Unlinked {count} JIRA issue(s) from '{plan_name}'", style="green")
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


@jira.command('links')
@click.option('--project', default=None, help='Project name (shows all projects if not specified)')
def jira_links(project):
    """List all JIRA links"""
    from .database import list_all_jira_links, get_jira_config
    from .jira_utils import generate_jira_issue_url

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

    init_database(str(db_path))
    session = get_session()

    # Get projects
    if project:
        proj = get_project_by_name(session, project)
        if not proj:
            console.print(f"ERROR Project '{project}' not found", style="red")
            return
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
        jira_config = get_jira_config(session, proj.id)

        for link in links:
            issue_key = link['jira_issue_key']
            table.add_row(
                link['plan_file_name'],
                issue_key,
                link['jira_issue_type'] or "--",
                link['created_at'].strftime("%Y-%m-%d") if link['created_at'] else "N/A"
            )

        console.print(table)


@jira.command('show')
@click.argument('plan_name')
@click.option('--project', default=None, help='Project name')
def jira_show(plan_name, project):
    """Show detailed JIRA links for a plan file"""
    from .database import get_jira_links, get_jira_config
    from .jira_utils import generate_jira_issue_url

    mcp_dir = get_mcp_dir()
    db_path = mcp_dir / "data.db"

    if not db_path.exists():
        console.print("ERROR Database not initialized. Run 'flanner init' first.", style="red")
        return

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
        return

    # Find plan file
    plan_file = None
    for pf in proj.plan_files:
        if pf.name == plan_name:
            plan_file = pf
            break

    if not plan_file:
        console.print(f"ERROR Plan '{plan_name}' not found", style="red")
        return

    # Get links
    links = get_jira_links(session, plan_file.id)

    if not links:
        console.print(f"\nNo JIRA links found for '{plan_name}'", style="yellow")
        return

    console.print(f"\nPlan: {plan_name}", style="cyan bold")
    console.print(f"JIRA Links:\n", style="white")

    # Get JIRA config
    jira_config = get_jira_config(session, proj.id)

    for link in links:
        console.print(f"  • {link.jira_issue_key}", style="green")
        if link.jira_issue_type:
            console.print(f"    Type: {link.jira_issue_type}", style="white")

        if jira_config:
            url = generate_jira_issue_url(jira_config.jira_url, link.jira_issue_key)
            console.print(f"    URL: {url}", style="cyan")

        if link.notes:
            console.print(f"    Notes: {link.notes}", style="white")

        console.print(f"    Linked: {link.created_at.strftime('%Y-%m-%d %H:%M')} by {link.created_by}", style="dim")
        console.print()


if __name__ == '__main__':
    cli()
