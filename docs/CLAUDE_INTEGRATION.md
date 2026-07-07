# Claude Code Integration Guide

## Overview

The Flanner now features **automatic Claude Code integration**! The MCP server is automatically registered with Claude Code when you run `flanner init`, making it seamless to use plan file management directly from Claude.

## ✨ Features

### Automatic Registration
- ✅ **Auto-detect** Claude Code installation
- ✅ **Auto-configure** MCP server settings
- ✅ **Auto-update** configuration if paths change
- ✅ **Verify** registration on every `status` check

### Manual Control
- 🔧 Register/unregister at any time
- 🔧 Force update configuration
- 🔧 View detailed integration status
- 🔧 Support for future cloud-based servers

### Smart Checking
- 🔍 Every `init` command checks and updates registration
- 🔍 Every `status` command verifies configuration
- 🔍 Automatic detection of configuration drift

## 🚀 Quick Start

### 1. Initialize (Auto-Registration)

```bash
# Run init - MCP server is automatically registered!
flanner init

# Output includes:
# ✓ Initialized Flanner...
# 🔌 Registering MCP server with Claude Code...
# ✓ MCP server registered in Claude Code
#   You may need to restart Claude Code for changes to take effect
```

### 2. Verify Registration

```bash
# Check status
flanner status
```

**Output shows:**
```
============================================================
MCP PLAN MANAGER STATUS
============================================================

Server Status: Stopped
Database: C:\Users\...\. flanners\data.db
Projects: 1
Total Plan Files: 3

------------------------------------------------------------
CLAUDE CODE INTEGRATION
------------------------------------------------------------

Config Path: C:\Users\...\Claude\claude_desktop_config.json
MCP Server: Registered & Valid
```

### 3. View Detailed Info

```bash
flanner claude-info
```

**Shows:**
- Configuration file location
- Registration status
- Current server configuration
- Manual registration instructions (if not registered)

### 4. Restart Claude Code

After registration, restart Claude Code to load the MCP server.

### 5. Test with Claude

Ask Claude to interact with the plan manager:

```
User: "List all my projects in the plan manager"
User: "Create a new architecture plan for my current project"
User: "Show me the version history of the architecture plan"
```

## 🔧 CLI Commands

### View Status
```bash
# Show integration status (part of overall status)
flanner status

# Show detailed Claude Code integration info
flanner claude-info
```

### Manual Registration
```bash
# Register MCP server (if auto-registration failed or was skipped)
flanner register

# Force update configuration (if paths changed)
flanner register --force

# Skip Claude integration during init
flanner init --skip-claude
```

### Unregister
```bash
# Remove MCP server from Claude Code
flanner unregister
```

### Future: Cloud Server Registration
```bash
# Register cloud-based MCP server (planned for future)
flanner register --type cloud \
  --url https://your-server.com \
  --api-key YOUR_API_KEY
```

## 📁 Configuration File Location

The CLI automatically detects Claude Code's configuration file:

**Windows:**
```
C:\Users\YourName\AppData\Roaming\Claude\claude_desktop_config.json
```

**macOS:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Linux:**
```
~/.config/claude/claude_desktop_config.json
```

## 🔍 How It Works

### On `init` Command:

1. **Check Registration**: Is the MCP server already registered?
2. **Verify Configuration**: If registered, is the configuration correct?
3. **Update if Needed**: If paths changed, update the configuration
4. **Register if Missing**: If not registered, add it to claude_desktop_config.json
5. **Notify User**: Show success message and next steps

### On `status` Command:

1. **Check Registration**: Show if server is registered
2. **Validate Configuration**: Check if current config matches expected
3. **Show Action Needed**: If not registered or outdated, suggest action

### Configuration Structure:

```json
{
  "mcpServers": {
    "flanner-manager": {
      "command": "python",
      "args": ["-m", "flanner.server"],
      "cwd": "C:\\path\\to\\mcp-cli",
      "env": {}
    }
  }
}
```

## 🎯 Use Cases

