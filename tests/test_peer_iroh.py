"""Peer sync carried over iroh instead of a reachable http address.

Most of this needs no network: the framing, the refusal shapes and the
choice of transport are all decidable locally. One test is different and
deliberately so. ``test_a_plan_crosses_between_two_devices_over_iroh``
dials the real network, because the entire reason this module exists is a
claim about reachability, and a fake cannot disagree with that claim.

That test skips when there is no route out, rather than failing. An
offline laptop is not a broken build.
"""

import asyncio
import base64
import threading

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flanner import peer, peer_iroh
from tests.test_peer import WORKSPACE, Device, a_stored_artifact, link

# Defined here rather than imported. Importing a fixture by name binds it as
# a module global and every test that takes it as an argument then reads as
# a redefinition, which buries real warnings under noise.


@pytest.fixture
def issuer_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def alice(tmp_path, issuer_key):
    return Device(tmp_path / "alice", issuer_key)


@pytest.fixture
def bob(tmp_path, issuer_key):
    return Device(tmp_path / "bob", issuer_key)


class _FakeStream:
    """Enough of an iroh stream to exercise the framing."""

    def __init__(self, data=b""):
        self.buffer = bytearray(data)
        self.sent = bytearray()
        self.finished = False

    async def write_all(self, data):
        self.sent.extend(data)

    async def finish(self):
        self.finished = True

    async def read_exact(self, size):
        if len(self.buffer) < size:
            raise EOFError("stream ended")
        out = bytes(self.buffer[:size])
        del self.buffer[:size]
        return out


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- framing


def test_a_frame_survives_a_round_trip():
    stream = _FakeStream()
    run(peer_iroh._write(stream, {"op": "manifest", "n": 1}))
    assert stream.finished
    assert run(peer_iroh._read(_FakeStream(stream.sent))) == {"op": "manifest", "n": 1}


def test_an_oversized_length_prefix_is_refused_before_allocating():
    """The prefix comes from a stranger, so it is checked, not believed."""
    header = peer_iroh._HEADER.pack(peer_iroh.MAX_FRAME + 1)
    with pytest.raises(peer.PeerError) as caught:
        run(peer_iroh._read(_FakeStream(header + b"x")))
    assert caught.value.status == 413


def test_a_frame_that_is_not_an_object_is_refused():
    body = b"[1, 2, 3]"
    with pytest.raises(peer.PeerError):
        run(peer_iroh._read(_FakeStream(peer_iroh._HEADER.pack(len(body)) + body)))


# --------------------------------------------------------------- dispatch


def test_a_refusal_carries_its_status_into_the_reply():
    """The same refusal must read the same over either transport."""
    reply = peer_iroh._dispatch(
        {"op": "manifest", "request": {"body": {"workspace_id": WORKSPACE}}},
        sessions=None,
        held=lambda: None,
    )
    assert reply["ok"] is False
    assert reply["status"] == 503
    assert "not part of a team" in reply["error"]


def test_a_request_with_no_signed_payload_is_refused():
    reply = peer_iroh._dispatch({"op": "manifest"}, sessions=None, held=lambda: None)
    assert reply == {"ok": False, "status": 400, "error": "no signed request"}


def test_an_unknown_operation_is_refused_by_the_shared_handler():
    """Not by this module: the transport decides nothing about access."""
    reply = peer_iroh._dispatch(
        {"op": "delete_everything", "request": {"body": {"workspace_id": WORKSPACE}}},
        sessions=None,
        held=lambda: None,
    )
    assert reply["ok"] is False


# --------------------------------------------------------------- dialling


def test_a_device_absent_from_the_keyring_cannot_be_dialled(alice, bob):
    """A revoked or unknown device is named as such, not left to time out."""
    alice.sign_in(device_keys={alice.device_id: alice.public_key})
    with pytest.raises(peer.PeerError) as caught:
        peer_iroh.endpoint_id_for(bob.device_id, alice.held)
    assert caught.value.status == 404
    assert bob.device_id in str(caught.value)


def test_dialling_without_a_session_is_refused(alice, bob):
    with pytest.raises(peer.PeerError):
        peer_iroh.endpoint_id_for(bob.device_id, lambda: None)


def test_the_dialable_id_is_the_devices_own_public_key(alice, bob):
    """No second identity: the id dialled is the key signatures are checked against."""
    link(alice, bob)
    resolved = peer_iroh.endpoint_id_for(bob.device_id, alice.held)
    assert resolved == base64.b64decode(bob.public_key).hex()


def test_a_device_id_routes_over_iroh_and_an_address_over_http(alice, bob, monkeypatch):
    link(alice, bob)
    assert peer_iroh.is_device_id(bob.device_id)
    assert not peer_iroh.is_device_id("http://127.0.0.1:8776")

    built = {}
    monkeypatch.setattr(peer_iroh, "transport", lambda d, h, **kw: built.setdefault("device", d))
    with alice.active():
        over_http = peer_iroh.peer_for("http://127.0.0.1:8776", WORKSPACE, alice.held)
    assert "device" not in built
    assert isinstance(over_http, peer.RemotePeer)

    with alice.active():
        peer_iroh.peer_for(bob.device_id, WORKSPACE, alice.held)
    assert built["device"] == bob.device_id


# ------------------------------------------------------------- the network


@pytest.fixture
def endpoints():
    """Real iroh endpoints, closed when the test ends."""
    made = []

    def start(device):
        with device.active():
            endpoint = peer_iroh.PeerEndpoint()
            try:
                endpoint.ready(timeout=60)
            except Exception as exc:  # pragma: no cover - depends on the network
                pytest.skip(f"no iroh endpoint available here: {exc}")
        made.append(endpoint)
        return endpoint

    yield start

    for endpoint in made:
        try:
            endpoint.close()
        except Exception:  # noqa: S110 - teardown is best effort
            pass


def test_a_plan_crosses_between_two_devices_over_iroh(alice, bob, endpoints):
    """The claim this module exists for: no address, no port, no admin rights.

    Alice never learns Bob's address and Bob opens no listening port. The
    only thing Alice is given is Bob's device id.
    """
    link(alice, bob)
    a_stored_artifact(bob.session, key=bob.signing_key())

    serving = endpoints(bob)
    threading.Thread(target=lambda: serving.serve(bob.sessions, bob.held), daemon=True).start()

    dialing = endpoints(alice)
    with alice.active():
        try:
            carry = peer_iroh.transport(bob.device_id, alice.held, endpoint=dialing, timeout=60)
            remote = peer.RemotePeer(bob.device_id, WORKSPACE, alice.held, transport=carry)
            manifest = remote.manifest(WORKSPACE)
        except peer.PeerError as exc:  # pragma: no cover - depends on the network
            pytest.skip(f"no route between two iroh endpoints here: {exc}")

        assert manifest.workspace_id == WORKSPACE
        assert manifest.artifact_ids, "Bob served an empty manifest over iroh"
        wanted = sorted(manifest.artifact_ids)
        fetched = remote.fetch(wanted)

    assert sorted(envelope["artifact_id"] for envelope, _ in fetched) == wanted
