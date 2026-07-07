# Flanner

A comprehensive plan file management system that integrates with Claude Code and other AI assistants through MCP (Model Context Protocol).

## Installation

```bash
# Clone or download this repository
cd flanner

# Install (provides the 'flanner' command)
pip install -e .

flanner --help
```

## Features

- 🤖 **MCP Integration**: Exposes plan file management tools to Claude/Codex
- 📁 **Git Protection**: Automatically prevents plan files from being committed to git
- 🔖 **Smart Identification**: YAML frontmatter clearly marks managed plan files
- 📝 **Automatic Versioning**: Tracks changes with semantic versioning
- 🌐 **Web Interface**: Browser-based UI for viewing and editing plans
- ⚙️ **Configurable**: Customize plan directory per project
- 🚀 **Future-Ready**: Architecture supports cloud deployment and Jira integration

## Quick Start

### 1. Installation

**Windows:**
```bash
cd flanner
python -m venv venv
venv\Scripts\activate
pip install -e .
```

**Linux/Mac:**
```bash
cd flanner
python -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. Initialize Git Repository (if not already done)

```bash
# Make sure you're in a git repository
git init
```

### 3. Initialize the Plan Manager

```bash
# Run from the flanner directory
flanner init
```

**✨ New: Idempotent Init** - Safe to run multiple times! The init command:
- Detects your git repository root
- Creates a `.plans/` directory
- Updates your `.gitignore` to exclude plan files
- Initializes the database at `~/.flanners/data.db`
- **Automatically registers MCP server with Claude Code**
- **Skips project creation if one already exists for this directory**
- Prompts you to create a project (only if needed)

**Example output:**
```
✓ Initialized MCP Plan Manager at C:\Users\YourName\.flanners
✓ Database created at C:\Users\YourName\.flanners\data.db
✓ Detected git repository at: C:\Users\YourName\mcp_examples\flanner
Enter project name [flanner]: my-project
✓ Created project: my-project
✓ Plan directory: C:\Users\YourName\mcp_examples\flanner\.plans
✓ Updated .gitignore to exclude plan files
```

### 4. Verify Installation

Check that everything is working:

```bash
flanner status
```

Should show:
```
============================================================
MCP PLAN MANAGER STATUS
============================================================

Server Status: Stopped
Database: C:\Users\YourName\.flanners\data.db
Projects: 1
Total Plan Files: 0
```

### 5. Test Creating a Plan File

You can test the MCP server directly:

```bash
python test_server.py
```

This will create a test plan file and verify UUIDs are working correctly.

### 6. Configure Claude Code (Automatic)

**The MCP server is automatically registered with Claude Code during `flanner init`!**

No manual configuration needed. The `init` command will:
- ✅ Detect your Claude Code installation
- ✅ Register the MCP server automatically
- ✅ Configure the correct paths
- ✅ Update your `claude_desktop_config.json`

**Check registration status:**
```bash
flanner status
```

**View detailed integration info:**
```bash
flanner claude-info
```

**Manually register/update if needed:**
```bash
# Register MCP server (local)
flanner register

# Force update configuration
flanner register --force

# Register cloud server (future use)
flanner register --type cloud --url https://your-server.com --api-key YOUR_KEY

# Unregister
flanner unregister
```

### 7. Start Using with Claude

Restart Claude Code, then you can ask Claude to create and manage plan files:

```
User: "Claude, list all projects in the plan manager"
User: "Claude, create a new architecture plan for our project"
User: "Claude, show me the history of the architecture plan"
```

Claude will use the MCP tools to:
- List projects with `list_projects()`
- Create plan files with `create_plan_file_tool()`
- Update plans with `update_plan_file_tool()`
- View history with `get_plan_history_tool()`

## Plan File Format

All plan files managed by this system include YAML frontmatter:

```markdown
---
mcp_plan_file: true
plan_manager_version: '1.0'
project_id: 3d816ecd-489a-4fa0-abe2-15ec93f60d5a
project_name: my-app
plan_file_id: 59c34f9c-8471-47fc-97f2-8dcfefa15434
plan_name: architecture
version: 2
created_at: 2025-01-15T10:30:00Z
created_by: claude
---

# Architecture Plan

Your plan content here...
```

**Note**: All IDs use UUIDs for global uniqueness and future cloud compatibility.

## CLI Commands

```bash
# Initialize plan manager (creates database and optionally a project)
flanner init [--project-root PATH] [--plan-dir DIR]

# Check server status (shows projects, plan files, database path)
flanner status

# List all projects
flanner list

# List plan files for a specific project
flanner list --project PROJECT_NAME

# Sync existing plan files from .plans/ into database
flanner sync [--project PROJECT_NAME] [--dry-run]

