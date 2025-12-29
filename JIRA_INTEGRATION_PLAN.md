# JIRA Integration Plan for Flanner

## Overview

Enable flanner plan files and projects to be associated with JIRA issues and sub-issues, allowing seamless tracking between planning documents and project management workflows.

## Goals

1. **Bidirectional Linking**: Associate plan files with JIRA issues/sub-issues
2. **Status Synchronization**: Optionally sync plan status with JIRA issue status
3. **Multi-Interface Support**: Available via CLI and Web UI
4. **Flexible Association**: Support both project-level and plan-file-level JIRA links
5. **Rich Metadata**: Store and display JIRA issue details alongside plan files

---

## Use Cases

### UC1: Link Plan File to Existing JIRA Issue
**As a developer**, I want to link my plan file to an existing JIRA issue so that I can track implementation planning alongside task management.

**Actions:**
- CLI: `flanner link-jira <plan-name> --issue PROJ-123`
- Web: Click "Link to JIRA" button, enter issue key

### UC2: Create JIRA Issue from Plan File
**As a project manager**, I want to create a JIRA issue directly from a plan file so that planning artifacts automatically generate trackable tasks.

**Actions:**
- CLI: `flanner create-jira-issue <plan-name> --project PROJ --type Epic`
- Web: Click "Create JIRA Issue" button, fill form

### UC3: View JIRA Status in Plan Listing
**As a team member**, I want to see JIRA issue status when listing plan files so that I understand the current state without switching tools.

**Actions:**
- CLI: `flanner list --project my-project` shows JIRA keys and status
- Web: Plan list table includes JIRA columns

### UC4: Link Project to JIRA Project
**As a team lead**, I want to associate a flanner project with a JIRA project so that all plan files default to creating issues in the correct project.

**Actions:**
- CLI: `flanner config my-project --jira-project PROJ`
- Web: Project settings page, JIRA configuration section

### UC5: Sync Plan Changes to JIRA
**As a developer**, when I update my plan file, I want to optionally add a comment to the linked JIRA issue so that stakeholders stay informed.

**Actions:**
- Automatic: On plan update, prompt to add JIRA comment
- CLI: `flanner sync-to-jira <plan-name> --comment "Updated architecture section"`

---

## Technical Architecture

### 1. Database Schema Changes

```sql
-- New table: jira_config
CREATE TABLE jira_config (
    id TEXT PRIMARY KEY,
    project_id TEXT UNIQUE,
    jira_url TEXT NOT NULL,              -- e.g., https://company.atlassian.net
    jira_project_key TEXT,                -- Default JIRA project (e.g., PROJ)
    jira_username TEXT,                   -- For basic auth (optional)
    jira_api_token_encrypted TEXT,        -- Encrypted API token
    default_issue_type TEXT,              -- Epic, Story, Task, etc.
    auto_sync_enabled BOOLEAN DEFAULT 0,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- New table: jira_links
CREATE TABLE jira_links (
    id TEXT PRIMARY KEY,
    plan_file_id TEXT NOT NULL,
    jira_issue_key TEXT NOT NULL,         -- e.g., PROJ-123
    jira_issue_type TEXT,                 -- Epic, Story, Task, Sub-task
    jira_issue_summary TEXT,              -- Cached issue title
    jira_issue_status TEXT,               -- Cached status
    jira_url TEXT,                        -- Full URL to issue
    link_type TEXT DEFAULT 'manual',      -- manual, auto-created
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (plan_file_id) REFERENCES plan_files(id) ON DELETE CASCADE,
    UNIQUE(plan_file_id, jira_issue_key)
);

-- New table: jira_sync_log
CREATE TABLE jira_sync_log (
    id TEXT PRIMARY KEY,
    plan_file_id TEXT NOT NULL,
    jira_issue_key TEXT NOT NULL,
    sync_type TEXT,                       -- comment, status, transition
    sync_direction TEXT,                  -- to_jira, from_jira
    details TEXT,                         -- JSON details
    success BOOLEAN,
    error_message TEXT,
    synced_at TIMESTAMP,
    FOREIGN KEY (plan_file_id) REFERENCES plan_files(id) ON DELETE CASCADE
);
```

