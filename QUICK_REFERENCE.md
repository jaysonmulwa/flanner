# Flanner - Quick Reference Card

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install flanner command (recommended)
pip install -e .
```

## Core Commands

```bash
# Initialize Flanner in your project
flanner init

# Check status
flanner status

# List projects
flanner list

# List plan files for a project
flanner list --project PROJECT_NAME
```

## Plan File Management

```bash
# Sync existing files into database
flanner sync

# Sync with preview (dry run)
flanner sync --dry-run

# Sync specific project only
flanner sync --project PROJECT_NAME
```

## Web Interface

```bash
# Launch web UI (default port 8080)
flanner web

# Launch and auto-open browser
flanner web --open-browser

# Custom port
flanner web --port 3000
```

## Claude Code Integration

```bash
# Show integration status
flanner claude-info

# Register MCP server
flanner register

# Force update configuration
flanner register --force

# Unregister from Claude
flanner unregister
```

## Project Configuration

```bash
# Configure project settings
flanner config PROJECT_NAME --plan-dir docs/plans

# Update .gitignore manually
flanner setup-gitignore PROJECT_NAME
```

## MCP Server

```bash
# Start server (shows connection instructions)
flanner start

# Stop server
flanner stop
```

## Common Workflows

### First Time Setup
```bash
cd your-project
flanner init
# Creates ~/.flanner/data.db
# Registers with Claude Code
# Sets up project
```

### Creating Plan Files

**Via Claude (Recommended):**
```
Ask Claude: "Create a new architecture plan for this project"
```

**Manual Creation:**
```bash
# 1. Create file in .plans/
# 2. Add frontmatter with UUIDs
# 3. Run sync
flanner sync
```

### Updating Plan Versions

```bash
# 1. Edit the plan file
# 2. Increment version: in frontmatter
# 3. Update created_at:
# 4. Run sync
flanner sync
```

### Viewing Plans

**Web Interface:**
```bash
flanner web --open-browser
# Navigate to: Projects → Select Project → View Plans
```

**Via Claude:**
```
Ask Claude: "Show me all plan files"
Ask Claude: "Show version history for architecture plan"
```

## Directory Structure

```
~/.flanner/              # Flanner data directory
├── data.db              # SQLite database
└── server.pid           # Server process ID

your-project/
├── .git/
├── .gitignore           # Auto-updated
├── .plans/              # Plan files (git-ignored)
│   ├── architecture.md
│   └── api-design.md
├── src/
└── ...
```

## Plan File Format

```yaml
---
mcp_plan_file: true
project_id: uuid-here
plan_file_id: uuid-here
plan_name: architecture
version: 1
created_at: '2025-12-25T10:00:00Z'
created_by: user
---

# Your Plan Title

Your plan content here...
```

## Troubleshooting

### Command not found
```bash
# Use Python module instead
python -m src.cli --help

# Or install properly
pip install -e .
```

### Database not initialized
```bash
flanner init
```

### MCP server not registered
```bash
flanner register
# Then restart Claude Code
```

### Plan files not showing
```bash
flanner sync
```

## Tips

- ✅ Run `flanner init` in each project
- ✅ Use `sync --dry-run` before syncing
- ✅ Check `status` regularly
- ✅ Keep version numbers sequential
- ✅ Restart Claude after registration

## Getting Help

```bash
# General help
flanner --help

# Command-specific help
flanner init --help
flanner sync --help
flanner web --help
```

## Documentation

- `README.md` - Full documentation
- `INSTALLATION.md` - Installation guide
- `VERSIONING_GUIDE.md` - How versioning works
- `PLAN_FILE_MANAGEMENT.md` - Plan file details
- `CLAUDE_INTEGRATION.md` - Claude Code integration

---

**Version:** 1.0.0
**Quick Reference for:** Flanner
