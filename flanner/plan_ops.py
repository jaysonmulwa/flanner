"""Shared plan write-path.

`write_version` is the one place that turns a body of markdown into a stored
plan version: frontmatter, filename, save to disk, hash, version record. The
MCP server and the web UI both call it so the sequence cannot drift between
surfaces. Callers keep their own validation, current-version bump, commit, and
response shaping.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .database import PlanFileModel, ProjectModel, VersionModel, create_version
from .exceptions import DatabaseError
from .frontmatter import create_plan_file_content, generate_frontmatter
from .storage import save_plan_file_with_frontmatter
from .utils import generate_file_name, hash_content, utcnow


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

    Generates frontmatter, combines it with the body, saves the versioned file
    in the project's plan directory, hashes the body, and creates the version
    row. Does not bump ``plan_file.current_version`` or commit; the caller owns
    that so it can update related state in one transaction.
    """
    root = project.project_root
    if root is None:
        # Callers guard this and return a friendly message; belt-and-suspenders.
        raise DatabaseError(f"Project '{project.name}' has no project_root configured")

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
