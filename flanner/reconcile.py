"""Catalog/disk reconciliation.

The SQLite catalog is an index, not the only copy of plan content (PRD §12.4):
the managed `_vN.md` files are the portable persisted representation. The two
can drift — a file deleted or edited outside flanner, a row whose file never
landed, a version file restored from backup that the catalog never saw.

This module compares the two and reports every disagreement with enough
detail to act on. Repair is deliberately conservative: only findings that
cannot lose content are fixed automatically (PRD §14.5, §24 "rebuild catalog
from managed artifact files"). Anything ambiguous is reported for a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .database import PlanFileModel, ProjectModel, VersionModel, create_version, list_versions
from .database import list_plan_files as db_list_plan_files
from .frontmatter import read_managed
from .utils import hash_content

# Findings that repair can fix without ever discarding content.
REPAIRABLE = {"orphan_file", "stale_current_version"}


@dataclass(frozen=True)
class Finding:
    """One disagreement between the catalog and the plan directory."""

    kind: str
    plan: str
    detail: str
    path: str | None = None

    @property
    def repairable(self) -> bool:
        return self.kind in REPAIRABLE


def reconcile_project(
    session: Session, project: ProjectModel, *, repair: bool = False
) -> list[Finding]:
    """Compare a project's catalog against its plan directory.

    With ``repair=True``, adopts orphan version files into the catalog and
    corrects a plan's ``current_version`` when it disagrees with the rows.
    Never deletes rows or files, and never overwrites a file, so a mistaken
    run cannot lose a plan.
    """
    findings: list[Finding] = []
    root = project.project_root
    if not root:
        return [Finding("no_project_root", "-", f"Project '{project.name}' has no project_root")]

    plan_dir = Path(root) / project.plan_directory
    plans = db_list_plan_files(session, project.id)
    known_paths: set[str] = set()
    dirty = False

    for plan in plans:
        versions = list_versions(session, plan.id)
        for version in versions:
            findings.extend(_check_version(plan, version, known_paths))
        if versions:
            highest = max(v.version for v in versions)
            if plan.current_version != highest:
                findings.append(
                    Finding(
                        "stale_current_version",
                        plan.name,
                        f"catalog says v{plan.current_version}, highest recorded is v{highest}",
                    )
                )
                if repair:
                    plan.current_version = highest
                    dirty = True

    if plan_dir.is_dir():
        for orphan in _find_orphans(plan_dir, known_paths):
            path, fm_data, body = orphan
            owner = _plan_for(session, plans, fm_data)
            if owner is None:
                findings.append(
                    Finding(
                        "unknown_plan",
                        str(fm_data.get("plan_name", "?")),
                        "file references a plan that is not in this catalog; run 'flanner sync'",
                        str(path),
                    )
                )
                continue
            version_num = fm_data.get("version")
            findings.append(
                Finding(
                    "orphan_file",
                    owner.name,
                    f"v{version_num} exists on disk but not in the catalog",
                    str(path),
                )
            )
            if repair and isinstance(version_num, int):
                create_version(
                    session,
                    plan_file_id=owner.id,
                    version=version_num,
                    file_path=str(path),
                    content_hash=hash_content(body),
                    created_by=str(fm_data.get("created_by", "unknown")),
                    notes="Adopted by reconcile",
                )
                if version_num > owner.current_version:
                    owner.current_version = version_num
                    dirty = True

    if dirty:
        session.commit()
    return findings


def _check_version(
    plan: PlanFileModel, version: VersionModel, known_paths: set[str]
) -> list[Finding]:
    """Findings for one recorded version: file present, and content unchanged."""
    findings: list[Finding] = []
    path = Path(version.file_path)
    known_paths.add(str(path.resolve()) if path.exists() else str(path))
    if not path.exists():
        return [
            Finding(
                "missing_file",
                plan.name,
                f"v{version.version} is recorded but its file is gone",
                version.file_path,
            )
        ]
    try:
        _, body = read_managed(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [
            Finding("unreadable_file", plan.name, f"v{version.version}: {e}", version.file_path)
        ]
    if hash_content(body) != version.content_hash:
        findings.append(
            Finding(
                "hash_mismatch",
                plan.name,
                f"v{version.version} was edited outside flanner; content no longer matches",
                version.file_path,
            )
        )
    return findings


def _find_orphans(plan_dir: Path, known_paths: set[str]) -> list[tuple[Path, dict[str, Any], str]]:
    """Managed plan files in the directory that no version row points at."""
    orphans = []
    for path in sorted(plan_dir.rglob("*.md")):
        if str(path.resolve()) in known_paths or str(path) in known_paths:
            continue
        try:
            fm_data, body = read_managed(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not fm_data.get("mcp_plan_file"):
            continue  # not ours; the guard hook keeps these out, but users can add them
        orphans.append((path, fm_data, body))
    return orphans


def _plan_for(
    session: Session, plans: list[PlanFileModel], fm_data: dict[str, Any]
) -> PlanFileModel | None:
    """Match a file's frontmatter to a catalog plan by id, then by name."""
    raw_id = fm_data.get("plan_file_id")
    if raw_id:
        try:
            wanted = UUID(str(raw_id))
        except ValueError:
            wanted = None
        if wanted is not None:
            for plan in plans:
                if plan.id == wanted:
                    return plan
    name = fm_data.get("plan_name")
    for plan in plans:
        if plan.name == name:
            return plan
    return None
