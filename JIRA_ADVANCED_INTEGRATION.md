# JIRA Advanced Integration Plan

## Overview

This document outlines advanced JIRA integration features for flanner that go beyond basic linking. These features enable bidirectional integration and rich user experiences within JIRA itself.

---

## 1. Display Plan Files/Links in JIRA Comments

### Goal
Automatically post comments or attachments to JIRA issues when plan files are linked, updated, or versioned.

---

### Option A: Post Comments with Links to Plans

**What it does:**
- When you link a plan to a JIRA issue, automatically post a comment in JIRA
- Comment includes a clickable link to view the plan in flanner's web interface
- Updates can also post new comments (e.g., "Plan updated to v3")

**Requirements:**

1. **JIRA REST API Integration**
   - Authentication: Email + API Token
   - Store credentials securely (encrypted or in environment variables)
   - API endpoint: `POST /rest/api/3/issue/{issueKey}/comment`

2. **Flanner Web Interface** (currently not implemented)
   - Host plan files so they're accessible via web browser
   - URLs like: `https://yourcompany.com/flanner/plans/test-architecture-v3`
   - Public or authenticated access depending on security needs

3. **Comment Posting Logic**
   - Trigger: When `link_plan_to_jira_tool` or `flanner jira link` is executed
   - Format: Markdown or Atlassian Document Format (ADF)
   - Include: Plan name, version, link, and optional summary

**Example Comment:**
```
📋 Flanner Plan Linked: test-architecture (v3)
View plan: https://yourcompany.com/flanner/plans/test-architecture-v3
Type: Epic
Linked by: jayson mulwa
Linked at: 2025-12-27 10:34
```

**Implementation Steps:**
1. Add JIRA API client library (e.g., `jira` Python package)
2. Create `jira_api.py` module with authentication and comment posting
3. Add configuration for JIRA credentials (per project or global)
4. Update `create_jira_link` to optionally post comment
5. Add CLI flag: `flanner jira link --post-comment`
6. Add MCP tool parameter: `post_comment: bool = False`

**Database Changes:**
```sql
-- Add to jira_config table
ALTER TABLE jira_config ADD COLUMN jira_email TEXT;
ALTER TABLE jira_config ADD COLUMN jira_api_token_encrypted TEXT;
ALTER TABLE jira_config ADD COLUMN auto_post_comments BOOLEAN DEFAULT 0;
```

---

### Option B: Attach Plan Files to JIRA Issues

**What it does:**
- Export plan file as PDF or Markdown
- Attach it to the JIRA issue
- Update attachment when plan version changes

**Requirements:**

1. **JIRA REST API Integration**
   - API endpoint: `POST /rest/api/3/issue/{issueKey}/attachments`
   - Requires `X-Atlassian-Token: no-check` header

2. **File Export Functionality**
   - Export plan as Markdown (simple - already have the content)
   - Export as PDF (requires library like `weasyprint` or `pdfkit`)
   - Include frontmatter metadata in a human-readable format

3. **Attachment Management**
   - Track which attachments belong to flanner (metadata in filename)
   - Delete old version when uploading new one (optional)
   - Naming convention: `flanner-test-architecture-v3.md`

**Implementation Steps:**
1. Create `export_plan_as_file()` function
2. Add JIRA attachment upload to API client
3. Update `create_jira_link` to optionally upload file
4. Add CLI flag: `flanner jira link --attach-plan`
5. Handle version updates: `flanner jira sync <plan-name> --update-attachment`

**Database Changes:**
```sql
-- Track uploaded attachments
CREATE TABLE jira_attachments (
    id TEXT PRIMARY KEY,
    jira_link_id TEXT NOT NULL,
    attachment_id TEXT NOT NULL,  -- JIRA's attachment ID
    file_name TEXT,
    version INTEGER,
    uploaded_at TIMESTAMP,
    FOREIGN KEY (jira_link_id) REFERENCES jira_links(id) ON DELETE CASCADE
);
```

---

### Option C: Rich Comments with Plan Summary

**What it does:**
- Post formatted comments using Atlassian Document Format (ADF)
- Include plan excerpts, tables, version history, etc.
- Create a rich, interactive experience within JIRA

**Requirements:**

1. **JIRA REST API Integration**
   - Use ADF (Atlassian Document Format) for rich content
   - Support tables, headings, code blocks, etc.

2. **Plan Summarization**
   - Extract key sections from plan (e.g., Overview, Components)
   - Convert Markdown to ADF format
   - Limit length to avoid huge comments

