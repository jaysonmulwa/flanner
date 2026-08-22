"""Provider-neutral private-network abstraction (PRD §10).

Flanner needs peers to be able to reach each other privately. It does not
need to know *how*, and must never learn: a provider supplies connectivity,
while flanner owns identity, authorization, and the artifact protocol.

Everything here is deliberately vendor-free. No account ids, setup keys,
groups, routes, or policy documents from any particular provider appear in
these types, so swapping NetBird for Headscale, ZeroTier, or a customer's
own network changes an adapter and nothing else (§10.1).

Two seams, because the work happens in two places:

- :class:`MeshControlProvider` runs in the hosted control plane, where the
  provider's admin credentials live. It creates networks, issues enrollment
  credentials, revokes devices, and reconciles drift.
- :class:`MeshRuntimeProvider` runs on the device, where the provider's
  client runs. It enrolls, reports peers, and says whether a connection is
  direct or relayed.

Neither ever authorizes a flanner artifact. Mesh membership means a packet
can arrive; a signed device certificate and workspace capability decide
whether it is honoured (§8.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

# How a peer is currently reachable. Reported for diagnostics only: the
# application protocol behaves identically either way.
DIRECT = "direct"
RELAY = "relay"
OFFLINE = "offline"


@dataclass(frozen=True)
class ProviderNetwork:
    """One organization's private network, as the provider sees it."""

    network_id: str
    provider: str


@dataclass(frozen=True)
class DeviceClaim:
    """A device flanner is asking the provider to admit."""

    device_id: str
    owner_user_id: str
    label: str = ""


@dataclass(frozen=True)
class Enrollment:
    """A short-lived credential for exactly one device to join.

    Single-use and expiring by construction: a leaked long-lived join key
    would let anyone onto a customer's private network (§10.4).
    """

    credential: str
    device_id: str
    expires_at: datetime
    single_use: bool = True


@dataclass(frozen=True)
class MeshPolicy:
    """Least privilege: which devices may reach flanner's port, and only that.

    Joining a flanner workspace must never hand a device broad access to a
    customer's private network (§10.4).
    """

    allowed_device_ids: frozenset[str] = frozenset()
    application_port: int = 51820


@dataclass(frozen=True)
class ProviderDevice:
    """A device the provider currently admits."""

    provider_device_id: str
    device_id: str
    connected: bool = False
    last_seen: datetime | None = None


@dataclass(frozen=True)
class ProviderHealth:
    """Whether the provider itself is usable right now."""

    reachable: bool
    relay_available: bool = True
    message: str = ""


@dataclass(frozen=True)
class DesiredNetworkState:
    """What flanner believes the network should contain.

    Reconciliation compares this with the provider's actual membership;
    flanner's view is authoritative, so a device removed here is removed
    there (§10.4).
    """

    network_id: str
    device_ids: frozenset[str] = frozenset()
    policy: MeshPolicy = field(default_factory=MeshPolicy)


@dataclass(frozen=True)
class ReconcileResult:
    """What reconciliation changed at the provider."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()

    @property
    def drifted(self) -> bool:
        return bool(self.added or self.removed)


@dataclass(frozen=True)
class MeshPeer:
    """Another device reachable on the private network."""

    device_id: str
    endpoint: str
    connection: str = OFFLINE


@dataclass(frozen=True)
class LocalMeshStatus:
    """This device's own view of the network."""

    enrolled: bool
    network_id: str | None = None
    endpoints: tuple[str, ...] = ()
    message: str = ""


class MeshControlProvider(Protocol):
    """The hosted half: administering a customer's network.

    Implementations hold the provider's admin credentials and therefore
    live only in the control plane, never on a customer device (§10.4).
    """

    def create_network(self, organization_id: str) -> ProviderNetwork: ...

    def delete_network(self, network_id: str) -> None: ...

    def create_enrollment(self, network_id: str, device: DeviceClaim) -> Enrollment: ...

    def revoke_device(self, network_id: str, device_id: str) -> None: ...

    def list_devices(self, network_id: str) -> list[ProviderDevice]: ...

    def apply_policy(self, network_id: str, policy: MeshPolicy) -> None: ...

    def get_health(self, network_id: str) -> ProviderHealth: ...

    def reconcile(self, desired: DesiredNetworkState) -> ReconcileResult: ...


class MeshRuntimeProvider(Protocol):
    """The device half: joining and reporting on the network."""

    def status(self) -> LocalMeshStatus: ...

    def enroll(self, credential: Enrollment) -> None: ...

    def leave(self) -> None: ...

    def private_endpoints(self) -> list[str]: ...

    def peers(self) -> list[MeshPeer]: ...

    def connection_type(self, device_id: str) -> str: ...
