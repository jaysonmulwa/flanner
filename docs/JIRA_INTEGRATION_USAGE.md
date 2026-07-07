# JIRA Integration - Usage Guide

## Overview

Flanner now supports linking plan files to JIRA issues through CLI and MCP tools (for Claude). This is a **linking-only** integration - no API calls to JIRA are made.

## Features Implemented

### ✅ Linking & Association
- Link plan files to existing JIRA issue keys (e.g., PROJ-123)
- Store JIRA issue keys and basic metadata (issue type, notes)
- Associate flanner projects with default JIRA URLs
- Multiple JIRA links per plan file
- Automatic URL generation for JIRA issues

### ✅ Multi-Interface Support
- **CLI**: Full command-line interface for JIRA operations
- **MCP Tools**: Claude can manage JIRA links via MCP server

---

## CLI Usage

### 1. Configure JIRA for a Project

```bash
flanner jira config <project-name> \
  --url https://company.atlassian.net \
  --project-key PROJ
```

**Example:**
```bash
flanner jira config my-awesome-project \
  --url https://mycompany.atlassian.net \
  --project-key PROJ
```

### 2. Link a Plan File to JIRA Issue

```bash
flanner jira link <plan-name> \
  --issue PROJ-123 \
  --type Epic \
  --notes "Main implementation epic" \
  --project <project-name>
```

**Example:**
```bash
flanner jira link test-architecture \
  --issue PROJ-123 \
  --type Epic \
  --notes "Main architecture plan" \
  --project my-awesome-project
```

### 3. Show JIRA Links for a Plan

```bash
flanner jira show <plan-name> --project <project-name>
```

**Example:**
```bash
flanner jira show test-architecture --project my-awesome-project
```

**Output:**
```
Plan: test-architecture
JIRA Links:

  • PROJ-123
    Type: Epic
    URL: https://mycompany.atlassian.net/browse/PROJ-123
    Notes: Main architecture plan
    Linked: 2025-12-27 10:34 by user
```

### 4. List All JIRA Links

```bash
# List for all projects
flanner jira links

# List for specific project
flanner jira links --project my-awesome-project
```

**Output:**
```
my-awesome-project:
+----------------------------------------------------+
| Plan File         | JIRA Issue | Type | Created    |
|-------------------+------------+------+------------|
| test-architecture | PROJ-123   | Epic | 2025-12-27 |
+----------------------------------------------------+
```

### 5. Unlink JIRA Issues

```bash
# Unlink specific issue
flanner jira unlink <plan-name> --issue PROJ-123 --project <project-name>

# Unlink all issues
flanner jira unlink <plan-name> --all --project <project-name>
```

**Example:**
```bash
flanner jira unlink test-architecture --issue PROJ-123 --project my-awesome-project
```

---

## MCP Tools (For Claude)

Claude can use these MCP tools to manage JIRA links:

### 1. `configure_jira_tool`

Configure JIRA for a project.

```python
configure_jira_tool(
    project_id="915a9af7-3471-4ffc-...",
    jira_url="https://company.atlassian.net",
    jira_project_key="PROJ"
)
```

### 2. `link_plan_to_jira_tool`

Link a plan file to a JIRA issue.

```python
link_plan_to_jira_tool(
    plan_file_id="59c34f9c-8471-47fc-...",
    jira_issue_key="PROJ-123",
    issue_type="Epic",
    notes="Main architecture plan"
)
```

### 3. `get_jira_links_tool`

Get all JIRA links for a plan file.

```python
get_jira_links_tool(
    plan_file_id="59c34f9c-8471-47fc-..."
)
```

### 4. `list_jira_links_tool`

List all JIRA links in a project.

```python
list_jira_links_tool(
    project_id="915a9af7-3471-4ffc-..."
)
```

### 5. `unlink_jira_issue_tool`

Unlink a JIRA issue from a plan file.

```python
# Unlink specific issue
unlink_jira_issue_tool(
    plan_file_id="59c34f9c-8471-47fc-...",
    jira_issue_key="PROJ-123"
)

# Unlink all issues
unlink_jira_issue_tool(
    plan_file_id="59c34f9c-8471-47fc-..."
)
```

### 6. `get_jira_config_tool`

Get JIRA configuration for a project.

```python
get_jira_config_tool(
    project_id="915a9af7-3471-4ffc-..."
)
```

---

## Database Schema

### jira_config table
- `id`: UUID (primary key)
- `project_id`: UUID (foreign key to projects, unique)
- `jira_url`: Base JIRA URL
- `jira_project_key`: Default JIRA project key
- `created_at`, `updated_at`: Timestamps

### jira_links table
- `id`: UUID (primary key)
- `plan_file_id`: UUID (foreign key to plan_files)
- `jira_issue_key`: JIRA issue key (e.g., PROJ-123)
- `jira_issue_type`: Issue type (Epic, Story, Task, etc.)
- `notes`: User notes
- `created_at`: Timestamp
- `created_by`: Who created the link

---

## Validation

### JIRA Issue Key Format

Valid formats:
- `PROJ-123` ✅
- `ABC-1` ✅
- `LONGPROJECTKEY-99999` ✅

Invalid formats:
- `proj-123` ❌ (must be uppercase)
- `PROJ` ❌ (must have issue number)
- `123-PROJ` ❌ (wrong order)

### JIRA URL Format

Valid formats:
- `https://company.atlassian.net` ✅
- `https://jira.company.com` ✅
- `http://localhost:8080` ✅ (for testing)

---

## Examples

### Complete Workflow

```bash
# 1. Configure JIRA for your project
flanner jira config my-project \
  --url https://mycompany.atlassian.net \
  --project-key BACKEND

# 2. Link a plan to an Epic
flanner jira link auth-service \
  --issue BACKEND-42 \
  --type Epic \
  --notes "Main authentication implementation" \
  --project my-project

# 3. Link the same plan to a related Story
flanner jira link auth-service \
  --issue BACKEND-50 \
  --type Story \
  --notes "OAuth 2.0 implementation" \
  --project my-project

# 4. View all links for the plan
flanner jira show auth-service --project my-project

# 5. List all JIRA links in the project
flanner jira links --project my-project

# 6. Unlink a specific issue
flanner jira unlink auth-service --issue BACKEND-50 --project my-project
```

---

## Notes

- **No API Integration**: This is a linking-only feature. No API calls are made to JIRA.
- **Manual Links**: All links are created manually via CLI or MCP tools.
- **URL Generation**: JIRA issue URLs are generated locally from the configured base URL.
- **Multiple Links**: One plan file can be linked to multiple JIRA issues.
- **Project-Level Config**: JIRA URL is configured at the project level.

---

## Future Enhancements (Not Implemented)

These features are planned but not yet implemented:
- Creating JIRA issues from plan files
- Syncing JIRA metadata (status, summary, etc.)
- Bidirectional synchronization
- Web UI for JIRA management
- JIRA API integration
- Webhooks for real-time updates

---

**Version**: 1.0
**Last Updated**: 2025-12-27
**Status**: Production Ready
