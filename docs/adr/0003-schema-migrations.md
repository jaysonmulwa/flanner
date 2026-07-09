# ADR 0003: Schema migrations via a versioned runner

Status: accepted

## Context

The catalog is a local SQLite file (see [ADR 0001](0001-sqlite-catalog-with-markdown-files.md)).
`PRAGMA user_version` stamps the schema version and refuses a database newer
than the running flanner. That protects against silent corruption, but it did
not upgrade an existing database when the schema changed: there was no runner,
so schema v2 would have had no path forward for v1 files.

## Decision

Keep a `MIGRATIONS` registry in `flanner/database.py` mapping each target
version to the step that upgrades from the previous one. `_apply_schema`:

- creates tables with `create_all` on a fresh file and stamps `SCHEMA_VERSION`
- on an existing file, adds any brand-new whole tables with `create_all`, then
  runs the pending migrations in order for in-place changes (ADD COLUMN,
  backfills, index changes), stamping `user_version` after each
- refuses a database whose version is newer than supported

`create_all` never alters existing tables, so in-place changes must be written
as explicit migration steps. A gap in the registry is a hard error rather than
a silent skip.

## Alternatives considered

- **Alembic:** the standard migration tool, but it is a heavy dependency and a
  separate versioning system for a single-file local database with a handful of
  tables. The stdlib-plus-SQLAlchemy runner is a few dozen lines and enough.
- **create_all only:** what we had. It creates missing tables but never alters
  existing ones, so column changes would silently not apply.
- **Drop and recreate:** unacceptable; the catalog is the user's data.

## Consequences

- Every `SCHEMA_VERSION` bump needs a matching `MIGRATIONS` entry and a test.
- Migrations run inside a transaction and are ordered; a failure aborts the
  upgrade rather than leaving a half-migrated database.
- If flanner ever moves to a hosted PostgreSQL catalog, this runner is the
  seam to revisit (Alembic likely becomes worth its weight there).
