"""Tests for signed artifacts and lineage (PRD §12)."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flanner import artifacts, identity


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def body():
    return b"# Architecture\n\nthe plan body\n"


def _make(body_bytes=b"x", **kw):
    params = {
        "artifact_type": artifacts.PLAN_VERSION,
        "workspace_id": "ws_1",
        "content_hash": artifacts.hash_bytes(body_bytes),
        "plan_file_id": "plan-1",
    }
    params.update(kw)
    return artifacts.make_artifact(**params)


# --- canonical form and identity ---


def test_canonical_form_is_key_order_independent():
    a = artifacts.canonical_bytes({"b": 2, "a": 1})
    b = artifacts.canonical_bytes({"a": 1, "b": 2})
    assert a == b == b'{"a":1,"b":2}'


def test_line_endings_do_not_change_content_identity():
    assert artifacts.hash_text("one\r\ntwo\r\n") == artifacts.hash_text("one\ntwo\n")


def test_artifact_id_is_the_hash_of_its_envelope(home, body):
    art = _make(body)
    assert art.artifact_id == artifacts.compute_artifact_id(art)
    assert art.artifact_id.startswith("sha256:")


def test_same_content_and_stamp_yields_the_same_id_on_two_devices(tmp_path, monkeypatch, body):
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    key = Ed25519PrivateKey.generate()
    ids = set()
    for name in ("device-a", "device-b"):
        monkeypatch.setenv("FLANNER_HOME", str(tmp_path / name))
        ids.add(_make(body, created_at=stamp, signing_key=key).artifact_id)
    assert len(ids) == 1  # identity is content-derived, not machine-derived


def test_different_content_yields_a_different_id(home):
    assert _make(b"one").artifact_id != _make(b"two").artifact_id


def test_parent_order_does_not_affect_identity(home):
    left = _make(parents=["sha256:aaa", "sha256:bbb"])
    right = _make(parents=["sha256:bbb", "sha256:aaa"], created_at=_stamp(left))
    assert left.artifact_id == right.artifact_id


def _stamp(art):
    return datetime.fromisoformat(art.created_at.replace("Z", "+00:00"))


def test_unknown_artifact_type_is_refused(home):
    with pytest.raises(ValueError, match="Unknown artifact type"):
        _make(artifact_type="plan.invented")


# --- verification ---


def test_valid_artifact_verifies_including_its_payload(home, body):
    art = _make(body)
    verdict = artifacts.verify_artifact(art, identity.device_public_key_b64(), body)
    assert verdict.ok and bool(verdict) is True


def test_tampered_envelope_field_is_caught(home, body):
    art = _make(body)
    forged = replace(art, workspace_id="ws_someone_else")
    verdict = artifacts.verify_artifact(forged, identity.device_public_key_b64(), body)
    assert not verdict
    assert "does not match its envelope" in verdict.reason


def test_reassigning_the_id_to_match_still_fails_on_signature(home, body):
    # An attacker who edits a field and recomputes the id cannot also forge
    # the signature without the device's private key.
    art = _make(body)
    forged = replace(art, workspace_id="ws_someone_else")
    forged = replace(forged, artifact_id=artifacts.compute_artifact_id(forged))
    verdict = artifacts.verify_artifact(forged, identity.device_public_key_b64(), body)
    assert not verdict
    assert "signature" in verdict.reason


def test_swapped_payload_is_caught(home, body):
    art = _make(body)
    verdict = artifacts.verify_artifact(art, identity.device_public_key_b64(), b"different body")
    assert not verdict
    assert "content_hash" in verdict.reason


def test_another_devices_key_does_not_verify(home, body, tmp_path, monkeypatch):
    art = _make(body)
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "intruder"))
    verdict = artifacts.verify_artifact(art, identity.device_public_key_b64(), body)
    assert not verdict


def test_future_protocol_version_is_refused_not_crashed(home, body):
    art = replace(_make(body), protocol_version=99)
    verdict = artifacts.verify_artifact(art, identity.device_public_key_b64(), body)
    assert not verdict
    assert "protocol version" in verdict.reason


def test_verification_never_raises_on_hostile_input(home, body):
    art = _make(body)
    for forged in (
        replace(art, signature="!!!not base64!!!"),
        replace(art, signature=""),
        replace(art, artifact_id="sha256:0000"),
        replace(art, artifact_type="plan.invented"),
    ):
        assert (
            artifacts.verify_artifact(forged, identity.device_public_key_b64(), body).ok is False
        )


def test_envelope_survives_the_wire_form(home, body):
    art = _make(body)
    restored = artifacts.Artifact.from_dict(art.to_dict())
    assert restored == art
    assert artifacts.verify_artifact(restored, identity.device_public_key_b64(), body)


def test_malformed_wire_form_is_reported(home):
    with pytest.raises(ValueError, match="Malformed artifact envelope"):
        artifacts.Artifact.from_dict({"artifact_type": "plan.version"})


# --- lineage (PRD §12.3) ---


def test_linear_history_has_one_head():
    graph = {"a": (), "b": ("a",), "c": ("b",)}
    assert artifacts.find_heads(graph) == {"c"}
    assert artifacts.is_conflicted(graph) is False
    assert artifacts.is_ancestor("a", "c", graph)
    assert not artifacts.is_ancestor("c", "a", graph)


def test_divergent_history_is_conflicted_and_names_both_heads():
    graph = {"base": (), "left": ("base",), "right": ("base",)}
    assert artifacts.find_heads(graph) == {"left", "right"}
    assert artifacts.is_conflicted(graph) is True
    assert artifacts.merge_base("left", "right", graph) == {"base"}


def test_a_merge_resolves_the_conflict():
    graph = {"base": (), "left": ("base",), "right": ("base",), "merge": ("left", "right")}
    assert artifacts.find_heads(graph) == {"merge"}
    assert artifacts.is_conflicted(graph) is False
    assert artifacts.ancestors("merge", graph) == {"left", "right", "base"}


def test_lineage_tolerates_a_cycle_from_a_hostile_peer():
    graph = {"a": ("b",), "b": ("a",)}
    assert artifacts.ancestors("a", graph) == {"a", "b"}  # terminates


def test_lineage_tolerates_parents_not_yet_received():
    # Events arrive out of order; a missing parent must not crash traversal.
    graph = {"child": ("absent-parent",)}
    assert artifacts.ancestors("child", graph) == {"absent-parent"}