### 2. Configuration Management

**Storage Location**: `~/.flanner/jira_credentials.json` (encrypted)

```json
{
  "credentials": {
    "url": "https://company.atlassian.net",
    "email": "user@company.com",
    "api_token": "encrypted_token_here"
  },
  "defaults": {
    "issue_type": "Epic",
    "auto_sync": false
  }
}
```

**Environment Variables** (alternative):
- `FLANNER_JIRA_URL`
- `FLANNER_JIRA_EMAIL`
- `FLANNER_JIRA_API_TOKEN`

### 3. JIRA API Integration

**Library**: Use `jira` Python library or `requests` for REST API

**Key Operations**:
1. **Authentication**: Basic auth with email + API token
2. **Get Issue**: `GET /rest/api/3/issue/{issueKey}`
3. **Create Issue**: `POST /rest/api/3/issue`
4. **Add Comment**: `POST /rest/api/3/issue/{issueKey}/comment`
5. **Update Issue**: `PUT /rest/api/3/issue/{issueKey}`
6. **Search Issues**: `POST /rest/api/3/search` (JQL)

**Error Handling**:
- Network errors: Retry with exponential backoff
- Auth errors: Clear message to reconfigure
- Rate limiting: Respect 429 responses

---

## CLI Commands

### Configuration Commands

```bash
# Configure JIRA connection
flanner jira config \
  --url https://company.atlassian.net \
  --email user@company.com \
  --token <api-token>

# Set project-level JIRA defaults
flanner config my-project \
  --jira-project PROJ \
  --jira-issue-type Epic \
  --jira-auto-sync true

# Test JIRA connection
flanner jira test

# Show JIRA configuration
flanner jira status
```

### Linking Commands

```bash
# Link existing plan to JIRA issue
flanner jira link <plan-name> --issue PROJ-123

# Link with metadata refresh
flanner jira link <plan-name> --issue PROJ-123 --sync

# Unlink plan from JIRA issue
flanner jira unlink <plan-name> --issue PROJ-123

# Unlink all JIRA issues from a plan
flanner jira unlink <plan-name> --all

# List all JIRA links for a project
flanner jira links --project my-project
```

### Issue Creation Commands

```bash
# Create JIRA issue from plan file
flanner jira create <plan-name> \
  --project PROJ \
  --type Epic \
  --summary "Optional custom summary" \
  --description "Optional description"

# Create issue and link automatically
flanner jira create <plan-name> --auto-link

# Create sub-task under existing issue
flanner jira create <plan-name> \
  --parent PROJ-123 \
  --type Sub-task
```

### Sync Commands

```bash
# Sync JIRA metadata (refresh issue status, summary, etc.)
flanner jira sync <plan-name>

# Sync all plan files in a project
flanner jira sync --project my-project

# Add comment to linked JIRA issues
flanner jira comment <plan-name> \
  --message "Updated plan with new architecture details"

# Auto-comment on plan updates (interactive)
flanner jira auto-comment <plan-name>
```

### List/Display Commands

```bash
# List plans with JIRA info
flanner list --project my-project --show-jira

# Output format:
# ID | Name | Version | JIRA Issue | Status | Updated
# ... | auth-plan | v2 | PROJ-123 | In Progress | 2025-12-27

# Show detailed JIRA info for a plan
flanner jira show <plan-name>
```

---

## MCP Tools/Functions

### New MCP Tools

