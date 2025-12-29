# Plan File Management Guide

## Overview

The Flanner tracks plan files in a **database** (SQLite) for version control and management. Physical files in the `.plans/` directory need corresponding database records to be recognized by the system.

**Important:** Versions are tracked by **frontmatter metadata** (`version: 1`, `version: 2`, etc.), not by filename. You update the same file and increment the version number in the frontmatter.

## Two Ways to Create Plan Files

### 1. Through MCP Server (Recommended)

When Claude or other AI assistants create plan files using the MCP server, **both the file and database record** are created automatically:

```python
# Claude uses this MCP tool:
create_plan_file_tool(
    project_id="uuid-here",
    name="architecture",
    content="# Architecture Plan\n\nYour content here...",
    created_by="claude"
)
```

**What happens:**
- ✅ Creates database record (PlanFileModel)
- ✅ Creates version record (VersionModel)
- ✅ Writes physical file with frontmatter
- ✅ Everything stays in sync

### 2. Manual File Creation (Requires Sync)

If you manually create a file in `.plans/`:

```bash
# Create file manually
echo "---
mcp_plan_file: true
project_id: your-uuid
plan_file_id: file-uuid
plan_name: my-plan
version: 1
---
# My Plan
Content here..." > .plans/my-plan_v1.md
```

**Problem:** The database doesn't know about it!
- ❌ `status` shows 0 plan files
- ❌ Web interface doesn't show it
- ❌ MCP tools can't access it

**Solution:** Run the `sync` command to import it into the database.

## The `sync` Command

### Purpose

Scans the `.plans/` directory for markdown files with valid MCP frontmatter and imports them into the database.

### Usage

```bash
# Dry run first (see what would be imported)
flanner sync --dry-run

# Actually import the files
flanner sync

# Sync only a specific project
flanner sync --project my-project
```

### What It Does

1. **Scans** all `.md` files in the `.plans/` directory
2. **Parses** frontmatter from each file
3. **Validates** that frontmatter is complete and correct
4. **Checks** if plan file already exists in database (by `plan_file_id`)
5. **Imports** new files by creating:
   - PlanFileModel record
   - VersionModel record
6. **Reports** summary of what was imported

### Example Output

```bash
$ flanner sync --dry-run

============================================================
SYNC PLAN FILES
============================================================

[DRY RUN MODE - No changes will be made]

Project: flanner
Plan directory: C:\Users\...\mcp-cli/.plans
  Found 1 file(s)

  WOULD IMPORT test-architecture_v1.md (plan: test-architecture, version: 1)

============================================================
SYNC SUMMARY
============================================================
Files scanned: 1
Files imported: 1
Files skipped: 0
Errors: 0

Run without --dry-run to actually import the files
```

### File Requirements

For a file to be imported, it must have valid YAML frontmatter:

```yaml
---
mcp_plan_file: true                    # Required: Must be true
plan_manager_version: '1.0'            # Optional: Defaults to 1.0
project_id: 3d816ecd-489a-4fa0-abe2... # Required: UUID
project_name: my-project               # Optional: For reference
plan_file_id: 59c34f9c-8471-47fc...   # Required: UUID (unique identifier)
plan_name: architecture                # Required: Plan name
version: 1                             # Required: Version number
created_at: '2025-12-25T10:30:00Z'    # Optional: Timestamp
created_by: claude                     # Required: Creator (claude, user, etc.)
---
```

### Skipped Files

Files are skipped if:
- ❌ No frontmatter present
- ❌ `mcp_plan_file: true` is missing
- ❌ Required fields are missing
- ❌ Already imported (plan_file_id exists in database)

## Checking Status

After syncing, verify the plan files are recognized:

```bash
# Check overall status
flanner status

# Output shows:
# Total Plan Files: 1  ✅

# List plan files for a project
flanner list --project my-project

# Output shows table with:
# | ID | Name | Version | Updated |
# |----|------|---------|---------|
# | 59c34f9c... | test-architecture | v1 | 2025-12-25 19:46 |
```

