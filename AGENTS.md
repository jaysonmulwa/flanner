<!-- flanner:managed -->
## Plan files (managed by flanner)

Design, architecture, and planning markdown for this repo is managed by flanner and lives in `.plans/` (project: flanner).

When the user asks to save a plan/design/architecture doc, confirm it is a plan, then use the flanner MCP tools instead of writing the file directly:

- `get_plan_config` to confirm the location and header format
- `create_plan_file_tool(project_id, name, content)` to create it (adds the YAML header and versions it)
- `update_plan_file_tool(plan_file_id, content)` to revise it

Never hand-write the YAML header; the tools generate it.
<!-- /flanner:managed -->
