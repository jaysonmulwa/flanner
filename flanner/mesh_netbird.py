"""Driving the NetBird client on this device (PRD §10.4).

The device half of the mesh seam. It lives in the client package because it
runs where the client runs: on a laptop, wrapping the `netbird` binary
installed there. The control plane never runs this and could not, having no
device to enrol.

**Connectivity only, never identity.** NetBird can say a peer is reachable
at an address. It cannot say which flanner device that is, and this module
deliberately does not pretend otherwise. Every peer it reports carries an
address and an empty device id, because the only thing that establishes who
a peer is is the signed handshake in :mod:`flanner.peer`: a public key that
hashes to the device id claimed, plus an entitlement naming that same id.
Taking NetBird's word for identity would mean trusting the network for the
one thing the design refuses to trust it with.

So what this buys is addresses. Two laptops behind different routers get a
path to each other, and the peer protocol decides the rest.

**Verification status.** The command shapes and the JSON field names come
from NetBird's CLI documentation and the struct definitions in their source,
not from memory. They have not been run against an installed client. The
parsing is deliberately forgiving: a field that moved should degrade to
"unknown" rather than raise, because a status page is not worth a crash.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .exceptions import MeshError, MeshUnavailableError
from .mesh import DIRECT, OFFLINE, RELAY, Enrollment, LocalMeshStatus, MeshPeer

BINARY = "netbird"
DEFAULT_TIMEOUT = 30.0

# What NetBird calls a working peer, and how its connection types map onto
# ours. Anything unrecognised is reported as offline rather than guessed at.
_CONNECTED = "connected"
_CONNECTION = {"P2P": DIRECT, "Relayed": RELAY}

Runner = Callable[[Sequence[str], float], "subprocess.CompletedProcess[str]"]


def _run(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed binary name, arguments are ours
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@dataclass
class NetBirdRuntime:
    """The `netbird` client on this machine.

    ``runner`` is injected so the whole wrapper is testable without the
    binary installed, which is the only way it could be tested at all here.
    """

    management_url: str = ""
    binary: str = BINARY
    timeout: float = DEFAULT_TIMEOUT
    runner: Runner = field(default=_run)

    # --- talking to the binary ------------------------------------------------

    def installed(self) -> bool:
        """Whether the client is on this machine at all.

        Absent is an ordinary state, not a fault: flanner works without a
        managed network, syncing over whatever path devices already share.
        """
        return shutil.which(self.binary) is not None

    def _netbird(self, *args: str) -> str:
        command = [self.binary, *args]
        try:
            result = self.runner(command, self.timeout)
        except FileNotFoundError:
            raise MeshUnavailableError(
                f"the {self.binary} client is not installed on this machine"
            ) from None
        except subprocess.TimeoutExpired:
            raise MeshUnavailableError(
                f"{self.binary} {args[0] if args else ''} did not finish in "
                f"{self.timeout:.0f}s"
            ) from None
        except OSError as e:
            raise MeshUnavailableError(f"could not run {self.binary}: {e}") from None

        if result.returncode != 0:
            # stderr first: their CLI puts the useful sentence there, and a
            # message that only says "exit 1" sends someone to the wrong place.
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise MeshError(
                f"{self.binary} {' '.join(args)} failed: "
                f"{detail[-1] if detail else f'exit {result.returncode}'}"
            )
        return result.stdout

    def _status_json(self) -> dict[str, Any]:
        """The client's own view, parsed.

        Returns an empty view rather than raising when the client is present
        but not up, because "not connected" is a status and not an error.
        """
        try:
            raw = self._netbird("status", "--json")
        except MeshError:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            # A format change should degrade to "unknown", not crash a
            # status command someone ran to find out what was wrong.
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # --- the runtime seam -------------------------------------------------------

    def status(self) -> LocalMeshStatus:
        """Whether this device is on the network, and where it is."""
        if not self.installed():
            return LocalMeshStatus(
                enrolled=False,
                message=f"the {self.binary} client is not installed",
            )

        view = self._status_json()
        management = view.get("management") or {}
        connected = bool(management.get("connected"))
        address = str(view.get("netbirdIp") or "")

        return LocalMeshStatus(
            enrolled=connected,
            network_id=str(management.get("url") or "") or None,
            endpoints=(address,) if address else (),
            message=str(management.get("error") or "")
            or ("" if connected else "not connected to a managed network"),
        )

    def enroll(self, credential: Enrollment) -> None:
        """Join the network with a one-off setup key."""
        args = ["up", "--setup-key", credential.credential]
        if self.management_url:
            args += ["--management-url", self.management_url]
        self._netbird(*args)

    def leave(self) -> None:
        """Disconnect. Idempotent: already down is not a failure."""
        try:
            self._netbird("down")
        except MeshError:
            # `down` on a client that is already down exits non-zero on some
            # versions, and "it is down" is the outcome either way.
            pass

    def private_endpoints(self) -> list[str]:
        """This device's addresses on the managed network."""
        view = self._status_json()
        return [str(view["netbirdIp"])] if view.get("netbirdIp") else []

    def peers(self) -> list[MeshPeer]:
        """Reachable peers, as addresses rather than identities.

        ``device_id`` is empty on purpose. NetBird knows a public key and a
        hostname, neither of which is a flanner device id, and inventing a
        mapping would put the network in charge of identity. The signed
        handshake decides who is on the other end; this only says where to
        knock.
        """
        details = ((self._status_json().get("peers") or {}).get("details")) or []
        found = []
        for peer in details:
            if not isinstance(peer, dict):
                continue
            address = str(peer.get("netbirdIp") or "")
            if not address:
                continue
            found.append(
                MeshPeer(
                    device_id="",
                    endpoint=address,
                    connection=_connection_of(peer),
                )
            )
        return found

    def connection_type(self, device_id: str) -> str:
        """Diagnostics only, and only for an address.

        The seam names this parameter after a flanner device, but the client
        cannot resolve one: it sees public keys and hostnames. Callers pass
        an address, and anything unrecognised comes back offline. The
        application protocol behaves identically either way, which is why
        this is allowed to be approximate (§10, DIRECT/RELAY).
        """
        for peer in self.peers():
            if peer.endpoint == device_id:
                return peer.connection
        return OFFLINE


def _connection_of(peer: dict[str, Any]) -> str:
    if str(peer.get("status") or "").lower() != _CONNECTED:
        return OFFLINE
    return _CONNECTION.get(str(peer.get("connectionType") or ""), DIRECT)


def runtime(management_url: str = "") -> NetBirdRuntime:
    """The wrapper, pointed at whichever management service is in use."""
    return NetBirdRuntime(management_url=management_url)
