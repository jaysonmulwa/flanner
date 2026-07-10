# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [Unreleased]

### Changed
- `flanner web` checks the port first and, if it is taken, prints an actionable
  message (how to pick another port / set `FLANNER_WEB_PORT`) and exits 1,
  instead of letting a raw bind error scroll past. `--open-browser` now opens
  once the server is actually accepting connections, on a background thread, so
  it never delays startup.

### Fixed
- Editing a plan in the web UI no longer corrupts its line endings. Browser
  forms submit CRLF; the file was written in text mode on Windows, doubling the
  carriage returns (`\r\r\n`) and gaining a blank line on every save. Plans are
  now normalized to LF and written without OS newline translation, and the body
  is normalized before hashing so an unchanged plan is not seen as modified.
- `flanner init` now also registers the MCP server with **Claude Code** (the
  CLI) by writing a project `.mcp.json`, not only Claude Desktop. Claude Code
  reads `.mcp.json`, so previously CLI users ran `init` and never saw the flanner
  tools under `/mcp`. Existing entries in `.mcp.json` are preserved; the portable
  `flanner-mcp` command is used so the file is shareable across a team.

## [0.6.0] - 2026-07-10

### Added
- Linear integration. Link plan files to Linear issues (`ENG-123`) via a
  `flanner linear` CLI group and matching MCP tools, mirroring the JIRA link
  surface:
  - Link-only by default: stores the issue id and builds a `linear.app` URL, no
    network or credentials.
  - API sync when `LINEAR_API_KEY` is set: `link` verifies the issue exists and
    caches its title/state, `--attach-url` attaches a URL to the issue, and
    `flanner linear refresh` re-pulls live status. The key is read from the
    environment only, never stored on disk. The GraphQL client uses the standard
    library, so it adds no runtime dependency.
  - `flanner linear auth` validates the key against Linear and prints the MCP
    server config snippet (with `LINEAR_API_KEY` in its `env`) so the agent's
    server process gets the same access.
  - Web UI: the plan viewer shows a "Linked Linear issues" panel (id, cached
    state, title, link), and the project page marks linked plans with a
    `Linear ×N` badge.
  - See docs/LINEAR_INTEGRATION.md.

## [0.5.0] - 2026-07-10

### Added
- `flanner-mcp` console script to run the MCP server (equivalent to
  `python -m flanner.server`); cleaner for client configs and registry listings
- `server.json` manifest for submitting to the MCP registry

### Changed
- `flanner init` and `flanner start` register/print the MCP server config with
  the absolute interpreter path (`sys.executable -m flanner.server`) instead of
  a bare command, so the client app spawns it regardless of its PATH (venv/pipx
  installs are not on the GUI app's PATH)

## [0.4.1] - 2026-07-09

### Fixed
- PyPI project page showed `pip install -e .`; the rendered description now uses
  `pip install flanner` (0.4.0 was built before the README install line was updated).

### Added
- README explains the skill and guard-hook enforcement layer on top of the MCP tools.

## [0.4.0] - 2026-07-09

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
