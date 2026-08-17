"""Tests for catalog/disk reconciliation (PRD Phase 1)."""

from pathlib import Path

import pytest

from flanner.database import (
    PlanFileModel,
    create_plan_file,
    create_project,
    get_session,
    list_versions,
)
from flanner.plan_ops import create_plan, record_new_version
from flanner.reconcile import reconcile_project


@pytest.fixture
def project(db, tmp_path):
    session = get_session()
    root = tmp_path / "proj"
    root.mkdir()
    proj = create_project(session, name="p", project_root=str(root), auto_gitignore=False)
    create_plan(session, project=proj, name="alpha", content="# one\n", created_by="test")
    session.commit()
    return session, proj


def _plan_dir(project):
    return Path(project.project_root) / project.plan_directory


def test_clean_project_has_no_findings(project):
    session, proj = project
    assert reconcile_project(session, proj) == []


def test_missing_file_is_reported_but_not_repaired(project):
    session, proj = project
    (_plan_dir(proj) / "alpha_v1.md").unlink()
    findings = reconcile_project(session, proj, repair=True)
    assert [f.kind for f in findings] == ["missing_file"]
    assert not findings[0].repairable


def test_hash_mismatch_detects_outside_edit(project):
    session, proj = project
    path = _plan_dir(proj) / "alpha_v1.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nsnuck in\n", encoding="utf-8")
    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["hash_mismatch"]
    assert not findings[0].repairable


def test_orphan_file_is_adopted_by_repair(project):
    session, proj = project
    plan = session.query(PlanFileModel).filter_by(name="alpha").one()
    # Write a v2 by hand, as a restored backup or an out-of-band copy would be.
    original = (_plan_dir(proj) / "alpha_v1.md").read_text(encoding="utf-8")
    orphan = _plan_dir(proj) / "alpha_v2.md"
    orphan.write_text(original.replace("version: 1", "version: 2"), encoding="utf-8")

    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["orphan_file"]
    assert findings[0].repairable

    reconcile_project(session, proj, repair=True)
    assert reconcile_project(session, proj) == []
    assert {v.version for v in list_versions(session, plan.id)} == {1, 2}
    session.refresh(plan)
    assert plan.current_version == 2


def test_repair_never_overwrites_the_orphan_file(project):
    session, proj = project
    original = (_plan_dir(proj) / "alpha_v1.md").read_text(encoding="utf-8")
    orphan = _plan_dir(proj) / "alpha_v2.md"
    body = original.replace("version: 1", "version: 2")
    orphan.write_text(body, encoding="utf-8")
    reconcile_project(session, proj, repair=True)
    assert orphan.read_text(encoding="utf-8") == body


def test_stale_current_version_is_corrected(project):
    session, proj = project
    plan = session.query(PlanFileModel).filter_by(name="alpha").one()
    record_new_version(
        session, project=proj, plan_file=plan, content="# two\n", created_by="test", notes=""
    )
    session.query(PlanFileModel).filter_by(id=plan.id).update({"current_version": 1})
    session.commit()
    session.expire(plan)

    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["stale_current_version"]

    reconcile_project(session, proj, repair=True)
    session.expire(plan)
    assert plan.current_version == 2
    assert reconcile_project(session, proj) == []


def test_unmanaged_markdown_is_ignored(project):
    session, proj = project
    (_plan_dir(proj) / "notes.md").write_text("# just notes\n", encoding="utf-8")
    assert reconcile_project(session, proj) == []


def test_file_for_unknown_plan_is_reported(project, tmp_path):
    session, proj = project
    # Frontmatter naming a plan id this catalog has never seen.
    other = create_project(
        session, name="other", project_root=str(tmp_path / "other"), auto_gitignore=False
    )
    (tmp_path / "other").mkdir(exist_ok=True)
    stray_plan = create_plan_file(session, project_id=other.id, name="ghost")
    session.commit()
    template = (_plan_dir(proj) / "alpha_v1.md").read_text(encoding="utf-8")
    stray = _plan_dir(proj) / "ghost_v1.md"
    import re

    text = re.sub(r"plan_file_id: .*", f"plan_file_id: {stray_plan.id}", template)
    text = re.sub(r"plan_name: .*", "plan_name: ghost", text)
    stray.write_text(text, encoding="utf-8")

    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["unknown_plan"]
    assert not findings[0].repairable


def test_project_without_root_is_reported(db, tmp_path):
    session = get_session()
    proj = create_project(session, name="rootless", project_root=None, auto_gitignore=False)
    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["no_project_root"]


# --- signature integrity (PRD §12: the catalog is an index, not the authority) ---


def test_a_forged_signature_is_detected(project):
    session, proj = project
    from flanner.database import get_artifact

    version = list_versions(session, session.query(PlanFileModel).one().id)[0]
    get_artifact(session, version.artifact_id).signature = "forged"
    session.commit()

    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["signature_invalid"]
    assert not findings[0].repairable


def test_a_version_whose_artifact_vanished_is_detected(project):
    session, proj = project
    from flanner.database import ArtifactModel

    session.query(ArtifactModel).delete()
    session.commit()

    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["artifact_missing"]


def test_a_peers_signature_is_reported_as_unverified_not_passed(project):
    """A clean report must never overstate what was actually checked."""
    session, proj = project
    from flanner.database import get_artifact

    version = list_versions(session, session.query(PlanFileModel).one().id)[0]
    get_artifact(session, version.artifact_id).actor_device_id = "dev_someone_else"
    session.commit()

    findings = reconcile_project(session, proj)
    assert [f.kind for f in findings] == ["unverified_signer"]
    assert findings[0].informational
    assert not findings[0].repairable


def test_versions_predating_artifacts_are_not_reported(project):
    session, proj = project
    version = list_versions(session, session.query(PlanFileModel).one().id)[0]
    version.artifact_id = None  # written by an older flanner
    session.commit()
    assert reconcile_project(session, proj) == []