3. **ADF Generation**
   - Library: `atlassian-python-api` supports ADF
   - Or manually construct ADF JSON structures

**Example ADF Comment Structure:**
```json
{
  "type": "doc",
  "version": 1,
  "content": [
    {
      "type": "heading",
      "attrs": { "level": 3 },
      "content": [{ "type": "text", "text": "📋 Flanner Plan: test-architecture" }]
    },
    {
      "type": "paragraph",
      "content": [
        { "type": "text", "text": "Version: v3 | Type: Epic | " },
        {
          "type": "text",
          "text": "View Full Plan",
          "marks": [{ "type": "link", "attrs": { "href": "https://..." } }]
        }
      ]
    },
    {
      "type": "heading",
      "attrs": { "level": 4 },
      "content": [{ "type": "text", "text": "Overview" }]
    },
    {
      "type": "paragraph",
      "content": [{ "type": "text", "text": "This is a test plan file to verify..." }]
    }
  ]
}
```

**Implementation Steps:**
1. Create Markdown → ADF converter
2. Add `generate_plan_summary()` function (extract first N characters or sections)
3. Post rich comment when linking plans
4. Add CLI flag: `flanner jira link --rich-comment`

---

## 2. Mini-App on JIRA for Flanner

### Goal
Create a JIRA app that displays flanner information directly within JIRA's UI, providing seamless integration between JIRA issues and flanner plans.

---

### Option A: Jira Forge App (Recommended)

**What it is:**
- Serverless apps hosted by Atlassian
- Easier to build and deploy than Connect apps
- No infrastructure management needed

**What users would see:**
- **Issue Panel**: "Flanner Plans" section on JIRA issue view
- **Issue Glance**: Small badge showing number of linked plans
- **Global Page**: Dedicated page for browsing all flanner plans
- **Create links**: Link plans to issues directly from JIRA

**Forge Modules to Use:**

1. **`jira:issuePanel`**
   - Displays on the right side of issue view
   - Shows linked plans, versions, and quick actions
   - Example: List of plans with links to view/edit

2. **`jira:issueGlance`**
   - Small badge/icon on issue card
   - Shows count: "2 plans linked"
   - Clicking opens the issue panel

3. **`jira:globalPage`**
   - Full-page app for browsing plans
   - Search, filter, and create plans
   - Manage JIRA links in bulk

4. **`jira:issueContext`**
   - Add "Link to Flanner Plan" option in issue context menu

**Technical Requirements:**

1. **Atlassian Forge Framework**
   - Install Forge CLI: `npm install -g @forge/cli`
   - Create Forge app: `forge create`
   - Languages: JavaScript/TypeScript, or UI Kit (React)

2. **Backend Integration**
   - Forge app needs to communicate with flanner
   - Options:
     - **REST API**: Forge calls flanner's web API (need to implement)
     - **Direct DB**: Forge reads flanner database (less secure)
     - **MCP Proxy**: Forge calls an intermediary that uses MCP tools

3. **Authentication**
   - Forge handles Atlassian authentication
   - Need to authenticate Forge → Flanner calls
   - Options: API keys, OAuth, JWT

4. **Storage**
   - Forge provides storage API for caching
   - Store frequently accessed data to reduce API calls

5. **Permissions**
   - Define in `manifest.yml`:
     - `read:jira-work` - read issues
     - `write:jira-work` - update issues
     - `external:fetch` - call flanner API

**Manifest Example (`manifest.yml`):**
```yaml
modules:
  jira:issuePanel:
    - key: flanner-issue-panel
      title: Flanner Plans
      icon: https://example.com/icon.png
      render: native
      resource: main

  jira:issueGlance:
    - key: flanner-glance
      icon: https://example.com/icon.png
      content:
        type: label
        value:
          text: "{planCount} plans"
      target:
        type: panel
        key: flanner-issue-panel

  jira:globalPage:
    - key: flanner-global-page
      title: Flanner Plans
      route: /
      resource: global-page

resources:
  - key: main
    path: src/index.jsx

permissions:
  scopes:
    - read:jira-work
    - write:jira-work
  external:
    fetch:
      backend:
        - 'https://yourcompany.com/flanner/*'
```

**Implementation Steps:**

1. **Setup**
   - Install Forge CLI
   - Create new Forge app: `forge create`
   - Choose template: Custom UI (React)

2. **Develop UI**
   - Build React components for issue panel
   - Display linked plans, version history
   - Add "Link Plan" button/modal

3. **Backend Logic**
   - Create resolver functions in `src/resolvers/index.js`
   - Call flanner API to get/create links
   - Handle errors and loading states

