"""The suite every mesh adapter must pass (PRD §26.2).

This is what makes provider portability checkable instead of aspirational.
It is written only against the protocols in ``flanner.mesh``, so nothing in
it can accidentally depend on how one vendor happens to work.

Shipped inside the package rather than kept with flanner's own tests, so
that anyone writing an adapter - for a provider we have never heard of, or
for their own network - can certify it against the same suite we hold
ourselves to. A portability claim nobody outside can run is a slogan.

Importing this needs pytest, which is a test dependency. That is deliberate:
nothing in flanner imports this module, only test suites do.

To certify an adapter, subclass :class:`MeshControlConformance` and supply
the two factories:

    from flanner.mesh_conformance import MeshControlConformance

    class TestNetBirdConformance(MeshControlConformance):
        def make_provider(self, **kw):
            return NetBirdControlProvider(api=stub_api(**kw))

        def make_runtime(self, control, device_id):
            return NetBirdRuntimeProvider(control, device_id)

``make_provider`` must honour ``unavailable`` and ``rate_limit_after`` so
the outage and throttling paths are exercised too; an adapter that only
works on a good day is not portable.
"""

from __future__ import annotations

import pytest

from flanner.exceptions import MeshError, MeshUnavailableError
from flanner.mesh import DesiredNetworkState, DeviceClaim, MeshPolicy

ORG = "org_conformance"


class MeshControlConformance:
    """Behaviour required of every control-plane adapter."""

    # --- factories a concrete adapter supplies -----------------------------

    def make_provider(self, **kwargs: object) -> object:
        raise NotImplementedError

    def make_runtime(self, control: object, device_id: str) -> object:
        raise NotImplementedError

    @pytest.fixture
    def control(self):
        return self.make_provider()

    @pytest.fixture
    def network(self, control):
        return control.create_network(ORG)

    def _join(self, control, network_id: str, device_id: str):
        """Enroll a device the way a real one would: credential then redeem."""
        enrollment = control.create_enrollment(
            network_id, DeviceClaim(device_id=device_id, owner_user_id="user_1")
        )
        runtime = self.make_runtime(control, device_id)
        runtime.enroll(enrollment)
        return runtime, enrollment

    # --- networks ----------------------------------------------------------

    def test_creates_a_usable_network(self, control):
        network = control.create_network(ORG)
        assert network.network_id
        assert network.provider
        assert control.list_devices(network.network_id) == []

    def test_networks_are_distinct(self, control):
        first = control.create_network(ORG)
        second = control.create_network(ORG)
        assert first.network_id != second.network_id

    def test_deletes_a_network(self, control, network):
        control.delete_network(network.network_id)
        with pytest.raises(MeshError):
            control.list_devices(network.network_id)

    def test_deleting_twice_is_not_an_error(self, control, network):
        """Reconciliation retries; a second delete must be a no-op."""
        control.delete_network(network.network_id)
        control.delete_network(network.network_id)

    def test_unknown_network_is_an_error(self, control):
        with pytest.raises(MeshError):
            control.list_devices("net_does_not_exist")

    # --- enrollment --------------------------------------------------------

    def test_enrollment_is_short_lived_and_single_use(self, control, network):
        enrollment = control.create_enrollment(
            network.network_id, DeviceClaim(device_id="dev_a", owner_user_id="user_1")
        )
        assert enrollment.credential
        assert enrollment.device_id == "dev_a"
        assert enrollment.single_use is True

    def test_a_credential_cannot_be_redeemed_twice(self, control, network):
        """A leaked join credential must not admit a second device."""
        _, enrollment = self._join(control, network.network_id, "dev_a")
        intruder = self.make_runtime(control, "dev_intruder")
        with pytest.raises(MeshError):
            intruder.enroll(enrollment)

    def test_each_device_gets_its_own_credential(self, control, network):
        first = control.create_enrollment(
            network.network_id, DeviceClaim(device_id="dev_a", owner_user_id="u")
        )
        second = control.create_enrollment(
            network.network_id, DeviceClaim(device_id="dev_b", owner_user_id="u")
        )
        assert first.credential != second.credential

    # --- membership --------------------------------------------------------

    def test_an_enrolled_device_is_listed(self, control, network):
        self._join(control, network.network_id, "dev_a")
        assert [d.device_id for d in control.list_devices(network.network_id)] == ["dev_a"]

    def test_revoking_removes_a_device(self, control, network):
        self._join(control, network.network_id, "dev_a")
        control.revoke_device(network.network_id, "dev_a")
        assert control.list_devices(network.network_id) == []

    def test_revoking_an_unknown_device_is_not_an_error(self, control, network):
        """Revocation is retried after failures, so it must be idempotent."""
        control.revoke_device(network.network_id, "dev_never_joined")

    # --- policy ------------------------------------------------------------

    def test_applies_a_least_privilege_policy(self, control, network):
        self._join(control, network.network_id, "dev_a")
        control.apply_policy(
            network.network_id,
            MeshPolicy(allowed_device_ids=frozenset({"dev_a"}), application_port=51820),
        )

    # --- reconciliation ----------------------------------------------------

    def test_reconcile_reports_no_drift_when_in_sync(self, control, network):
        self._join(control, network.network_id, "dev_a")
        result = control.reconcile(
            DesiredNetworkState(network_id=network.network_id, device_ids=frozenset({"dev_a"}))
        )
        assert not result.drifted
        assert result.unchanged == ("dev_a",)

    def test_reconcile_removes_a_device_flanner_no_longer_authorizes(self, control, network):
        """Flanner's membership is authoritative; provider drift is corrected."""
        self._join(control, network.network_id, "dev_a")
        self._join(control, network.network_id, "dev_stale")

        result = control.reconcile(
            DesiredNetworkState(network_id=network.network_id, device_ids=frozenset({"dev_a"}))
        )
        assert result.drifted
        assert "dev_stale" in result.removed
        assert [d.device_id for d in control.list_devices(network.network_id)] == ["dev_a"]

    def test_reconcile_is_idempotent(self, control, network):
        self._join(control, network.network_id, "dev_a")
        desired = DesiredNetworkState(
            network_id=network.network_id, device_ids=frozenset({"dev_a"})
        )
        control.reconcile(desired)
        again = control.reconcile(desired)
        assert not again.drifted

    # --- degraded providers (PRD §24: fail closed, never lose local state) --

    def test_outage_surfaces_as_mesh_unavailable(self):
        down = self.make_provider(unavailable=True)
        with pytest.raises(MeshUnavailableError):
            down.create_network(ORG)

    def test_health_reports_an_outage_rather_than_raising(self):
        """Health is what callers consult *because* things may be down."""
        down = self.make_provider(unavailable=True)
        health = down.get_health("net_anything")
        assert health.reachable is False

    def test_health_is_positive_when_the_provider_is_up(self, control, network):
        assert control.get_health(network.network_id).reachable is True

    def test_rate_limiting_surfaces_as_mesh_unavailable(self):
        """Throttling reads as an outage, whatever the provider's call budget.

        Deliberately makes no assumption about how many API calls one
        operation costs. An earlier version asserted the first
        create_network succeeded and the second failed, which quietly
        required every provider to admit a device in exactly one request:
        the second adapter written against this suite needed two, and
        failed a portability test for being unlike the first rather than
        for being wrong.
        """
        throttled = self.make_provider(rate_limit_after=1)
        with pytest.raises(MeshUnavailableError):
            for _ in range(20):
                throttled.create_network(ORG)