```python
# 1. Configure JIRA for a project
configure_jira_tool(
    project_id: str,
    jira_url: str,
    jira_project_key: str,
    jira_username: str,
    jira_api_token: str,
    default_issue_type: str = "Epic",
    auto_sync: bool = False
)

# 2. Link plan file to JIRA issue
link_plan_to_jira_tool(
    plan_file_id: str,
    jira_issue_key: str,
    sync_metadata: bool = True
)

# 3. Create JIRA issue from plan
create_jira_issue_from_plan_tool(
    plan_file_id: str,
    jira_project_key: str,
    issue_type: str = "Epic",
    summary: str = None,  # Defaults to plan name
    description: str = None,  # Defaults to plan content preview
    auto_link: bool = True
)

# 4. Sync JIRA metadata
sync_jira_metadata_tool(
    plan_file_id: str = None,
    project_id: str = None  # If provided, sync all plans
)

# 5. Get JIRA links for plan
get_jira_links_tool(
    plan_file_id: str
) -> List[JiraLink]

# 6. Unlink JIRA issue
unlink_jira_issue_tool(
    plan_file_id: str,
    jira_issue_key: str = None  # If None, unlink all
)

# 7. Add JIRA comment
add_jira_comment_tool(
    plan_file_id: str,
    comment: str,
    jira_issue_key: str = None  # If None, comment on all linked issues
)

# 8. Test JIRA connection
test_jira_connection_tool(
    project_id: str
) -> {"success": bool, "message": str}
```

---

## Web Interface Changes

### Project Settings Page

**New Section: "JIRA Integration"**

```
┌─────────────────────────────────────────────────┐
│ JIRA Configuration                              │
├─────────────────────────────────────────────────┤
│ JIRA URL: [https://company.atlassian.net     ] │
│ Username: [user@company.com                   ] │
│ API Token: [••••••••••••••••] [Test Connection]│
│                                                 │
│ Default Project: [PROJ                        ] │
│ Default Issue Type: [Epic           ▼]         │
│                                                 │
│ □ Auto-sync plan changes to JIRA               │
│                                                 │
│ [Save Configuration]                            │
└─────────────────────────────────────────────────┘
```

### Plan List Page

**Enhanced Table with JIRA Columns**

```
┌──────────────────────────────────────────────────────────────────────┐
│ Plan Files                                    [+ New Plan] [Refresh]│
├──────┬────────────┬────────┬──────────────┬──────────┬─────────────┤
│ Name │ Version    │ JIRA   │ Issue Status │ Updated  │ Actions     │
├──────┼────────────┼────────┼──────────────┼──────────┼─────────────┤
│ auth │ v3         │ PROJ-  │ In Progress  │ 12/27    │ [View] [⚙]  │
│      │            │ 123    │              │          │             │
├──────┼────────────┼────────┼──────────────┼──────────┼─────────────┤
│ api  │ v2         │ PROJ-  │ To Do        │ 12/26    │ [View] [⚙]  │
│      │            │ 124    │              │          │             │
├──────┼────────────┼────────┼──────────────┼──────────┼─────────────┤
│ db   │ v1         │ --     │ --           │ 12/25    │ [View] [⚙]  │
└──────┴────────────┴────────┴──────────────┴──────────┴─────────────┘
```

### Plan Detail/Edit Page

**JIRA Panel (Sidebar or Expandable Section)**

```
┌─────────────────────────────────────────┐
│ 🔗 JIRA Links                           │
├─────────────────────────────────────────┤
│ PROJ-123: Implement Authentication      │
│ Status: In Progress                     │
│ Type: Epic                              │
│ [View in JIRA] [Refresh] [Unlink]       │
│                                         │
│ PROJ-125: Add OAuth Support             │
│ Status: To Do                           │
│ Type: Sub-task                          │
│ [View in JIRA] [Refresh] [Unlink]       │
│                                         │
├─────────────────────────────────────────┤
│ [+ Link Existing Issue]                 │
│ [+ Create New Issue]                    │
└─────────────────────────────────────────┘
```

**Link Existing Issue Modal**

