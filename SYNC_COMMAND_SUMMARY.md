# Sync Command - Quick Summary

## Problem You Encountered

```bash
$ flanner status
Total Plan Files: 0  ❌

# But you have a file in .plans/!
$ ls .plans/
test-architecture_v1.md
```

**Why?** The file exists on disk but has no database record.

## Solution: Use `sync` Command

```bash
# Preview what will be imported
flanner sync --dry-run

# Actually import the files
flanner sync
```

## What Happened

**Before:**
- File: `.plans/test-architecture_v1.md` ✅
- Database record: ❌

**After running sync:**
- File: `.plans/test-architecture_v1.md` ✅
- Database record: ✅

**Verification:**
```bash
$ flanner status
Total Plan Files: 1  ✅

$ flanner list --project flanner
+-----------------------------------------------------------------------------+
| ID                         | Name              | Version | Updated          |
|----------------------------+-------------------+---------+------------------|
| 59c34f9c-8471-47fc-97f2-8� | test-architecture | v1      | 2025-12-25 19:46 |
+-----------------------------------------------------------------------------+
```

## How Plan Files Are Created & Updated

### Method 1: Through MCP Server (Automatic)

When Claude creates plan files using MCP tools:
- ✅ File created
- ✅ Database record created
- ✅ Everything in sync automatically

**No sync needed!**

### Method 2: Manual Files (Requires Sync)

When you manually create files or run test scripts:
- ✅ File created
- ❌ Database record missing
- ⚠️ Need to run `sync` command

### Updating Existing Files

To create a new version:
1. Edit the file (e.g., `.plans/architecture.md`)
2. Update `version: 2` in frontmatter (increment by 1)
3. Update `created_at` timestamp
4. Run `flanner sync`
5. New version recorded in database ✅

**Version is tracked by metadata, not filename!**

## Quick Reference

| Command | Purpose |
|---------|---------|
| `flanner sync --dry-run` | Preview what will be imported |
| `flanner sync` | Import plan files into database |
| `flanner sync --project NAME` | Sync only specific project |
| `flanner status` | Check total plan files |
| `flanner list --project NAME` | List plan files for project |

## When to Use Sync

✅ **Use sync when:**
- After running test scripts
- After manually creating plan files
- After cloning a repository with existing `.plans/`
- When `status` shows 0 files but files exist
- Importing existing markdown files

❌ **Don't need sync when:**
- Creating files through Claude (MCP tools)
- Files already imported (sync will skip them)

## Example Output

```bash
$ flanner sync

============================================================
SYNC PLAN FILES
============================================================

Project: flanner
Plan directory: C:\Users\...\mcp-cli/.plans
  Found 1 file(s)

  OK IMPORTED test-architecture_v1.md (plan: test-architecture, version: 1)

============================================================
SYNC SUMMARY
============================================================
Files scanned: 1
Files imported: 1
Files skipped: 0
Errors: 0
```

---

**Documentation:**
- Full guide: `PLAN_FILE_MANAGEMENT.md`
- Main README: `README.md`