## Workflow Comparison

### Recommended Workflow (Using MCP)

```mermaid
graph LR
    A[Claude via MCP] --> B[create_plan_file_tool]
    B --> C[Database Record Created]
    B --> D[File Written to Disk]
    C --> E[✅ Everything in Sync]
    D --> E
```

### Manual Workflow (Requires Sync)

```mermaid
graph LR
    A[Manually Create File] --> B[File on Disk]
    B --> C[❌ Database Unaware]
    C --> D[Run: sync command]
    D --> E[Database Record Created]
    E --> F[✅ Now in Sync]
```

## Common Scenarios

### Scenario 1: Test Script Created Files

**Problem:**
```bash
$ python test_server.py  # Creates test file
$ flanner status
# Shows: Total Plan Files: 0  ❌
```

**Solution:**
```bash
$ flanner sync
# Output: OK IMPORTED test-architecture_v1.md
$ flanner status
# Shows: Total Plan Files: 1  ✅
```

### Scenario 2: Manually Created Plan

**Steps:**
```bash
# 1. Create file manually in .plans/
# 2. Add valid frontmatter (with UUIDs)
# 3. Run sync
flanner sync --dry-run  # Check first
flanner sync            # Import
```

### Scenario 3: Migrating Existing Plans

If you have existing markdown files and want to convert them to MCP plan files:

```bash
# 1. Add frontmatter to each file
# 2. Ensure all required fields are present
# 3. Generate UUIDs for project_id and plan_file_id
# 4. Run sync
flanner sync --dry-run
flanner sync
```

## Best Practices

### ✅ DO:
- Use the MCP server to create plan files (preferred method)
- Run `sync --dry-run` first to see what will be imported
- Validate frontmatter before syncing
- Use sync after running test scripts or importing files

### ❌ DON'T:
- Don't manually create files unless necessary
- Don't skip frontmatter validation
- Don't reuse plan_file_id UUIDs (each file needs unique ID)

## Troubleshooting

### "Total Plan Files: 0" but files exist

**Cause:** Files not in database

**Solution:**
```bash
flanner sync
```

### "ERROR: Invalid frontmatter"

**Cause:** Missing required fields

**Check:**
- `mcp_plan_file: true` present?
- All required UUIDs present?
- `plan_name`, `version`, `created_by` filled in?

### "SKIP: Already in database"

**Cause:** File already imported

**Solution:** This is normal - file won't be imported again

### "SKIP: Not an MCP plan file"

**Cause:** File doesn't have `mcp_plan_file: true` in frontmatter

**Solution:** Add proper frontmatter or exclude from `.plans/` directory

## Database Schema

### PlanFileModel
- `id` (UUID) - Unique identifier for the plan file
- `project_id` (UUID) - Which project it belongs to
- `name` - Plan name (e.g., "architecture")
- `current_version` - Latest version number
- `description` - Optional description

### VersionModel
- `id` (UUID) - Unique version identifier
- `plan_file_id` (UUID) - Links to PlanFileModel
- `version` - Version number (1, 2, 3...)
- `file_path` - Absolute path to markdown file
- `content_hash` - SHA256 hash for change detection
- `created_by` - Who created it (claude, user, etc.)

## Future Enhancements

Planned features for sync command:

- **Auto-sync on init** - Optionally run sync during `flanner init`
- **Watch mode** - Auto-import new files as they're created
- **Conflict resolution** - Handle frontmatter vs database mismatches
- **Bulk import** - Import entire directories at once
- **Validation report** - Detailed report of frontmatter issues

---

**Summary:**
- MCP server creates files + database records automatically ✅
- Manual files need `sync` command to import ✅
- Always use `--dry-run` first to preview ✅
- Check `status` and `list` to verify ✅