# Configure a project's settings
flanner config PROJECT_NAME [--project-root PATH] [--plan-dir DIR] [--auto-gitignore BOOL]

# Manually update .gitignore for a project
flanner setup-gitignore PROJECT_NAME

# Start MCP server (displays connection instructions)
flanner start [--port 8080]

# Stop server (not yet implemented - server runs via stdio)
flanner stop

# Launch web interface
flanner web [--port 8080] [--host 127.0.0.1] [--open-browser]

# Claude Code Integration Commands (NEW!)
# Show Claude Code integration status and configuration
flanner claude-info

# Register MCP server with Claude Code (auto-registration happens during init)
flanner register [--force] [--type local|cloud] [--url URL] [--api-key KEY]

# Unregister MCP server from Claude Code
flanner unregister
```

### Example Usage

```bash
# Check status
flanner status

# List all projects
flanner list

# View plan files for a project
flanner list --project my-project

# Sync existing plan files into database (dry run first)
flanner sync --dry-run
flanner sync

# Sync only a specific project
flanner sync --project my-project

# Change plan directory for a project
flanner config my-project --plan-dir docs/plans

# Launch web interface on default port (8080)
flanner web

# Launch web interface and auto-open browser
flanner web --open-browser

# Launch on custom port
flanner web --port 3000
```

## Web Interface

### Features

The web interface provides a comprehensive UI for managing plan files:

- **Dashboard**: Overview of all projects with statistics and recent activity
- **Project Management**: Create and view projects
- **Plan File Viewer**: Beautiful markdown rendering with syntax highlighting
- **Version Selector**: Switch between different versions of plan files
- **Plan File Editor**: Edit plans with live markdown preview
- **Version History**: Timeline view of all changes with metadata
- **Responsive Design**: Works on desktop, tablet, and mobile

### Launching the Web UI

```bash
# Start the web server
flanner web

# Or with auto-open browser
flanner web --open-browser
```

Access at: `http://localhost:8080`

### Web Interface Screenshots

The web interface includes:
- **Dashboard** (`/`): Project overview with stats
- **Projects** (`/projects`): List and manage all projects
- **Project Detail** (`/projects/{id}`): View project's plan files
- **Plan Viewer** (`/plans/{id}`): View plan with markdown rendering
- **Plan Editor** (`/plans/{id}/edit`): Edit and create new versions
- **Version History** (`/plans/{id}/history`): Timeline of all versions

## How It Works

### For Developers

- Plan files are stored in your project repository (default: `.plans/`)
- Automatically excluded from git via `.gitignore`
- Clear identification via frontmatter
- Configurable directory per project

### For Claude/Codex

- Calls `get_plan_config()` to know where to create files
- Automatic frontmatter generation
- Version management happens transparently
- No need to ask users where files should go

## Project Structure

```
flanner/
├── flanner/
│   ├── server.py           # MCP Server
│   ├── cli.py              # CLI tool
│   ├── web.py              # Web server
│   ├── database.py         # Database operations
│   ├── models.py           # Pydantic models
│   ├── storage.py          # File operations
│   ├── git_integration.py  # Git operations
│   ├── frontmatter.py      # Frontmatter handling
│   ├── utils.py            # Utilities
│   └── web/                # Templates and static assets
├── tests/                  # Pytest suite
├── docs/                   # Guides (installation, Jira, versioning, web UI)
└── pyproject.toml          # Packaging and dependencies
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

### Performance

Reproduce with `python benchmarks/bench.py` (throwaway temp database). Measured on
Windows 11, Python 3.12, SQLite on NVMe:

| Operation | Median |
|---|---|
| `create_project` | ~36 ms |
| `create_plan_file` (2.4 KB body) | ~40 ms (n=100) |
| `list_plan_files` (100 plans) | ~5 ms (n=20) |

### Database Location

The database is stored at:
- **Windows**: `C:\Users\YourName\.flanners\data.db`
- **Linux/Mac**: `~/.flanners/data.db`

### Plan Files Location

Plan files are stored in your project repository:
```
your-project/
├── .git/
├── .gitignore          # Auto-updated to exclude .plans/
├── .plans/             # Your plan files (git-ignored)
│   ├── architecture_v1.md
│   ├── architecture_v2.md
│   └── api-design_v1.md
├── src/
└── ...
```

## Future Enhancements

- ☁️ Cloud deployment with PostgreSQL + S3
- 🔗 Jira integration for ticket linking
- 🔍 Full-text search for plan files
- 👥 Multi-user collaboration
- 🔄 Real-time updates

## License

MIT

## Contributing

Contributions welcome! See the guides in [docs/](docs/) for architecture and usage details.