### Scenario 1: First Installation
```bash
# Install and initialize
pip install -e .
flanner init

# ✓ MCP server automatically registered
# ✓ Ready to use with Claude immediately
```

### Scenario 2: Moved Project Directory
```bash
# You moved the mcp-cli folder
# Run init or status to detect and update
flanner init

# ✓ Configuration automatically updated with new path
```

### Scenario 3: Multiple Developers
```bash
# Each developer runs init on their machine
flanner init

# ✓ Each gets correct path for their system
# ✓ No manual configuration needed
```

### Scenario 4: Troubleshooting
```bash
# Check what's wrong
flanner claude-info

# Shows detailed status and manual instructions
# Can manually register if needed
flanner register --force
```

## 🔮 Future: Cloud Server Support

The system is designed to support cloud-based MCP servers:

```bash
# Local server (current)
flanner register --type local

# Cloud server (future)
flanner register --type cloud \
  --url https://api.yourcompany.com/mcp \
  --api-key sk_live_...
```

**Configuration for cloud:**
```json
{
  "mcpServers": {
    "flanner-manager": {
      "type": "cloud",
      "url": "https://api.yourcompany.com/mcp",
      "apiKey": "sk_live_..."
    }
  }
}
```

**Benefits of cloud deployment:**
- 📡 Share plan files across team
- 🔐 Centralized access control
- 💾 Persistent storage
- 🔄 Real-time synchronization

## 🐛 Troubleshooting

### Issue: "Could not find Claude Code configuration path"

**Solution:**
- Ensure Claude Code is installed
- Check if `~/.claude/claude_desktop_config.json` exists (create manually if needed)
- Try manual registration

### Issue: "MCP server registered but not working in Claude"

**Solutions:**
1. Restart Claude Code completely
2. Check Claude Code's MCP settings UI to verify registration
3. Run `flanner claude-info` to verify configuration
4. Re-register with `flanner register --force`

### Issue: "Configuration path is different"

**Solution:**
Edit `flanner/claude_integration.py` and add your path to `possible_paths`:
```python
if system == "Windows":
    possible_paths = [
        home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json",
        home / ".claude" / "claude_desktop_config.json",
        home / "Your" / "Custom" / "Path" / "claude_desktop_config.json",  # Add here
    ]
```

### Issue: "Want to disable auto-registration"

**Solution:**
```bash
# Skip Claude integration during init
flanner init --skip-claude

# Or unregister after init
flanner unregister
```

## 📊 Status Indicators

### In `status` command:

**Registered & Valid** (Green):
- ✅ MCP server is registered
- ✅ Configuration is correct
- ✅ No action needed

**Registered (config outdated)** (Yellow):
- ⚠️ MCP server is registered
- ⚠️ Configuration has changed
- 💡 Run `flanner init` to update

**Not Registered** (Yellow):
- ⚠️ MCP server not found in Claude config
- 💡 Run `flanner init` or `flanner register`

### In `claude-info` command:

Shows complete details:
- Configuration file path
- Whether file exists
- Registration status
- All MCP servers configured
- Current configuration (JSON)

## 🔐 Security Notes

- Configuration files are stored locally on your machine
- No credentials are transmitted during registration
- Cloud server support will require API keys (future)
- API keys should be stored securely (environment variables recommended)

## 📝 Summary

**What You Get:**
1. ✅ **Zero-config setup** - Just run `flanner init`
2. ✅ **Auto-updates** - Configuration stays current
3. ✅ **Smart checking** - Verifies on every status check
4. ✅ **Manual control** - Override when needed
5. ✅ **Future-proof** - Ready for cloud deployment

**Commands to Remember:**
```bash
flanner init          # Auto-register on first run
flanner status        # Check registration status
flanner claude-info   # View detailed info
flanner register      # Manual registration
flanner unregister    # Remove registration
```

**After Setup:**
- Restart Claude Code
- Ask Claude to interact with plan manager
- Enjoy seamless plan file management!

---

*Built for Flanner - December 2025*