4. **Deploy**
   - `forge deploy` - deploy to Atlassian's infrastructure
   - `forge install` - install to your JIRA instance
   - Test in JIRA Cloud environment

5. **Publish** (optional)
   - Submit to Atlassian Marketplace
   - Make available to other teams/companies

**Pros:**
- ✅ Serverless - no hosting needed
- ✅ Easier development
- ✅ Atlassian handles security and scaling
- ✅ Built-in authentication

**Cons:**
- ❌ Only works with Jira Cloud (not Server/Data Center)
- ❌ Some limitations on API calls and storage
- ❌ Less control over infrastructure

---

### Option B: Atlassian Connect App

**What it is:**
- Web application that JIRA embeds via iframe
- You host the app on your own infrastructure
- More flexibility but more complex

**What users would see:**
- Same modules as Forge (panels, pages, etc.)
- Embedded web UI within JIRA
- Seamless integration with JIRA's UI

**Technical Requirements:**

1. **Web Application**
   - Build with any framework: React, Vue, Flask, Django, etc.
   - Must be accessible via HTTPS
   - Responsive design (works in iframe)

2. **Atlassian Connect Descriptor**
   - JSON file describing your app
   - Hosted at `https://yourapp.com/atlassian-connect.json`
   - Defines modules, permissions, webhooks

3. **Host Your App**
   - Deploy to cloud provider (AWS, GCP, Azure, Heroku, etc.)
   - SSL certificate required
   - High availability recommended

4. **JWT Authentication**
   - Secure communication between JIRA and your app
   - JIRA signs requests with JWT
   - Your app validates JWT before responding

5. **Webhooks**
   - JIRA sends events when issues are updated
   - Example: Issue created, updated, linked, etc.
   - Your app listens and syncs data

6. **REST API Calls**
   - Your app calls JIRA REST API
   - Your app calls flanner API/database
   - Bidirectional sync

**Connect Descriptor Example (`atlassian-connect.json`):**
```json
{
  "name": "Flanner for JIRA",
  "key": "flanner-jira-connect",
  "baseUrl": "https://yourcompany.com/flanner-jira",
  "authentication": {
    "type": "jwt"
  },
  "lifecycle": {
    "installed": "/installed",
    "uninstalled": "/uninstalled"
  },
  "scopes": ["READ", "WRITE"],
  "modules": {
    "webPanels": [
      {
        "key": "flanner-issue-panel",
        "location": "atl.jira.view.issue.right.context",
        "name": {
          "value": "Flanner Plans"
        },
        "url": "/panels/issue?issueKey={issue.key}",
        "conditions": [
          {
            "condition": "user_is_logged_in"
          }
        ]
      }
    ],
    "generalPages": [
      {
        "key": "flanner-global-page",
        "location": "system.top.navigation.bar",
        "name": {
          "value": "Flanner Plans"
        },
        "url": "/pages/global",
        "icon": {
          "width": 16,
          "height": 16,
          "url": "/images/icon.png"
        }
      }
    ],
    "webhooks": [
      {
        "event": "jira:issue_updated",
        "url": "/webhooks/issue-updated"
      }
    ]
  }
}
```

**Implementation Steps:**

1. **Setup Web App**
   - Create web application (Flask, Express.js, Django, etc.)
   - Implement JWT authentication
   - Host on HTTPS-enabled server

2. **Create Descriptor**
   - Define `atlassian-connect.json`
   - List all modules and permissions
   - Host at root of your domain

3. **Build UI Modules**
   - `/panels/issue` - Issue panel showing linked plans
   - `/pages/global` - Global page for all plans
   - Embed flanner web interface (if available)

4. **Implement Webhooks**
   - Listen for JIRA events
   - Sync changes bidirectionally
   - Update flanner when issues change

5. **Install in JIRA**
   - Go to JIRA → Apps → Manage Apps
   - "Upload app" and provide descriptor URL
   - JIRA fetches descriptor and installs

6. **Test & Deploy**
   - Test all modules in JIRA
   - Monitor webhooks and API calls
   - Scale infrastructure as needed

**Pros:**
- ✅ Full control over infrastructure
- ✅ Works with Jira Cloud, Server, and Data Center
- ✅ Can use any backend technology
- ✅ No Atlassian platform limitations

**Cons:**
- ❌ More complex to build and maintain
- ❌ Need to host and scale infrastructure
- ❌ More security considerations (JWT, HTTPS, etc.)

---

### Option C: Simple Web Link (Easiest)

