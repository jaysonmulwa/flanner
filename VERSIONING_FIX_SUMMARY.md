# Versioning Fix - Summary

## What You Requested

> "The versioning of the files does not need to be based on the underscore that comes after the naming, rather on the metadata version number."

## Problem Before

The sync command would skip files that were already imported, even if you:
- Updated the `version` number in frontmatter
- Changed the content
- Updated the `created_at` timestamp

**Old behavior:**
```bash
$ flanner sync
# Output: SKIP test-architecture_v1.md - Already in database
# ❌ Didn't check if version number changed!
```

## Solution Implemented

The sync command now **checks the version number** in frontmatter and compares it to the database:

```python
# New logic:
if plan_file_exists_in_db:
    if file_version > db_version:
        # Import as new version ✅
    else:
        # Skip (already imported) ✅
```

## New Behavior

### Scenario 1: New File (First Import)
```bash
# File: architecture.md with version: 1
$ flanner sync
# Output: OK IMPORTED architecture.md (plan: architecture, version: 1)
```

### Scenario 2: Updated File (Higher Version)
```bash
# Edit same file, change version: 1 → version: 2
$ flanner sync
# Output: OK UPDATED architecture.md (plan: architecture, v1 -> v2)
```

### Scenario 3: Already Synced (Same Version)
```bash
# No changes to version number
$ flanner sync
# Output: SKIP architecture.md - Version 2 already in database (current: v2)
```

## Real Test Results

You tested this with `test-architecture_v1.md`:

| Version | Action | Sync Output |
|---------|--------|-------------|
| v1 | Initial import | ✅ OK IMPORTED |
| v2 | Updated metadata + content | ✅ OK UPDATED (v1 → v2) |
| v3 | Updated metadata + content | ✅ OK UPDATED (v2 → v3) |
| v3 | No changes | ⏭️ SKIP (already imported) |

**Verification:**
```bash
$ flanner list --project flanner
+-----------------------------------------------------------------------------+
| ID                         | Name              | Version | Updated          |
|----------------------------+-------------------+---------+------------------|
| 59c34f9c-8471-47fc-97f2-8� | test-architecture | v3      | 2025-12-25 20:03 |
+-----------------------------------------------------------------------------+
```

## How to Create New Versions Now

**One file, multiple versions:**

```yaml
# .plans/architecture.md

# Version 1 (initial)
---
version: 1
created_at: '2025-12-25T10:00:00Z'
---
Content v1...

# Version 2 (edit same file)
---
version: 2              # ← Increment
created_at: '2025-12-25T14:00:00Z'  # ← Update
---
Content v2 with changes...

# Version 3 (edit again)
---
version: 3              # ← Increment
created_at: '2025-12-25T18:00:00Z'  # ← Update
---
Content v3 with more changes...
```

**After each edit:**
```bash
flanner sync
```

## Database Tracking

The database maintains:

1. **PlanFileModel** - Tracks current version
   - `current_version: 3` (latest)

2. **VersionModel** - Multiple records for history
   - Version 1 record
   - Version 2 record
   - Version 3 record

All linked by the same `plan_file_id` UUID.

## Key Changes in Code

### Updated File: `src/cli.py`

**Before:**
```python
if existing_plan_file:
    # Always skip if plan exists
    console.print("SKIP - Already in database")
    continue
```

**After:**
```python
if existing_plan_file:
    # Check version number
    if version <= existing_plan_file.current_version:
        console.print("SKIP - Version already imported")
        continue

    # Higher version - create new version record
    create_version_record(...)
    existing_plan_file.current_version = version
    console.print("OK UPDATED (v1 -> v2)")
```

## Benefits

✅ **Single file per plan** - No need for `architecture_v1.md`, `architecture_v2.md`, etc.
✅ **Version in metadata** - Clear and explicit version tracking
✅ **Automatic detection** - Sync command detects new versions
✅ **Full history** - All versions tracked in database
✅ **Flexible naming** - Name files however you want

## Documentation Created

1. **VERSIONING_GUIDE.md** - Complete guide on how versioning works
2. **VERSIONING_FIX_SUMMARY.md** - This document
3. Updated **PLAN_FILE_MANAGEMENT.md** - Reflects new behavior
4. Updated **SYNC_COMMAND_SUMMARY.md** - Includes versioning info

## Next Steps

1. ✅ **Versioning fixed** - Works based on metadata
2. ✅ **Tested successfully** - v1, v2, v3 all imported correctly
3. ✅ **Documentation updated** - All guides reflect new behavior

You can now:
- Edit a single file
- Increment `version` in frontmatter
- Run `flanner sync`
- New version recorded in database ✅

---

**Status:** ✅ Complete
**Date:** 2025-12-25
**Requested by:** User feedback on sync behavior