```
┌─────────────────────────────────────────┐
│ Link to JIRA Issue                 [×]  │
├─────────────────────────────────────────┤
│ Issue Key: [PROJ-___                  ] │
│                                         │
│ □ Fetch issue details automatically    │
│                                         │
│           [Cancel] [Link Issue]         │
└─────────────────────────────────────────┘
```

**Create New Issue Modal**

```
┌─────────────────────────────────────────┐
│ Create JIRA Issue                  [×]  │
├─────────────────────────────────────────┤
│ Project: [PROJ            ▼]            │
│ Issue Type: [Epic         ▼]            │
│                                         │
│ Summary:                                │
│ [Auto-populated from plan name       ] │
│                                         │
│ Description:                            │
│ ┌─────────────────────────────────────┐ │
│ │ Auto-populated from plan content... │ │
│ │                                     │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ Parent Issue (optional):                │
│ [PROJ-___                             ] │
│                                         │
│ □ Link to this plan after creation     │
│                                         │
│      [Cancel] [Create Issue]            │
└─────────────────────────────────────────┘
```

### Dashboard Widget (Optional)

```
┌─────────────────────────────────────────┐
│ 📊 JIRA Integration Summary             │
├─────────────────────────────────────────┤
│ Linked Plans: 12 / 15                   │
│ Active Issues: 8                        │
│ Completed: 4                            │
│                                         │
│ Recent Activity:                        │
│ • PROJ-123 → In Progress (2h ago)       │
│ • PROJ-124 → Created (1d ago)           │
│                                         │
│ [View All Links]                        │
└─────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Foundation (Week 1-2)
**Goal**: Basic JIRA linking without sync

- [ ] Database schema updates
- [ ] JIRA API client wrapper
- [ ] Configuration storage (credentials, project defaults)
- [ ] CLI: `flanner jira config`
- [ ] CLI: `flanner jira link/unlink`
- [ ] MCP: `link_plan_to_jira_tool`
- [ ] MCP: `configure_jira_tool`
- [ ] Web: JIRA configuration in project settings
- [ ] Web: Basic link display in plan list

**Deliverable**: Users can link plan files to existing JIRA issues via CLI and web

### Phase 2: Issue Creation (Week 3)
**Goal**: Create JIRA issues from plans

- [ ] JIRA issue creation logic
- [ ] Plan-to-issue content mapping
- [ ] CLI: `flanner jira create`
- [ ] MCP: `create_jira_issue_from_plan_tool`
- [ ] Web: "Create JIRA Issue" modal
- [ ] Template system for issue descriptions

**Deliverable**: Users can create JIRA issues directly from plan files

### Phase 3: Metadata Sync (Week 4)
**Goal**: Keep JIRA metadata fresh

- [ ] JIRA metadata refresh logic
- [ ] Caching strategy (avoid rate limits)
- [ ] CLI: `flanner jira sync`
- [ ] MCP: `sync_jira_metadata_tool`
- [ ] Web: Auto-refresh on page load (with caching)
- [ ] Web: Manual refresh button
- [ ] Background sync job (optional)

**Deliverable**: JIRA issue status/summary stays current in flanner

### Phase 4: Bidirectional Sync (Week 5-6)
**Goal**: Sync changes between flanner and JIRA

- [ ] Plan update → JIRA comment
- [ ] JIRA status change → Plan metadata update (optional)
- [ ] Webhook support for JIRA → flanner updates
- [ ] CLI: `flanner jira comment`
- [ ] MCP: `add_jira_comment_tool`
- [ ] Web: "Sync to JIRA" button after plan edits
- [ ] Conflict resolution UI

**Deliverable**: Changes flow between flanner and JIRA

### Phase 5: Advanced Features (Week 7+)
**Goal**: Power user features

- [ ] Bulk operations (link/sync multiple plans)
- [ ] JQL-based plan filtering
- [ ] JIRA webhooks for real-time updates
- [ ] Custom field mapping
- [ ] JIRA attachment support (attach plan files)
- [ ] Analytics/reporting on JIRA links
- [ ] Multi-JIRA instance support

---

## Security Considerations

### 1. Credential Storage
- **Never store plaintext API tokens**
- Use OS keyring/credential manager where available
- Encrypt tokens at rest with user-specific key
- Support environment variables for CI/CD

### 2. Token Permissions
- Require minimum JIRA permissions:
  - Read issues
  - Create issues (if using create feature)
  - Add comments (if using sync feature)
- Document required permissions clearly

### 3. Data Privacy
- Don't cache sensitive JIRA data unnecessarily
- Respect JIRA permission model (don't expose restricted issues)
- Allow users to clear cached JIRA data

### 4. Rate Limiting
- Implement exponential backoff
- Cache JIRA responses (5-15 min TTL)
- Batch operations when possible
- Show rate limit status to users

---

## Configuration Examples

### Example 1: Basic Setup

```bash
# 1. Configure JIRA connection
flanner jira config \
  --url https://mycompany.atlassian.net \
  --email dev@mycompany.com \
  --token abc123xyz