**What it is:**
- Just add web links to JIRA issues
- No custom app needed
- Manual or automated via API

**Requirements:**

1. **Flanner Web Interface**
   - Build web UI to view plans
   - URL structure: `https://yourcompany.com/flanner/plans/{plan-id}`

2. **JIRA Remote Links API**
   - API endpoint: `POST /rest/api/3/issue/{issueKey}/remotelink`
   - Add web links programmatically

**Implementation Steps:**
1. Build flanner web interface
2. When linking plan to JIRA, also create remote link
3. Users see "Web Links" section in JIRA with link to flanner

**Pros:**
- ✅ Very simple to implement
- ✅ No app development needed
- ✅ Works with all JIRA versions

**Cons:**
- ❌ Less integrated (just a link)
- ❌ No custom UI in JIRA
- ❌ No webhooks or bidirectional sync

---

## Implementation Recommendations

### Phase 1: Basic API Integration
1. Add JIRA API client to flanner
2. Implement comment posting on link creation
3. Test with a few issues

### Phase 2: Rich Integration
1. Implement plan file attachments
2. Add rich ADF comments with plan summaries
3. Create bidirectional webhooks

### Phase 3: JIRA App (Choose One)
- **For small teams / quick start**: Build **Forge app**
- **For enterprise / on-premise**: Build **Connect app**
- **For MVP / testing**: Use **Web Links** approach

### Phase 4: Web Interface (Required for all)
1. Build flanner web interface (not yet implemented)
2. Host plan files as web pages
3. Add authentication and permissions
4. Create shareable URLs for plans

---

## Dependencies

### New Libraries Needed

**Python (for flanner backend):**
```bash
pip install jira  # Official JIRA Python library
pip install atlassian-python-api  # Alternative with ADF support
pip install pdfkit  # For PDF export (requires wkhtmltopdf)
pip install weasyprint  # Alternative PDF library
pip install cryptography  # For encrypting API tokens
```

**JavaScript (for Forge/Connect app):**
```bash
npm install @forge/cli  # Forge CLI
npm install @forge/api  # Forge API client
npm install express  # For Connect app backend
npm install atlassian-jwt  # JWT authentication for Connect
npm install axios  # HTTP client for API calls
```

---

## Security Considerations

1. **API Tokens**
   - Never store plaintext API tokens
   - Use encryption (e.g., Fernet, AES)
   - Store in environment variables or secure vault

2. **Authentication**
   - Validate JWT signatures in Connect apps
   - Use Atlassian's authentication in Forge apps
   - Implement rate limiting on API endpoints

3. **Permissions**
   - Respect JIRA's permission model
   - Don't expose restricted plans to unauthorized users
   - Implement role-based access control (RBAC)

4. **HTTPS**
   - All communications must use HTTPS
   - Valid SSL certificates required
   - No mixed content in iframes

---

## Cost Considerations

### Forge App
- Free for development and testing
- Pricing based on active users in production
- Atlassian hosts infrastructure (no server costs)

### Connect App
- Free to develop (just app code)
- Infrastructure costs: hosting, SSL, database, etc.
- Higher maintenance burden

### API Integration
- JIRA API is free (included with JIRA Cloud/Server)
- Standard rate limits apply
- Monitor usage to avoid throttling

---

## Timeline Estimates

### Comment/Attachment Integration
- **Basic**: 1-2 weeks (simple comments)
- **Rich**: 3-4 weeks (ADF, attachments, webhooks)

### Forge App
- **Basic panel**: 2-3 weeks
- **Full-featured**: 4-6 weeks

### Connect App
- **Basic integration**: 4-6 weeks
- **Production-ready**: 8-12 weeks

### Web Interface (Prerequisite)
- **Basic viewer**: 2-3 weeks
- **Full CRUD interface**: 6-8 weeks

---

## Next Steps

1. **Decide on approach**:
   - Comments only?
   - Comments + Attachments?
   - Full Forge/Connect app?

2. **Build web interface first** (required for all options)
   - This is the missing piece
   - Needed for shareable URLs

3. **Implement JIRA API integration**
   - Start with read-only operations
   - Add write operations (comments, links)

4. **Prototype JIRA app** (if chosen)
   - Start with Forge for faster iteration
   - Migrate to Connect if needed later

5. **Test with real users**
   - Get feedback on UX
   - Iterate based on usage patterns

---

**Document Version**: 1.0
**Last Updated**: 2025-12-27
**Status**: Planning - Ready for Implementation
**Prerequisites**: Web interface for flanner (not yet implemented)
