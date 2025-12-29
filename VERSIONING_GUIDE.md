# Plan File Versioning Guide

## How Versioning Works

Plan file versions are tracked **by metadata in the frontmatter**, not by filename. This means you can:

✅ **Update a single file** (e.g., `test-architecture.md`)
✅ **Bump the version number** in frontmatter
✅ **Run sync** to record the new version
✅ **Keep version history** in the database

## Example: Creating Multiple Versions

### Version 1 (Initial)

**File:** `.plans/architecture.md`

```yaml
---
mcp_plan_file: true
project_id: 3d816ecd-489a-4fa0-abe2-15ec93f60d5a
plan_file_id: 59c34f9c-8471-47fc-97f2-8dcfefa15434
plan_name: architecture
version: 1                                    # ← Version number
created_at: '2025-12-25T10:00:00.000000Z'
created_by: user
---

# Architecture Plan

Initial content...
```

**Import:**
```bash
flanner sync
# Output: OK IMPORTED architecture.md (plan: architecture, version: 1)
```

### Version 2 (Update)

**Same File:** `.plans/architecture.md` (just edit it)

```yaml
---
mcp_plan_file: true
project_id: 3d816ecd-489a-4fa0-abe2-15ec93f60d5a
plan_file_id: 59c34f9c-8471-47fc-97f2-8dcfefa15434  # ← SAME
plan_name: architecture                              # ← SAME
version: 2                                           # ← CHANGED
created_at: '2025-12-25T14:00:00.000000Z'           # ← UPDATED
created_by: user
---

# Architecture Plan

Updated content with new features...
```

**Import:**
```bash
flanner sync
# Output: OK UPDATED architecture.md (plan: architecture, v1 -> v2)
```

### Version 3 (Another Update)

**Same File:** `.plans/architecture.md`

```yaml
---
version: 3                                    # ← Increment version
created_at: '2025-12-25T18:00:00.000000Z'    # ← Update timestamp
created_by: jayson mulwa                      # ← Update creator
---

# Architecture Plan

More improvements...
```

**Import:**
```bash
flanner sync
# Output: OK UPDATED architecture.md (plan: architecture, v2 -> v3)
```

## What Changes Between Versions

### Must Change:
- ✅ `version` - Increment by 1 (1 → 2 → 3...)
- ✅ `created_at` - Update timestamp to current time
- ✅ Content - Otherwise why make a new version?

### Should Change:
- 👤 `created_by` - Who made this version?

### Must Stay Same:
- 🔒 `plan_file_id` - Links all versions together
- 🔒 `project_id` - Same project
- 🔒 `plan_name` - Same plan name
- 🔒 `mcp_plan_file: true` - Always required

## Workflow

```bash
# 1. Edit the plan file
nano .plans/architecture.md

# 2. Update frontmatter:
#    - Increment version number
#    - Update created_at timestamp
#    - Update content

# 3. Sync to database
flanner sync --dry-run  # Preview
flanner sync            # Import

# 4. Verify
flanner list --project my-project
# Shows latest version number
```

## Database Behavior

### PlanFileModel (One per plan)
- Tracks the **current** version
- Gets updated when new version imported

### VersionModel (One per version)
- **Multiple records** for one plan file
- Each version has its own record
- All linked by `plan_file_id`

**Example:**
```
PlanFileModel:
  id: 59c34f9c-8471-47fc-97f2-8dcfefa15434
  name: architecture
  current_version: 3  ← Latest

VersionModel (3 records):
  1. version: 1, file_path: .plans/architecture.md, created_at: 2025-12-25 10:00
  2. version: 2, file_path: .plans/architecture.md, created_at: 2025-12-25 14:00
  3. version: 3, file_path: .plans/architecture.md, created_at: 2025-12-25 18:00
```

## Sync Command Behavior

### New File (First Time)
```bash
flanner sync
# Output: OK IMPORTED architecture.md (plan: architecture, version: 1)
```

### Updated Version (Higher Version Number)
```bash
flanner sync
# Output: OK UPDATED architecture.md (plan: architecture, v2 -> v3)
```

### Already Imported (Same or Lower Version)
```bash
flanner sync
# Output: SKIP architecture.md - Version 3 already in database (current: v3)
```

## Filename Convention (Flexible)

You can name files however you want:

✅ `architecture.md` - Simple
✅ `architecture_v1.md` - With version suffix (optional)
✅ `2025-architecture-plan.md` - With date

**The version number in frontmatter is what matters!**

## Viewing Version History

To see all versions of a plan file:

```bash
# Through web interface
flanner web
# Navigate to: Projects → Select Project → Select Plan → View History

# Through MCP server (via Claude)
# Ask Claude: "Show me the version history for the architecture plan"
```

## Real World Example

You just tested this!

**File:** `.plans/test-architecture_v1.md`

| Action | Version in File | Version in DB | Sync Output |
|--------|----------------|---------------|-------------|
| Initial import | v1 | v1 | OK IMPORTED |
| Update file to v2 | v2 | v2 | OK UPDATED (v1 → v2) |
| Update file to v3 | v3 | v3 | OK UPDATED (v2 → v3) |
| Run sync again | v3 | v3 | SKIP (already imported) |

## Tips

1. **Always increment version** - Don't skip numbers
2. **Update timestamp** - Shows when changes were made
3. **Add meaningful content changes** - Otherwise version is pointless
4. **Run sync after editing** - Database stays in sync
5. **Use --dry-run first** - Preview what will happen

## Comparison to Old Approach

### ❌ Old (Separate Files)
```
.plans/
  architecture_v1.md  ← Version 1
  architecture_v2.md  ← Version 2
  architecture_v3.md  ← Version 3
```

**Problems:**
- File clutter
- Confusion about which is latest
- Manual file naming

### ✅ New (Single File, Metadata Versioning)
```
.plans/
  architecture.md  ← All versions (tracked in database)
```

**Benefits:**
- One file to maintain
- Version tracked in frontmatter
- Database keeps full history
- Clean directory structure

---

**Key Takeaway:** Update the `version` number in frontmatter, run `sync`, and you're done! 🎉
