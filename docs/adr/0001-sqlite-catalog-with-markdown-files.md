# ADR 0001: SQLite catalog with plain-markdown plan files

Status: accepted (retroactive; documents the design as built)

## Context

Flanner tracks versioned plan files that AI assistants create inside a user's
project. Two kinds of state exist: the plan content itself, and the catalog
(projects, versions, Jira links). Content must stay reviewable by humans and
usable by tools that know nothing about flanner.

## Decision

- Plan content lives as plain markdown files in the project (`.plans/`),
  identified by YAML frontmatter, git-ignored by default.
- The catalog lives in a single SQLite file under `~/.flanner/` (override via
  `FLANNER_HOME`/`FLANNER_DB_PATH`), accessed through SQLAlchemy.
- Content changes are detected by SHA-256 hash; each version is a new file
  (`name_v2.md`), never an in-place rewrite.

## Alternatives considered

- **Everything in SQLite (content too):** loses direct editability; plans stop
  being reviewable in editors/PRs without flanner installed.
- **Everything in files (no DB):** version lineage, cross-project queries, and
  Jira links would require scanning and parsing every file on every command.
- **PostgreSQL:** nothing here needs a server; local-first is the product.
  SQLAlchemy keeps a migration path open if a hosted mode ever lands.

## Consequences

- Files and catalog can drift; `flanner sync` reconciles from frontmatter.
- SQLite's writer model is fine for a single-user local tool; concurrent
  writers are out of scope (documented ceiling).
