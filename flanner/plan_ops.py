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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from . import artifacts
from .database import (
    ArtifactModel,
    PlanFileModel,
    ProjectModel,
    VersionModel,
    create_version,
    get_plan_file,
    get_version,
    save_artifact,
)
from .database import create_plan_file as db_create_plan_file
from .exceptions import DatabaseError
from .frontmatter import create_plan_file_content, generate_frontmatter, read_managed
from .storage import save_plan_file_with_frontmatter
from .utils import generate_file_name, hash_content, utcnow

# ponytail: one coarse lock per plan directory, file-based so it works across
# unrelated processes on every OS. Per-plan locks if contention ever matters.
_LOCK_NAME = ".flanner.lock"
_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 30.0
_LOCK_RETRY_S = 0.05


def workspace_id_for(project: ProjectModel) -> str:
    """The workspace this project's artifacts belong to.

    Real workspace ids are opaque and issued by the control plane. Until a
    project joins one, artifacts still need a stable workspace to belong to,
    so a local id is derived from the project. It never leaves the machine,
    and no entitlement can ever grant a role in it, which is exactly why
    authorization stays advisory while a project is solo.
    """
    return project.workspace_id or f"local:{project.id}"


def _parent_artifact_ids(session: Session, plan_file: PlanFileModel) -> tuple[str, ...]:
    """The artifact this new version descends from, if there is one.

    Lineage is per plan, so a new version's parent is the latest signed
    version of the same plan. Versions written before artifacts existed have
    no id, which correctly yields a root rather than a broken link.
    """
    latest = get_version(session, plan_file.id, None)
    if latest is None or not latest.artifact_id:
        return ()
    return (latest.artifact_id,)


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

    # Sign the body before writing. The artifact's content hash covers the
    # body only, never the generated frontmatter (PRD §12.4), which is what
    # lets the resulting artifact id be written *into* that frontmatter
    # without changing what was signed.
    artifact = artifacts.make_artifact(
        artifact_type=artifacts.PLAN_VERSION,
        workspace_id=workspace_id_for(project),
        content_hash=artifacts.hash_text(content),
        plan_file_id=str(plan_file.id),
        parents=_parent_artifact_ids(session, plan_file),
        actor_user_id=created_by,
    )

    frontmatter_str = generate_frontmatter(
        project_id=project.id,
        project_name=project.name,
        plan_file_id=plan_file.id,
        plan_name=plan_file.name,
        version=version,
        created_by=created_by,
        created_at=utcnow(),
        artifact_id=artifact.artifact_id,
        parents=artifact.parents,
        workspace_id=artifact.workspace_id,
        actor_device_id=artifact.actor_device_id,
    )
    full_content = create_plan_file_content(frontmatter_str, content)
    file_name = generate_file_name(plan_file.name, version)
    file_path = save_plan_file_with_frontmatter(
        project_root=root,
        plan_directory=project.plan_directory,
        file_name=file_name,
        content=full_content,
    )
    save_artifact(
        session,
        artifact_id=artifact.artifact_id,
        artifact_type=artifact.artifact_type,
        workspace_id=artifact.workspace_id,
        content_hash=artifact.content_hash,
        actor_device_id=artifact.actor_device_id,
        created_at=artifact.created_at,
        signature=artifact.signature,
        plan_file_id=artifact.plan_file_id,
        parents=list(artifact.parents),
        actor_user_id=artifact.actor_user_id,
    )
    # The artifact id goes in with the INSERT. Assigning it afterwards would
    # leave it unflushed behind create_version's own commit, and a caller that
    # never commits (the create path does not) would silently lose the link,
    # breaking the parent chain for the next version.
    return create_version(
        session,
        plan_file_id=plan_file.id,
        version=version,
        file_path=file_path,
        content_hash=hash_content(content),  # hash the body, not the frontmatter
        created_by=created_by,
        notes=notes,
        artifact_id=artifact.artifact_id,
    )


@dataclass(frozen=True)
class MaterializeResult:
    """Outcome of turning a synced artifact into a working file."""

    version: VersionModel | None = None
    reason: str = ""
    conflict_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.version is not None


