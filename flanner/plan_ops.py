"""Shared plan write-path.

`write_version` is the one place that turns a body of markdown into a stored
plan version: frontmatter, filename, save to disk, hash, version record. The
MCP server and the web UI both call it so the sequence cannot drift between
surfaces.

`record_new_version` is the locked update path: it refreshes the plan row,
picks the next free version number (skipping any number whose file already
exists on disk), writes the version, bumps ``current_version``, and commits,
all under a cross-process lock. Two MCP clients, or the MCP server and the
web UI, can therefore never write the same version number or overwrite each
other's files (PRD Phase 1: single-writer discipline).
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.orm import Session

from .database import PlanFileModel, ProjectModel, VersionModel, create_version
from .database import create_plan_file as db_create_plan_file
from .exceptions import DatabaseError
from .frontmatter import create_plan_file_content, generate_frontmatter
from .storage import save_plan_file_with_frontmatter
from .utils import generate_file_name, hash_content, utcnow

# ponytail: one coarse lock per plan directory, file-based so it works across
# unrelated processes on every OS. Per-plan locks if contention ever matters.
_LOCK_NAME = ".flanner.lock"
_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 30.0
_LOCK_RETRY_S = 0.05


@contextlib.contextmanager
def plan_write_lock(project_root: str, plan_directory: str) -> Iterator[None]:
    """Cross-process write lock for a project's plan directory.

    Lock file creation with O_EXCL is atomic on all supported platforms. A
    lock file older than ``_LOCK_STALE_S`` is treated as abandoned (crashed
    holder) and taken over.
    """
    lock_dir = Path(project_root) / plan_directory
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / _LOCK_NAME
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock_path.stat().st_mtime > _LOCK_STALE_S:
                    lock_path.unlink()
                    continue
            if time.monotonic() > deadline:
                raise DatabaseError(
                    f"Timed out waiting for plan write lock at {lock_path}"
                ) from None
            time.sleep(_LOCK_RETRY_S)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


def create_plan(
    session: Session,
    *,
    project: ProjectModel,
    name: str,
    content: str,
    description: str = "",
    created_by: str,
    auto_version: bool = True,
) -> tuple[PlanFileModel, VersionModel]:
    """Create a plan record and write its initial version (v1).

    The one create sequence shared by every surface. Callers keep their own
    name validation and duplicate-error shaping; ``DuplicateError`` and
    ``NotFoundError`` from the database layer propagate.
    """
    plan_file = db_create_plan_file(
        session,
        project_id=project.id,
        name=name,
        description=description,
        auto_version=auto_version,
    )
    version = write_version(
        session,
        project=project,
        plan_file=plan_file,
        version=1,
        content=content,
        created_by=created_by,
        notes="Initial version",
    )
    return plan_file, version


def record_new_version(
    session: Session,
    *,
    project: ProjectModel,
    plan_file: PlanFileModel,
    content: str,
    created_by: str,
    notes: str,
) -> VersionModel:
    """Write the next version of an existing plan and commit, under the lock.

    Refreshes ``plan_file`` after acquiring the lock so a concurrent writer's
    bump is seen, and never reuses a version number whose file already exists
    on disk (catalog drift heals forward instead of overwriting history).
    """
    root = project.project_root
    if root is None:
        raise DatabaseError(f"Project '{project.name}' has no project_root configured")

    with plan_write_lock(root, project.plan_directory):
        session.refresh(plan_file)
        next_version = plan_file.current_version + 1
        plan_dir = Path(root) / project.plan_directory
        while (plan_dir / generate_file_name(plan_file.name, next_version)).exists():
            next_version += 1
        version = _write_version_unlocked(
            session,
            project=project,
            plan_file=plan_file,
            version=next_version,
            content=content,
            created_by=created_by,
            notes=notes,
        )
        plan_file.current_version = next_version
        plan_file.updated_at = utcnow()
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    return version


def write_version(
    session: Session,
    *,
    project: ProjectModel,
    plan_file: PlanFileModel,
    version: int,
    content: str,
    created_by: str,
    notes: str,
) -> VersionModel:
    """Write one version of a plan to disk and record it; return the version row.

    Takes the plan write lock, then delegates. Used by the create flows where
    the version number is fixed (v1); update flows use ``record_new_version``.
    Does not bump ``plan_file.current_version`` or commit; the caller owns
    that so it can update related state in one transaction.
    """
    root = project.project_root
    if root is None:
        # Callers guard this and return a friendly message; belt-and-suspenders.
        raise DatabaseError(f"Project '{project.name}' has no project_root configured")
    with plan_write_lock(root, project.plan_directory):
        return _write_version_unlocked(
            session,
            project=project,
            plan_file=plan_file,
            version=version,
            content=content,
            created_by=created_by,
            notes=notes,
        )


def _write_version_unlocked(
    session: Session,
    *,
    project: ProjectModel,
    plan_file: PlanFileModel,
    version: int,
    content: str,
    created_by: str,
    notes: str,
) -> VersionModel:
    """The raw write sequence; callers must hold the plan write lock."""
    root = project.project_root
    if root is None:
        raise DatabaseError(f"Project '{project.name}' has no project_root configured")

    # Normalize line endings before hashing and saving so the stored hash
    # matches the LF content that is written and later read back (CRLF from web
    # form submissions would otherwise make an unchanged plan look modified).
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    frontmatter_str = generate_frontmatter(
        project_id=project.id,
        project_name=project.name,
        plan_file_id=plan_file.id,
        plan_name=plan_file.name,
        version=version,
        created_by=created_by,
        created_at=utcnow(),
    )
    full_content = create_plan_file_content(frontmatter_str, content)
    file_name = generate_file_name(plan_file.name, version)
    file_path = save_plan_file_with_frontmatter(
        project_root=root,
        plan_directory=project.plan_directory,
        file_name=file_name,
        content=full_content,
    )
    return create_version(
        session,
        plan_file_id=plan_file.id,
        version=version,
        file_path=file_path,
        content_hash=hash_content(content),  # hash the body, not the frontmatter
        created_by=created_by,
        notes=notes,
    )
