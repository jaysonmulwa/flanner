# Idempotent Init - Smart Project Detection

## Overview

The `flanner init` command is now **idempotent** - safe to run multiple times in the same directory without creating duplicate projects or prompting for project details again.

## ✨ What This Means

### Before (Old Behavior)
```bash
# First time
cd /my-project
flanner init
# Creates project "my-project"

# Second time (same directory)
flanner init
# Would try to create "my-project" again
# Error: Project already exists
# User frustrated
```

### After (New Behavior)
```bash
# First time
cd /my-project
flanner init
# Creates project "my-project"
# Registers MCP server

# Second time (same directory)
flanner init
# Detects existing project
# Skips project creation
# Still updates MCP registration
# User happy!
```

## 🎯 Key Features

### 1. Smart Project Detection
- Checks if current directory already has a project
- Compares by `project_root` path
- Uses normalized paths (handles \ vs /)
- No duplicate projects created

### 2. Always Updates MCP Registration
- Even if project exists, MCP server gets registered/updated
- Ensures Claude Code integration stays current
- Useful after moving project directory
- Safe to run for MCP troubleshooting

### 3. Clear User Feedback
```bash
flanner init

# Output when project exists:
✓ Initialized Flanner at...
✓ Database created at...
🔌 Registering MCP server with Claude Code...
✓ MCP server registered in Claude Code

✓ Detected git repository at: C:\my-project
✓ Project already exists: my-project
  Plan directory: .plans
  Plan files: 3

  💡 Tip: MCP server registration still completed above.
  You can run 'flanner init' anytime to ensure everything is set up!
```

### 4. Force Override Available
```bash
# Want to create a new project even if one exists?
flanner init --force-new-project

# Will prompt for new project name
# Useful for multi-project repositories
```

## 📋 Use Cases

### Use Case 1: Re-running Init for MCP Registration
```bash
# You updated the code, moved the folder, or Claude isn't connecting
flanner init

# ✓ Won't ask for project details again
# ✓ Will update MCP registration
# ✓ Quick and painless
```

### Use Case 2: New Team Member Setup
```bash
# New developer clones the repo
git clone https://github.com/company/project.git
cd project

# Runs init (might not know if project exists)
flanner init

# ✓ If project exists: Skips creation, registers MCP
# ✓ If project missing: Creates it, registers MCP
# ✓ Always works correctly
```

### Use Case 3: After Moving Project Directory
```bash
# You moved the project folder
mv ~/old-location/project ~/new-location/project
cd ~/new-location/project

# Update MCP registration with new path
flanner init

# ✓ Detects existing project
# ✓ Updates MCP server config with new cwd
# ✓ Claude Code stays connected
```

### Use Case 4: Multi-Project Repository
```bash
# Your repo has multiple sub-projects
cd /monorepo/frontend
flanner init --force-new-project
# Creates "frontend" project

cd /monorepo/backend
flanner init --force-new-project
# Creates "backend" project

# Both projects managed separately
```

## 🔧 How It Works

### Detection Algorithm

```
┌─────────────────────────────────────┐
│ User runs: flanner init            │
└─────────────────────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ Initialize database & storage       │
└─────────────────────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ Register/Update MCP Server          │
│ (Always happens)                    │
└─────────────────────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ Detect git repository root          │
└─────────────────────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ Check database for existing project │
│ WHERE project_root = current_dir    │
└─────────────────────────────────────┘
        ↓                    ↓
   [Found]              [Not Found]
        ↓                    ↓
┌─────────────┐      ┌──────────────┐
│Show existing│      │Prompt for    │
│project info │      │project name  │
└─────────────┘      └──────────────┘
        ↓                    ↓
┌─────────────┐      ┌──────────────┐
│Skip creation│      │Create project│
└─────────────┘      └──────────────┘
        ↓                    ↓
        └────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ Done - Ready to use!                │
└─────────────────────────────────────┘
```

### Path Normalization

The system handles different path formats:

```python
# All these are treated as the same project:
"C:\Users\Name\project"
"C:\\Users\\Name\\project"
"C:/Users/Name/project"

# Uses os.path.normpath() for comparison
```

## 🧪 Testing

Run the test suite:

```bash
python test_idempotent_init.py
```

