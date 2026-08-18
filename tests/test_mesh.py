"""The reference provider, certified against the portability suite (PRD §10)."""

from __future__ import annotations

import pytest

from flanner.exceptions import MeshError
from flanner.mesh import DIRECT, OFFLINE, DesiredNetworkState, DeviceClaim, MeshPolicy
from flanner.mesh_conformance import MeshControlConformance
from flanner.mesh_fake import FakeControlProvider, FakeRuntimeProvider


class TestFakeProviderConformance(MeshControlConformance):
    """The in-memory provider must pass exactly what NetBird will have to."""

    def make_provider(self, **kwargs):
        return FakeControlProvider(**kwargs)

    def make_runtime(self, control, device_id):
        return FakeRuntimeProvider(control, device_id)


# --- the device half ---------------------------------------------------------


@pytest.fixture
def control():
    return FakeControlProvider()


@pytest.fixture
def network(control):
    return control.create_network("org_1")


def join(control, network_id, device_id):
    enrollment = control.create_enrollment(
        network_id, DeviceClaim(device_id=device_id, owner_user_id="u")
    )
    runtime = FakeRuntimeProvider(control, device_id)
    runtime.enroll(enrollment)
    return runtime


def test_a_device_starts_outside_any_network(control):
    status = FakeRuntimeProvider(control, "dev_a").status()
    assert status.enrolled is False
    assert status.network_id is None
    assert "not enrolled" in status.message


def test_enrolling_gives_the_device_an_endpoint(control, network):
    runtime = join(control, network.network_id, "dev_a")
    status = runtime.status()
    assert status.enrolled is True
    assert status.network_id == network.network_id
    assert status.endpoints and runtime.private_endpoints() == list(status.endpoints)


def test_a_device_sees_its_peers_but_not_itself(control, network):
    alice = join(control, network.network_id, "dev_alice")
    join(control, network.network_id, "dev_bob")
    assert [p.device_id for p in alice.peers()] == ["dev_bob"]


def test_connection_type_is_reported_for_diagnostics(control, network):
    alice = join(control, network.network_id, "dev_alice")
    join(control, network.network_id, "dev_bob")
    assert alice.connection_type("dev_bob") == DIRECT
    assert alice.connection_type("dev_nobody") == OFFLINE


def test_leaving_removes_the_device_from_the_network(control, network):
    alice = join(control, network.network_id, "dev_alice")
    alice.leave()
    assert alice.status().enrolled is False
    assert control.list_devices(network.network_id) == []


def test_leaving_twice_is_harmless(control, network):
    alice = join(control, network.network_id, "dev_alice")
    alice.leave()
    alice.leave()


def test_an_unenrolled_device_has_no_peers_or_endpoints(control):
    runtime = FakeRuntimeProvider(control, "dev_a")
    assert runtime.peers() == []
    assert runtime.private_endpoints() == []


def test_an_unknown_credential_is_refused(control, network):
    from datetime import datetime, timezone

    from flanner.mesh import Enrollment

    forged = Enrollment(
        credential="enr_forged",
        device_id="dev_intruder",
        expires_at=datetime.now(timezone.utc),
    )
    with pytest.raises(MeshError):
        FakeRuntimeProvider(control, "dev_intruder").enroll(forged)


# --- policy read-back (beyond the protocol, so it lives here) ----------------


def test_policy_is_stored_as_applied(control, network):
    policy = MeshPolicy(allowed_device_ids=frozenset({"dev_a"}), application_port=51820)
    control.apply_policy(network.network_id, policy)
    assert control.policy_for(network.network_id) == policy


def test_reconcile_carries_the_policy_through(control, network):
    join(control, network.network_id, "dev_a")
    policy = MeshPolicy(allowed_device_ids=frozenset({"dev_a"}), application_port=41641)
    control.reconcile(
        DesiredNetworkState(
            network_id=network.network_id, device_ids=frozenset({"dev_a"}), policy=policy
        )
    )
    assert control.policy_for(network.network_id) == policy


def test_the_default_policy_admits_nobody(control, network):
    """Least privilege by construction: access is granted, never assumed."""
    assert control.policy_for(network.network_id).allowed_device_ids == frozenset()


# --- the seam itself ---------------------------------------------------------


def test_no_vendor_details_leak_into_the_domain_types():
    """PRD §10.1: provider concepts must not reach flanner's model.

    The provider name is data on a handle, not a type or field name, so an
    adapter swap cannot ripple outwards.
    """
    import dataclasses

    from flanner import mesh

    vendor_words = ("netbird", "headscale", "tailscale", "zerotier", "wireguard", "setup_key")
    for name in dir(mesh):
        obj = getattr(mesh, name)
        if not dataclasses.is_dataclass(obj):
            continue
        assert not any(word in name.lower() for word in vendor_words)
        for field in dataclasses.fields(obj):
            assert not any(
                word in field.name.lower() for word in vendor_words
            ), f"{name}.{field.name} names a specific provider"
