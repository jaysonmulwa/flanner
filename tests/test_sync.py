"""Manifest exchange and verified ingest between two devices (PRD §14)."""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from flanner import artifacts, identity, sync
from flanner.database import Base, get_artifact, list_artifacts, save_artifact

WS = "ws_team"


def make_session(path):
    """An independent catalog, so two devices can be simulated in one process."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class Device:
    """A device with its own catalog, key, and identity."""

    def __init__(self, path):
        self.session = make_session(path)
        self.key = Ed25519PrivateKey.generate()
        self.public_key = identity.public_key_b64(self.key.public_key())
        self.device_id = identity.device_id_for(self.key.public_key())

    def write(self, body: bytes, workspace_id=WS, plan_file_id="plan_1", parents=()):
        artifact = artifacts.make_artifact(
            artifact_type=artifacts.PLAN_VERSION,
            workspace_id=workspace_id,
            content_hash=artifacts.hash_bytes(body),
            plan_file_id=plan_file_id,
            parents=parents,
            signing_key=self.key,
        )
        save_artifact(
            self.session,
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            workspace_id=artifact.workspace_id,
            content_hash=artifact.content_hash,
            actor_device_id=artifact.actor_device_id,
            created_at=artifact.created_at,
            signature=artifact.signature,
            plan_file_id=artifact.plan_file_id,
            parents=list(artifact.parents),
            payload=body.decode(),
        )
        return artifact


@pytest.fixture
def alice(tmp_path):
    return Device(tmp_path / "alice.db")


@pytest.fixture
def bob(tmp_path):
    return Device(tmp_path / "bob.db")


@pytest.fixture
def knows_alice(alice):
    """Bob's view of the device registry: he knows Alice's key."""
    return {alice.device_id: alice.public_key}.get


# --- manifests ---


def test_manifest_lists_what_a_device_holds(alice):
    a1 = alice.write(b"one")
    a2 = alice.write(b"two", parents=[a1.artifact_id])
    manifest = sync.build_manifest(alice.session, WS)
    assert manifest.artifact_ids == frozenset({a1.artifact_id, a2.artifact_id})
    assert manifest.heads == frozenset({a2.artifact_id})  # a1 is a2's parent


def test_manifest_is_scoped_to_one_workspace(alice):
    alice.write(b"ours")
    alice.write(b"theirs", workspace_id="ws_other")
    assert len(sync.build_manifest(alice.session, WS).artifact_ids) == 1


def test_manifest_survives_the_wire_form(alice):
    alice.write(b"one")
    manifest = sync.build_manifest(alice.session, WS)
    assert sync.Manifest.from_dict(manifest.to_dict()) == manifest


def test_malformed_manifest_is_reported():
    with pytest.raises(ValueError, match="Malformed manifest"):
        sync.Manifest.from_dict({})


def test_difference_is_what_the_remote_has_and_we_lack(alice, bob, knows_alice):
    shared = alice.write(b"shared")
    # Bob already holds that one, because he synced it earlier.
    sync.ingest_artifact(
        bob.session,
        sync.LocalPeer(alice.session).fetch([shared.artifact_id])[0][0],
        None,
        knows_alice,
    )
    only_alice = alice.write(b"extra")

    missing = sync.missing_artifact_ids(
        sync.build_manifest(bob.session, WS), sync.build_manifest(alice.session, WS)
    )
    assert missing == {only_alice.artifact_id}
    assert shared.artifact_id not in missing


def test_two_devices_writing_the_same_body_produce_distinct_artifacts(alice, bob):
    """Identity covers who wrote it and when, not just the bytes.

    Content addressing dedupes a *replicated* artifact, never two independent
    authorings, which must stay separately attributable.
    """
    a = alice.write(b"same body")
    b = bob.write(b"same body")
    assert a.artifact_id != b.artifact_id
    assert a.content_hash == b.content_hash  # the body itself is identical


def test_difference_across_workspaces_is_empty(alice, bob):
    alice.write(b"one")
    local = sync.build_manifest(bob.session, "ws_other")
    remote = sync.build_manifest(alice.session, WS)
    assert sync.missing_artifact_ids(local, remote) == set()


# --- the happy path ---


def test_bob_pulls_what_alice_has(alice, bob, knows_alice):
    a1 = alice.write(b"one")
    a2 = alice.write(b"two", parents=[a1.artifact_id])

    report = sync.sync_from_peer(bob.session, sync.LocalPeer(alice.session), WS, knows_alice)

    assert report.ok
    assert set(report.accepted) == {a1.artifact_id, a2.artifact_id}
    assert get_artifact(bob.session, a1.artifact_id) is not None
    # Lineage crossed intact.
    assert sync.build_manifest(bob.session, WS).heads == frozenset({a2.artifact_id})


def test_sync_is_idempotent(alice, bob, knows_alice):
    alice.write(b"one")
    peer = sync.LocalPeer(alice.session)
    first = sync.sync_from_peer(bob.session, peer, WS, knows_alice)
    second = sync.sync_from_peer(bob.session, peer, WS, knows_alice)

    assert len(first.accepted) == 1
    assert second.accepted == [] and second.ok  # nothing left to want
    assert len(list_artifacts(bob.session)) == 1


def test_nothing_to_do_is_not_an_error(alice, bob, knows_alice):
    report = sync.sync_from_peer(bob.session, sync.LocalPeer(alice.session), WS, knows_alice)
    assert report.ok and report.accepted == []


def test_only_the_requested_workspace_crosses(alice, bob, knows_alice):
    alice.write(b"ours")
    alice.write(b"theirs", workspace_id="ws_other")
    sync.sync_from_peer(bob.session, sync.LocalPeer(alice.session), WS, knows_alice)
    assert {a.workspace_id for a in list_artifacts(bob.session)} == {WS}


# --- a peer is hostile until proven otherwise (PRD §14.4) ---


def test_tampered_artifact_is_refused(alice, bob, knows_alice):
    a1 = alice.write(b"one")
    row = get_artifact(alice.session, a1.artifact_id)
    row.content_hash = artifacts.hash_bytes(b"swapped")  # envelope no longer hashes to its id
    alice.session.commit()

    report = sync.sync_from_peer(bob.session, sync.LocalPeer(alice.session), WS, knows_alice)
    assert not report.ok
    assert "does not match its envelope" in report.rejected[0][1]
    assert list_artifacts(bob.session) == []  # nothing was stored


def test_artifact_from_an_unknown_device_is_refused(alice, bob):
    alice.write(b"one")
    knows_nobody = {}.get
    report = sync.sync_from_peer(bob.session, sync.LocalPeer(alice.session), WS, knows_nobody)
    assert not report.ok
    assert "no known key" in report.rejected[0][1]
    assert list_artifacts(bob.session) == []


def test_artifact_signed_by_a_different_key_is_refused(alice, bob):
    alice.write(b"one")
    # Bob believes Alice's device id maps to someone else's key.
    impostor = Ed25519PrivateKey.generate()
    wrong = {alice.device_id: identity.public_key_b64(impostor.public_key())}.get

    report = sync.sync_from_peer(bob.session, sync.LocalPeer(alice.session), WS, wrong)
    assert not report.ok
    assert "signature" in report.rejected[0][1]


def test_oversized_payload_is_refused_before_it_is_stored(alice, bob, knows_alice):
    a1 = alice.write(b"one")
    envelope = sync.LocalPeer(alice.session).fetch([a1.artifact_id])[0][0]
    huge = b"x" * (sync.MAX_PAYLOAD_BYTES + 1)

    verdict = sync.ingest_artifact(bob.session, envelope, huge, knows_alice)
    assert not verdict
    assert "exceeds" in verdict.reason
    assert list_artifacts(bob.session) == []


def test_a_peer_cannot_smuggle_in_unrequested_artifacts(alice, bob, knows_alice):
    wanted = alice.write(b"wanted")
    sneaky = alice.write(b"never asked for", workspace_id=WS)

    class Smuggler(sync.LocalPeer):
        def manifest(self, workspace_id):
            # Advertise only one artifact...
            return sync.Manifest(
                workspace_id=workspace_id, artifact_ids=frozenset({wanted.artifact_id})
            )

        def fetch(self, artifact_ids):
            # ...but deliver two.
            return super().fetch([*artifact_ids, sneaky.artifact_id])

    report = sync.sync_from_peer(bob.session, Smuggler(alice.session), WS, knows_alice)
    assert report.accepted == [wanted.artifact_id]
    assert any("not requested" in reason for _, reason in report.rejected)
    assert get_artifact(bob.session, sneaky.artifact_id) is None


def test_one_bad_artifact_does_not_abort_the_good_ones(alice, bob, knows_alice):
    good = alice.write(b"good")
    bad = alice.write(b"bad")
    row = get_artifact(alice.session, bad.artifact_id)
    row.signature = "forged"
    alice.session.commit()

    report = sync.sync_from_peer(bob.session, sync.LocalPeer(alice.session), WS, knows_alice)
    assert good.artifact_id in report.accepted
    assert any(aid == bad.artifact_id for aid, _ in report.rejected)


def test_a_peer_that_cannot_answer_is_reported_not_raised(bob, knows_alice):
    class Broken:
        def manifest(self, workspace_id):
            raise ConnectionError("peer went away")

        def fetch(self, artifact_ids):
            return []

    report = sync.sync_from_peer(bob.session, Broken(), WS, knows_alice)
    assert not report.ok
    assert "peer manifest unavailable" in report.rejected[0][1]


def test_a_peer_failing_mid_transfer_is_reported_not_raised(alice, bob, knows_alice):
    alice.write(b"one")

    class FailsOnFetch(sync.LocalPeer):
        def fetch(self, artifact_ids):
            raise ConnectionError("dropped")

    report = sync.sync_from_peer(bob.session, FailsOnFetch(alice.session), WS, knows_alice)
    assert not report.ok
    assert "peer fetch failed" in report.rejected[0][1]


def test_a_peer_on_another_protocol_version_is_refused(bob, knows_alice):
    class FromTheFuture:
        def manifest(self, workspace_id):
            return sync.Manifest(workspace_id=workspace_id, protocol_version=99)

        def fetch(self, artifact_ids):
            return []

    report = sync.sync_from_peer(bob.session, FromTheFuture(), WS, knows_alice)
    assert not report.ok
    assert "unsupported peer protocol" in report.rejected[0][1]


def test_a_peer_answering_for_the_wrong_workspace_is_refused(bob, knows_alice):
    class Confused:
        def manifest(self, workspace_id):
            return sync.Manifest(workspace_id="ws_somewhere_else")

        def fetch(self, artifact_ids):
            return []

    report = sync.sync_from_peer(bob.session, Confused(), WS, knows_alice)
    assert not report.ok
    assert "different workspace" in report.rejected[0][1]


def test_malformed_envelope_is_refused(bob, knows_alice):
    verdict = sync.ingest_artifact(
        bob.session, {"artifact_type": "plan.version"}, None, knows_alice
    )
    assert not verdict
    assert "Malformed artifact envelope" in verdict.reason