# 2. Set project defaults
flanner config my-project --jira-project BACKEND

# 3. Link a plan to existing issue
flanner jira link auth-service --issue BACKEND-42

# 4. Create new issue from plan
flanner jira create api-redesign --type Epic
```

### Example 2: Automated Workflow

```bash
# After creating/updating a plan, auto-sync to JIRA
flanner jira create new-feature --auto-link
flanner jira comment new-feature --message "Plan updated with implementation details"
```

### Example 3: Team Dashboard

```bash
# List all plans with JIRA status
flanner list --project my-project --show-jira

# Sync all JIRA metadata
flanner jira sync --project my-project
```

---

## Testing Strategy

### Unit Tests
- [ ] JIRA API client methods
- [ ] Database operations (CRUD for jira_links)
- [ ] Credential encryption/decryption
- [ ] Issue creation payload generation

### Integration Tests
- [ ] End-to-end link creation flow
- [ ] Issue creation from plan
- [ ] Metadata sync accuracy
- [ ] Error handling (network, auth, rate limits)

### Manual Testing
- [ ] CLI command usability
- [ ] Web UI workflows
- [ ] Multi-user scenarios
- [ ] Various JIRA configurations (Cloud, Server, Data Center)

---

## Success Metrics

1. **Adoption**: % of plan files with JIRA links
2. **Sync Accuracy**: % of JIRA metadata in sync (within 15 min)
3. **User Satisfaction**: NPS or feedback score
4. **Performance**: < 2s for JIRA operations (95th percentile)
5. **Reliability**: < 1% error rate on JIRA API calls

---

## Future Enhancements

1. **Two-way Planning**: Create plan files from JIRA issues
2. **Smart Suggestions**: Auto-suggest JIRA issues based on plan content
3. **Templates**: JIRA issue templates for different plan types
4. **Gantt View**: Timeline view combining plans and JIRA issues
5. **Slack/Teams Integration**: Notifications when plan ↔ JIRA sync happens
6. **AI Summaries**: Auto-generate JIRA descriptions from plan content using LLM
7. **JIRA Automation**: Trigger JIRA automation rules from plan events

---

## Open Questions

1. **Multi-instance Support**: Should one flanner project support multiple JIRA instances?
2. **Conflict Resolution**: What happens when JIRA issue is deleted but link remains?
3. **Offline Mode**: How to handle JIRA operations when offline?
4. **Audit Trail**: Should we track who made JIRA links/changes?
5. **Bulk Import**: Support importing many JIRA issues as plan files?

---

## Resources

- [JIRA Cloud REST API Docs](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/)
- [JIRA Python Library](https://jira.readthedocs.io/)
- [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
- [JQL Reference](https://support.atlassian.com/jira-software-cloud/docs/what-is-advanced-searching-in-jira-cloud/)

---

**Document Version**: 1.0
**Last Updated**: 2025-12-27
**Author**: Flanner Team
**Status**: Draft - Ready for Review
