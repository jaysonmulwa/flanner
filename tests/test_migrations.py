"""Schema migration runner: fresh stamp, refuse-newer, and pending upgrades."""

import pytest
from sqlalchemy import create_engine

import flanner.database as dbmod
from flanner.database import SCHEMA_VERSION, _apply_schema
from flanner.exceptions import DatabaseError


def _user_version(engine):
    with engine.connect() as conn:
        return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def test_fresh_db_stamped_to_current(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        _apply_schema(engine)
        assert _user_version(engine) == SCHEMA_VERSION
    finally:
        engine.dispose()


def test_refuses_newer_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'future.db'}")
    try:
        _apply_schema(engine)  # -> current, tables created
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA user_version = 99")
        with pytest.raises(DatabaseError):
            _apply_schema(engine)
    finally:
        engine.dispose()


def test_runs_pending_migration(tmp_path, monkeypatch):
    """A database one version behind runs exactly the step it is missing.

    Written against whatever the current version happens to be, so a real
    schema bump does not need this test edited.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    try:
        _apply_schema(engine)  # fresh db, stamped current
        current = _user_version(engine)
        assert current == dbmod.SCHEMA_VERSION

        pending = current + 1
        ran = []

        def next_migration(conn):
            ran.append(pending)
            conn.exec_driver_sql("CREATE TABLE marker (id INTEGER)")

        monkeypatch.setattr(dbmod, "SCHEMA_VERSION", pending)
        monkeypatch.setitem(dbmod.MIGRATIONS, pending, next_migration)

        _apply_schema(engine)

        assert ran == [pending]  # the pending step actually executed
        assert _user_version(engine) == pending
    finally:
        engine.dispose()


def test_missing_migration_is_an_error(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'gap.db'}")
    try:
        _apply_schema(engine)  # fresh db, stamped current
        # Claim a version beyond the last registered step.
        monkeypatch.setattr(dbmod, "SCHEMA_VERSION", dbmod.SCHEMA_VERSION + 1)
        with pytest.raises(DatabaseError, match="No migration registered"):
            _apply_schema(engine)
    finally:
        engine.dispose()
