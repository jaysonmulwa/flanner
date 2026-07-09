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
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    try:
        _apply_schema(engine)  # existing db at v1
        assert _user_version(engine) == 1

        ran = []

        def migration_2(conn):
            ran.append(2)
            conn.exec_driver_sql("CREATE TABLE marker (id INTEGER)")

        monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 2)
        monkeypatch.setitem(dbmod.MIGRATIONS, 2, migration_2)

        _apply_schema(engine)

        assert ran == [2]  # the pending step actually executed
        assert _user_version(engine) == 2
    finally:
        engine.dispose()


def test_missing_migration_is_an_error(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'gap.db'}")
    try:
        _apply_schema(engine)  # v1
        monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 3)  # no migration 2 or 3 registered
        with pytest.raises(DatabaseError, match="No migration registered"):
            _apply_schema(engine)
    finally:
        engine.dispose()
