"""In-memory reference implementation of the mesh provider seam.

Two jobs. It is the harness that lets everything above the provider
boundary be tested without a real network (PRD §33, criterion 14), and it
is the worked example an adapter author reads to see what the protocols
actually require.

It also fails on purpose. Adapters must behave sanely when a provider is
down or rate-limiting, so the fake can be told to do both, and the
conformance suite exercises those paths against every adapter (§26.2).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .exceptions import MeshError, MeshUnavailableError
from .mesh import (
    DIRECT,
    OFFLINE,
    DesiredNetworkState,
    DeviceClaim,
    Enrollment,
    LocalMeshStatus,
    MeshPeer,
    MeshPolicy,
    ProviderDevice,
    ProviderHealth,
    ProviderNetwork,
    ReconcileResult,
)

PROVIDER_NAME = "fake"
ENROLLMENT_TTL = timedelta(minutes=15)


@dataclass
class _Network:
    network_id: str
    organization_id: str
    devices: dict[str, ProviderDevice] = field(default_factory=dict)
    policy: MeshPolicy = field(default_factory=MeshPolicy)
    issued: dict[str, str] = field(default_factory=dict)  # credential -> device id
    spent: set[str] = field(default_factory=set)


class FakeControlProvider:
    """A provider that lives entirely in memory.

    ``unavailable`` simulates the control plane being down, and
    ``rate_limit_after`` starts refusing once that many calls have been
    made, so callers can be tested against both without a real outage.
    """

    def __init__(self, *, unavailable: bool = False, rate_limit_after: int | None = None) -> None:
        self.unavailable = unavailable
        self.rate_limit_after = rate_limit_after
        self.calls = 0
        self.networks: dict[str, _Network] = {}

    def _check(self) -> None:
        self.calls += 1
        if self.unavailable:
            raise MeshUnavailableError("provider control plane is unreachable")
        if self.rate_limit_after is not None and self.calls > self.rate_limit_after:
            raise MeshUnavailableError("provider rate limit exceeded")

    def _network(self, network_id: str) -> _Network:
        network = self.networks.get(network_id)
        if network is None:
            raise MeshError(f"unknown network {network_id}")
        return network

    def create_network(self, organization_id: str) -> ProviderNetwork:
        self._check()
        network_id = f"net_{secrets.token_hex(6)}"
        self.networks[network_id] = _Network(network_id, organization_id)
        return ProviderNetwork(network_id=network_id, provider=PROVIDER_NAME)

    def delete_network(self, network_id: str) -> None:
        self._check()
        self.networks.pop(network_id, None)  # deleting twice is not an error

    def create_enrollment(self, network_id: str, device: DeviceClaim) -> Enrollment:
        self._check()
        network = self._network(network_id)
        credential = f"enr_{secrets.token_hex(8)}"
        network.issued[credential] = device.device_id
        return Enrollment(
            credential=credential,
            device_id=device.device_id,
            expires_at=datetime.now(timezone.utc) + ENROLLMENT_TTL,
        )

    def redeem(self, network_id: str, credential: str) -> ProviderDevice:
        """Join a device using a credential. Refuses a second use."""
        self._check()
        network = self._network(network_id)
        if credential in network.spent:
            raise MeshError("enrollment credential has already been used")
        device_id = network.issued.get(credential)
        if device_id is None:
            raise MeshError("unknown enrollment credential")
        network.spent.add(credential)
        device = ProviderDevice(
            provider_device_id=f"pd_{secrets.token_hex(4)}",
            device_id=device_id,
            connected=True,
            last_seen=datetime.now(timezone.utc),
        )
        network.devices[device_id] = device
        return device

    def revoke_device(self, network_id: str, device_id: str) -> None:
        self._check()
        self._network(network_id).devices.pop(device_id, None)

    def list_devices(self, network_id: str) -> list[ProviderDevice]:
        self._check()
        return list(self._network(network_id).devices.values())

    def apply_policy(self, network_id: str, policy: MeshPolicy) -> None:
        self._check()
        self._network(network_id).policy = policy

    def get_health(self, network_id: str) -> ProviderHealth:
        # Health is what callers consult *because* things may be down, so it
        # reports an outage rather than raising one.
        self.calls += 1
        if self.unavailable:
            return ProviderHealth(reachable=False, relay_available=False, message="unreachable")
        return ProviderHealth(reachable=True)

    def reconcile(self, desired: DesiredNetworkState) -> ReconcileResult:
        self._check()
        network = self._network(desired.network_id)
        actual = set(network.devices)
        wanted = set(desired.device_ids)

        removed = tuple(sorted(actual - wanted))
        for device_id in removed:
            network.devices.pop(device_id, None)
        # Flanner's desired state is authoritative for removal, but a device
        # it has never enrolled cannot be conjured here: joining requires
        # redeeming a credential from the device itself.
        added = tuple(sorted(wanted - actual))
        network.policy = desired.policy
        return ReconcileResult(
            added=added, removed=removed, unchanged=tuple(sorted(actual & wanted))
        )

    def policy_for(self, network_id: str) -> MeshPolicy:
        """Read back the applied policy, so tests need not touch internals."""
        return self._network(network_id).policy


class FakeRuntimeProvider:
    """The device half, in memory. Pairs with :class:`FakeControlProvider`."""

    def __init__(self, control: FakeControlProvider, device_id: str) -> None:
        self._control = control
        self.device_id = device_id
        self._network_id: str | None = None

    def status(self) -> LocalMeshStatus:
        if self._network_id is None:
            return LocalMeshStatus(enrolled=False, message="not enrolled in a network")
        return LocalMeshStatus(
            enrolled=True,
            network_id=self._network_id,
            endpoints=tuple(self.private_endpoints()),
        )

    def enroll(self, credential: Enrollment) -> None:
        for network_id, network in self._control.networks.items():
            if credential.credential in network.issued:
                self._control.redeem(network_id, credential.credential)
                self._network_id = network_id
                return
        raise MeshError("no network accepts this credential")

    def leave(self) -> None:
        if self._network_id is not None:
            self._control.revoke_device(self._network_id, self.device_id)
            self._network_id = None

    def private_endpoints(self) -> list[str]:
        if self._network_id is None:
            return []
        return [f"fake://{self._network_id}/{self.device_id}"]

    def peers(self) -> list[MeshPeer]:
        if self._network_id is None:
            return []
        return [
            MeshPeer(
                device_id=device.device_id,
                endpoint=f"fake://{self._network_id}/{device.device_id}",
                connection=DIRECT if device.connected else OFFLINE,
            )
            for device in self._control.list_devices(self._network_id)
            if device.device_id != self.device_id
        ]

    def connection_type(self, device_id: str) -> str:
        for peer in self.peers():
            if peer.device_id == device_id:
                return peer.connection
        return OFFLINE
