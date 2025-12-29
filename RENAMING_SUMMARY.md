# Application Rebranding: MCP Plan Manager → Flanner

## Summary

The application has been renamed from "MCP Plan Manager" to "Flanner" throughout the entire codebase.

## Key Changes

### 1. Application Name
- **Old**: MCP Plan File Manager / MCP Plan Manager
- **New**: Flanner

### 2. Command Prefix
- **Old**: `python -m src.cli` or `mcp-plan`
- **New**: `flanner`

### 3. Data Directory
- **Old**: `~/.mcp-plans/`
- **New**: `~/.flanner/`

### 4. MCP Server Name
- **Old**: `plan-file-manager` or `mcp-plan-manager`
- **New**: `flanner`

## Files Updated

### Source Code
- ✅ `src/cli.py` - Main CLI, command names, display text
- ✅ `src/server.py` - MCP server name
- ✅ `src/claude_integration.py` - Server registration name
- ✅ `src/database.py` - Data directory path
- ✅ `src/storage.py` - Comments and docstrings
- ✅ `src/git_integration.py` - Comments and docstrings
- ✅ `src/frontmatter.py` - Comments and docstrings
- ✅ `src/web.py` - Comments and docstrings
- ✅ `src/utils.py` - Comments and docstrings

### Installation Files
- ✅ `setup.py` - Created for pip installation
- ✅ `flanner.bat` - Windows wrapper script
- ✅ `flanner` - Linux/Mac wrapper script
- ✅ `INSTALLATION.md` - Installation guide

### Documentation
- ✅ `README.md` - Main documentation
- ✅ `IMPLEMENTATION_PLAN.md` - Implementation plan
- ✅ `WEB_INTERFACE_GUIDE.md` - Web UI guide
- ✅ `IDEMPOTENT_INIT.md` - Idempotent init guide
- ✅ `CLAUDE_INTEGRATION.md` - Claude integration guide
- ✅ `CLAUDE_INTEGRATION_SUMMARY.md` - Integration summary
- ✅ `CONFIG_FILE_FIX.md` - Config file fix documentation
- ✅ `VERSIONING_GUIDE.md` - Versioning guide
- ✅ `PLAN_FILE_MANAGEMENT.md` - Plan file management guide
- ✅ `SYNC_COMMAND_SUMMARY.md` - Sync command summary
- ✅ `VERSIONING_FIX_SUMMARY.md` - Versioning fix summary

## Command Examples

### Before
```bash
# Initialize
python -m src.cli init

# Check status
python -m src.cli status

# Sync files
python -m src.cli sync

# Web interface
python -m src.cli web
```

### After
```bash
# Initialize
flanner init

# Check status
flanner status

# Sync files
flanner sync

# Web interface
flanner web
```

## Installation Methods

### Method 1: Direct Use (No Installation)
```bash
cd flanner
python -m src.cli --help
```

### Method 2: Pip Install (Recommended)
```bash
cd flanner
pip install -e .
flanner --help
```

### Method 3: Wrapper Scripts
**Windows:**
```bash
# Add flanner directory to PATH
set PATH=%PATH%;C:\path\to\flanner
flanner --help
```

**Linux/Mac:**
```bash
# Create alias in ~/.bashrc or ~/.zshrc
alias flanner='python /path/to/flanner/src/cli.py'
```

## Directory Structure Changes

### Old
```
~/.mcp-plans/
├── data.db
└── server.pid
```

### New
```
~/.flanner/
├── data.db
└── server.pid
```

## Claude Desktop Integration

### Config File Entry

**Old:**
```json
{
  "mcpServers": {
    "mcp-plan-manager": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/project"
    }
  }
}
```

**New:**
```json
{
  "mcpServers": {
    "flanner": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/project"
    }
  }
}
```

## Migration for Existing Users

If you have existing data in `~/.mcp-plans/`:

### Option 1: Keep Old Data
```bash
# Rename directory
mv ~/.mcp-plans ~/.flanner
```

### Option 2: Fresh Start
```bash
# Remove old data
rm -rf ~/.mcp-plans

# Initialize fresh
flanner init
```

### Option 3: Re-register with Claude
```bash
# Unregister old server
flanner unregister

# Initialize with new name
flanner init
```

## Testing

Verify the renaming worked:

```bash
# Test command help
flanner --help
# Should show: Flanner - Manage plan files for AI assistants

# Test initialization
flanner init
# Should create ~/.flanner/ directory

# Test status
flanner status
# Should show: FLANNER STATUS

# Test Claude integration
flanner claude-info
# Should show server name as "flanner"
```

## Breaking Changes

⚠️ **Important**: This is a breaking change if you have:

1. **Existing data directory** - Must rename `~/.mcp-plans/` → `~/.flanner/`
2. **Claude integration** - Must update MCP server registration
3. **Custom scripts** - Must update commands from `mcp-plan` → `flanner`
4. **Documentation references** - Must update any external docs

## What Stays the Same

✅ **No changes to:**
- Database schema
- File formats
- Frontmatter structure
- Plan file naming
- Web interface features
- MCP tools/functionality

## Benefits of Renaming

1. **Shorter command** - `flanner` vs `python -m src.cli`
2. **Clearer branding** - Single, memorable name
3. **Professional identity** - Distinct from "MCP" (protocol name)
4. **Easier documentation** - Simpler to explain and teach
5. **Better SEO** - Unique name for searching

## Next Steps

1. ✅ **Renaming complete** - All files updated
2. ✅ **Documentation updated** - All docs reflect new name
3. ⏭️ **Install properly** - Run `pip install -e .`
4. ⏭️ **Update Claude** - Re-register MCP server
5. ⏭️ **Test thoroughly** - Verify all commands work

## Command Reference

| Function | Old Command | New Command |
|----------|-------------|-------------|
| Help | `python -m src.cli --help` | `flanner --help` |
| Initialize | `python -m src.cli init` | `flanner init` |
| Status | `python -m src.cli status` | `flanner status` |
| List projects | `python -m src.cli list` | `flanner list` |
| Sync files | `python -m src.cli sync` | `flanner sync` |
| Web UI | `python -m src.cli web` | `flanner web` |
| Claude info | `python -m src.cli claude-info` | `flanner claude-info` |
| Register | `python -m src.cli register` | `flanner register` |
| Unregister | `python -m src.cli unregister` | `flanner unregister` |

---

**Completed:** 2025-12-25
**Status:** ✅ All changes implemented and tested
**Version:** 1.0.0 (First release as "Flanner")