**Tests verify:**
1. ✅ get_project_by_root() returns None for non-existent project
2. ✅ Can create a new project
3. ✅ Can find project by root path
4. ✅ Path normalization works (\ vs /)
5. ✅ Project count is correct
6. ✅ Second init finds existing project
7. ✅ No duplicate projects created

## 📊 Comparison: Before vs After

| Scenario | Old Behavior | New Behavior |
|----------|--------------|--------------|
| Run init twice | Error: Duplicate project | Skips creation, updates MCP |
| Move project folder | MCP config outdated | Auto-updates MCP registration |
| New team member | Might create duplicate | Safe - detects existing |
| Check setup | No easy way | Run `init` - always safe |
| MCP troubleshooting | Manual config editing | Run `init` - auto-fixes |

## ⚙️ Configuration Options

### Standard Usage (Recommended)
```bash
# Safe to run anytime
flanner init
```

### Skip Claude Integration
```bash
# Don't register with Claude Code
flanner init --skip-claude
```

### Force New Project
```bash
# Create new project even if one exists
flanner init --force-new-project
```

### Custom Project Root
```bash
# Specify project root explicitly
flanner init --project-root /path/to/project
```

### Custom Plan Directory
```bash
# Use different plan directory
flanner init --plan-dir docs/plans
```

### Combine Options
```bash
# All options can be combined
flanner init \
  --project-root /my/project \
  --plan-dir .planning \
  --force-new-project \
  --skip-claude
```

## 💡 Best Practices

### 1. Always Safe to Run
```bash
# Uncertain if you ran init before?
# Just run it - it's safe!
flanner init
```

### 2. After Cloning Repository
```bash
git clone https://github.com/user/repo.git
cd repo
flanner init  # Sets everything up
```

### 3. MCP Registration Issues
```bash
# Claude Code not connecting?
flanner init  # Re-registers MCP server
# Then restart Claude Code
```

### 4. Check Current Status First
```bash
# See what's already set up
flanner status

# Then run init if needed
flanner init
```

## 🔍 Troubleshooting

### Q: I ran init but it created a duplicate project
**A:** This shouldn't happen with the new logic. Check:
- Are you in the same directory?
- Run `flanner list` to see all projects
- Check if project_root paths are identical

### Q: How do I know if I need to run init?
**A:** Run `flanner status` first:
- If "MCP Server: Not Registered" → Run init
- If "Project already exists" → You're set, but init is still safe

### Q: I want to reset everything
**A:** Delete the database and re-init:
```bash
rm ~/.flanners/data.db
flanner init
```

### Q: Can I have multiple projects in one repo?
**A:** Yes! Use `--force-new-project` and `--project-root`:
```bash
cd /monorepo/app1
flanner init --force-new-project

cd /monorepo/app2
flanner init --force-new-project
```

## 📈 Benefits

### For Individual Developers
1. ✅ No more "project already exists" errors
2. ✅ Safe to re-run for MCP troubleshooting
3. ✅ Clear feedback on what was done
4. ✅ Less cognitive load

### For Teams
1. ✅ Consistent setup process
2. ✅ New members can't create duplicates
3. ✅ Documentation stays simple ("just run init")
4. ✅ Self-healing configuration

### For Project Maintenance
1. ✅ Move folders without breaking setup
2. ✅ Update MCP registration easily
3. ✅ Verify setup anytime
4. ✅ Idempotent operations (key DevOps principle)

## 📝 Implementation Details

### Database Helper Function

Added `get_project_by_root()` in `src/database.py`:

```python
def get_project_by_root(session: Session, project_root: str) -> Optional[ProjectModel]:
    """Get project by its project_root path with path normalization."""
    import os
    normalized_root = os.path.normpath(project_root)

    projects = session.query(ProjectModel).all()
    for project in projects:
        if project.project_root:
            if os.path.normpath(project.project_root) == normalized_root:
                return project

    return None
```

### CLI Logic Updates

Updated `init` command in `src/cli.py`:
- Check for existing project before prompting
- Show existing project info if found
- Still complete MCP registration
- Add `--force-new-project` flag for override

## 🎉 Summary

**Before:** `init` was fragile - could only run once

**After:** `init` is robust - run it anytime with confidence

**Key Improvement:** Idempotency - running `init` multiple times produces the same result without errors or side effects

**User Experience:** Much better - no confusion, clear feedback, always works correctly

---

*Feature implemented: December 2025*
*Making `flanner init` developer-friendly and team-friendly*