def _conflict_name(plan_name: str, version: int, artifact_id: str) -> str:
    """A deterministic filename for a version number two devices both used.

    PRD §12.4 requires both artifacts to be retained until someone merges
    them, so the loser of a filename race is parked under a name derived
    from its own artifact id: stable, collision-free, and obviously a
    conflict to anyone looking at the directory.
    """
    digest = artifact_id.split(":", 1)[-1][:12]
    return generate_file_name(f"{plan_name}__conflict-{digest}", version)


def materialize_version(
    session: Session,
    *,
    project: ProjectModel,
    envelope: dict[str, Any],
    managed_file: bytes,
) -> MaterializeResult:
    """Write a peer's verified plan version into this device's working tree.

    The caller has already verified the envelope and payload; this turns an
    accepted artifact into something a human and an agent can actually open.

    Frontmatter is regenerated rather than copied, because the project ids
    and name are this device's, while the artifact identity, plan name,
    version number, author, and timestamp all belong to the version and are
    preserved exactly. The body is written byte for byte, so the signed
    content hash still verifies here.
    """
    root = project.project_root
    if root is None:
        return MaterializeResult(reason=f"Project '{project.name}' has no project_root configured")

    artifact_id = str(envelope.get("artifact_id", ""))
    try:
        fm_data, body = read_managed(managed_file.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return MaterializeResult(reason=f"unreadable plan file: {e}")

    if artifacts.hash_text(body) != envelope.get("content_hash"):
        return MaterializeResult(reason="body does not match the signed content hash")

    plan_name = str(fm_data.get("plan_name") or "")
    raw_version = fm_data.get("version")
    if not plan_name or not isinstance(raw_version, int):
        return MaterializeResult(reason="plan file is missing its name or version")
    try:
        plan_uuid = UUID(str(fm_data.get("plan_file_id")))
    except (ValueError, TypeError):
        return MaterializeResult(reason="plan file has no usable plan_file_id")

    with plan_write_lock(root, project.plan_directory):
        existing = (
            session.query(VersionModel).filter_by(artifact_id=artifact_id).first()
            if artifact_id
            else None
        )
        if existing is not None:
            return MaterializeResult(version=existing, reason="already materialized")

        plan_file = get_plan_file(session, plan_uuid)
        if plan_file is None:
            clash = (
                session.query(PlanFileModel)
                .filter_by(project_id=project.id, name=plan_name)
                .first()
            )
            if clash is not None:
                # Same human name, different identity: two unrelated plans.
                # Merging them would silently fuse separate histories.
                return MaterializeResult(
                    reason=f"a different plan named '{plan_name}' already exists here"
                )
            plan_file = db_create_plan_file(
                session,
                project_id=project.id,
                name=plan_name,
                description=str(fm_data.get("description") or ""),
                plan_file_id=plan_uuid,
            )
        elif plan_file.project_id != project.id:
            return MaterializeResult(reason="plan belongs to a different project on this device")

        frontmatter_str = generate_frontmatter(
            project_id=project.id,
            project_name=project.name,
            plan_file_id=plan_file.id,
            plan_name=plan_name,
            version=raw_version,
            created_by=str(fm_data.get("created_by") or "peer"),
            created_at=_parse_stamp(fm_data.get("created_at")),
            artifact_id=artifact_id,
            parents=envelope.get("parents") or None,
            workspace_id=str(envelope.get("workspace_id") or ""),
            actor_device_id=str(envelope.get("actor_device_id") or ""),
        )

        # Never overwrite: a file already sitting on this version number is
        # either this very artifact or a concurrent one worth keeping.
        plan_dir = Path(root) / project.plan_directory
        file_name = generate_file_name(plan_name, raw_version)
        conflict_path = None
        target = plan_dir / file_name
        if target.exists() and artifacts.hash_text(read_managed(target.read_text("utf-8"))[1]) != (
            artifacts.hash_text(body)
        ):
            file_name = _conflict_name(plan_name, raw_version, artifact_id)
            conflict_path = str(plan_dir / file_name)

        file_path = save_plan_file_with_frontmatter(
            project_root=root,
            plan_directory=project.plan_directory,
            file_name=file_name,
            content=create_plan_file_content(frontmatter_str, body),
        )
        version = create_version(
            session,
            plan_file_id=plan_file.id,
            version=raw_version,
            file_path=file_path,
            content_hash=hash_content(body),
            created_by=str(fm_data.get("created_by") or "peer"),
            notes=str(fm_data.get("notes") or ""),
            artifact_id=artifact_id or None,
        )
        if raw_version > (plan_file.current_version or 0) and conflict_path is None:
            plan_file.current_version = raw_version
            plan_file.updated_at = utcnow()
        session.commit()

    return MaterializeResult(version=version, conflict_path=conflict_path)


def _parse_stamp(raw: Any) -> datetime | None:
    """The version's own timestamp, so materializing preserves when it was written."""
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Adoption:
    """What joining a workspace carried across, and what it left behind."""

    adopted: tuple[str, ...] = ()
    already_there: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def moved(self) -> int:
        return len(self.adopted)


def adopt_into_workspace(
    session: Session,
    *,
    project: ProjectModel,
    workspace_id: str,
) -> Adoption:
    """Re-root each plan's current content in a workspace it can sync from.

    A workspace id sits inside the signed envelope, so joining a team cannot
    move an artifact written before the join: re-signing it under the new
    workspace would produce a different id, and the signature that made the
    old one trustworthy would no longer describe anything. Without this, a
    team that adopts flanner after six months finds their existing plans
    invisible to each other, which reads as broken rather than as a design.

    So the current head of each plan is signed afresh into the workspace,
    with **no parents**. It is a root there, deliberately:

    - Its real parent lives in a workspace nobody else can verify. Pointing
      at it would hand every peer a link they can never resolve.
    - The history is not lost. Every local artifact stays exactly as it was,
      still verifiable, still on this machine. What does not travel is the
      record of *how* the plan reached its current state.

    That is the trade, and it is the honest one: teammates get the content
    they need today, and nobody is told a lineage crossed a boundary it
    did not. A plan with no signed version yet is skipped rather than
    invented.

    Idempotent. Adopting twice does nothing the second time, because the
    head is already in the workspace.
    """
    from .database import list_plan_files as _list_plan_files

    adopted: list[str] = []
    already: list[str] = []
    skipped: list[tuple[str, str]] = []

    for plan_file in _list_plan_files(session, project.id):
        latest = get_version(session, plan_file.id, None)
        if latest is None:
            skipped.append((plan_file.name, "no versions"))
            continue
        if not latest.content_hash:
            skipped.append((plan_file.name, "no content hash"))
            continue

        current = (
            session.query(ArtifactModel).filter_by(artifact_id=latest.artifact_id).first()
            if latest.artifact_id
            else None
        )
        if current is not None and current.workspace_id == workspace_id:
            already.append(plan_file.name)
            continue

        artifact = artifacts.make_artifact(
            artifact_type=artifacts.PLAN_VERSION,
            workspace_id=workspace_id,
            content_hash=latest.content_hash,
            plan_file_id=str(plan_file.id),
            parents=(),
            actor_user_id=latest.created_by or "user",
        )
        save_artifact(
            session,
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            workspace_id=artifact.workspace_id,
            content_hash=artifact.content_hash,
            plan_file_id=artifact.plan_file_id,
            parents=list(artifact.parents),
            created_at=artifact.created_at,
            actor_device_id=artifact.actor_device_id,
            actor_user_id=artifact.actor_user_id,
            signature=artifact.signature,
            organization_id=artifact.organization_id,
        )
        # The version row names the artifact that speaks for this version,
        # and after adoption that is the new one. Repointing it is what
        # makes the content reachable to a peer, because a plan version's
        # payload is found through this row, and what makes the *next*
        # version chain into the workspace rather than back across the
        # boundary.
        latest.artifact_id = artifact.artifact_id
        adopted.append(plan_file.name)

    session.commit()
    return Adoption(adopted=tuple(adopted), already_there=tuple(already), skipped=tuple(skipped))
