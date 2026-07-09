# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Schema migration runner: a versioned MIGRATIONS registry upgrades an existing
  database in-place when SCHEMA_VERSION rises, instead of only stamping the
  version (see docs/adr/0003-schema-migrations.md)

### Changed
- Extracted the shared plan write-path (frontmatter, filename, save, hash,
  version record) into flanner/plan_ops.write_version; the MCP server and web
  UI both call it so the four create/update copies cannot drift

### Fixed
- Bump pytest to >=9.0.3 so pip-audit passes (PYSEC-2026-1845)
- Replace deprecated datetime.utcnow() with a naive-UTC helper
- Refresh the stale docs/INSTALLATION.md

### Security
- flanner web warns when binding a non-local host (the web UI has no auth)

## [0.3.0] - 2026-07-09

### Added
- Claude Code agent integration so plan files reliably land in the managed
  directory with the standard header, without the user reminding Claude:
  - `flanner hook guard-write`: a PreToolUse hook that denies raw Writes into
    a project's plan directory and steers Claude to create_plan_file_tool
    (fails open; the MCP tools write outside the Write tool so they are never
    blocked)
  - `flanner init` now writes a managed block into CLAUDE.md and AGENTS.md
    (the cross-tool file Codex reads), merges the guard-write hook into
    .claude/settings.json, and installs a flanner-plan skill

## [0.2.0] - 2026-07-08

### Added
- Web UI redesigned: drafting-paper light / blueprint-night dark mode
  (prefers-color-scheme), monospace chrome around a serif reading column,
  path breadcrumbs on every page, empty-state illustrations, fluid type
  scale for wide displays; fully offline (CDN dependencies removed)
- Footer credit linking to the author's GitHub
- Pagination on the projects list and project detail pages (50 per page)
- MCP: list_plan_files_tool pages results (default 50, cap 200); get_plan_file_tool
  truncates content past max_chars (default 100k) with truncated/total_chars fields
- Plans past 1M characters are served as plain text instead of rendered markdown
- Styled HTML error pages (400/404/500) for browser routes; /api/* keeps JSON;
  unhandled exceptions log the traceback and never leak it to the page
- Flash messages: project deletion confirms with a success banner; saving a plan
  with unchanged content explains why no new version was created

### Changed
- Markdown rendering runs off the event loop and is cached by content hash;
  a multi-megabyte plan no longer freezes the server for all clients (8.7s -> 57ms)
- Dashboard stats computed in SQL instead of loading every plan file (fixes N+1)

### Fixed
- MCP server startup never initialized the database, so every DB-backed tool
  failed in a fresh server process (added end-to-end stdio regression test)
- 'list --project X --output json' printed a table instead of JSON
- Emoji in CLI output crashed cp1252 Windows consoles
- API returned 200 with an empty list for a nonexistent project's plans (now 404)

## [0.1.0] - 2026-07-07

### Added
- `flanner --version`, `--verbose`/`--quiet` global flags
- `flanner list --output json` for machine-readable output
- Configuration via environment variables: `FLANNER_HOME`, `FLANNER_DB_PATH`, `FLANNER_WEB_PORT`
- Structured exception hierarchy (`FlannerError` and subclasses)
- Schema version stamping (`PRAGMA user_version`) for future migrations
- Reproducible benchmark (`benchmarks/bench.py`) with numbers in the README
- Pytest suite with an enforced import-boundary test; CI on Python 3.10/3.12
- MIT license

### Changed
- Package restructured: `src/` is now the installable `flanner` package with
  web assets inside it; `setup.py`/`requirements.txt` replaced by `pyproject.toml`
- Version reset to 0.1.0 (1.0.0 was never released)
- CLI error paths exit with code 1 (previously 0); exit codes: 0 success, 1 error
- Web UI binds 127.0.0.1 by default (previously 0.0.0.0)
- Library modules log via `logging` instead of printing

### Fixed
- Web routes crashed on current Starlette (old `TemplateResponse` signature)
- SQLite connection-pool exhaustion after ~15 rapid MCP tool calls (NullPool)
- Database writes now roll back on failure instead of leaving the session dirty
- `sync` reported wrong old version in its update messages
