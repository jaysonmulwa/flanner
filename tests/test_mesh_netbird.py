"""The device half of the mesh seam.

Every fixture below uses the field names from NetBird's own status structs
rather than names invented here, which is the difference between testing the
parser and testing my memory of their API. It still cannot prove an
installed client emits exactly this; only a machine with one does that.

The property worth most attention is the one that is a design decision
rather than a parsing detail: this reports where a peer is, never who it is.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from flanner.exceptions import MeshError, MeshUnavailableError
from flanner.mesh import DIRECT, OFFLINE, RELAY, Enrollment
from flanner.mesh_netbird import NetBirdRuntime

# A credential that has not expired, which is the ordinary case.
SOON = datetime.now(timezone.utc) + timedelta(hours=1)


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["netbird"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# Shaped from netbirdio/netbird client/status/status.go: OutputOverview and
# PeerStateDetailOutput, so the tags are theirs rather than mine.
CONNECTED = """
{
  "peers": {
    "total": 2,
    "connected": 1,
    "details": [
      {
        "fqdn": "rajs-laptop.netbird.cloud",
        "netbirdIp": "100.64.0.2",
        "publicKey": "abc=",
        "status": "Connected",
        "connectionType": "P2P",
        "lastStatusUpdate": "2026-08-19T10:00:00Z"
      },
      {
        "fqdn": "build-box.netbird.cloud",
        "netbirdIp": "100.64.0.3",
        "publicKey": "def=",
        "status": "Connected",
        "connectionType": "Relayed",
        "relayAddress": "relay.netbird.io:33080"
      }
    ]
  },
  "management": {"url": "https://api.netbird.io:443", "connected": true, "error": ""},
  "signal": {"url": "https://signal.netbird.io:443", "connected": true, "error": ""},
  "netbirdIp": "100.64.0.1",
  "publicKey": "mine=",
  "fqdn": "marias-laptop.netbird.cloud",
  "cliVersion": "0.30.0",
  "daemonVersion": "0.30.0"
}
"""

DISCONNECTED = """
{
  "peers": {"total": 0, "connected": 0, "details": []},
  "management": {"url": "https://api.netbird.io:443", "connected": false,
                 "error": "context deadline exceeded"},
  "signal": {"url": "", "connected": false, "error": ""},
  "netbirdIp": "",
  "publicKey": ""
}
"""


def a_runtime(monkeypatch, *, output=CONNECTED, installed=True, **kw):
    calls: list[list[str]] = []

    def runner(command, timeout):
        calls.append(list(command))
        if isinstance(output, Exception):
            raise output
        return completed(output) if not isinstance(output, tuple) else completed(*output)

    monkeypatch.setattr(
        "flanner.mesh_netbird.shutil.which", lambda _: "/usr/bin/netbird" if installed else None
    )
    runtime = NetBirdRuntime(runner=runner, **kw)
    return runtime, calls


# --- reading the client's view --------------------------------------------------


def test_a_connected_device_reports_its_address(monkeypatch):
    runtime, _ = a_runtime(monkeypatch)
    status = runtime.status()

    assert status.enrolled is True
    assert status.endpoints == ("100.64.0.1",)
    assert status.network_id == "https://api.netbird.io:443"


def test_a_disconnected_device_says_so_without_failing(monkeypatch):
    """Not connected is a status, not an error."""
    runtime, _ = a_runtime(monkeypatch, output=DISCONNECTED)
    status = runtime.status()

    assert status.enrolled is False
    assert "context deadline exceeded" in status.message


def test_a_missing_client_is_an_ordinary_state(monkeypatch):
    """flanner works without a managed network, so this is not a fault."""
    runtime, _ = a_runtime(monkeypatch, installed=False)
    status = runtime.status()

    assert status.enrolled is False
    assert "not installed" in status.message


def test_private_endpoints_are_this_devices_addresses(monkeypatch):
    runtime, _ = a_runtime(monkeypatch)
    assert runtime.private_endpoints() == ["100.64.0.1"]


def test_a_client_that_is_not_up_reports_no_endpoints(monkeypatch):
    runtime, _ = a_runtime(monkeypatch, output=DISCONNECTED)
    assert runtime.private_endpoints() == []


# --- connectivity, never identity -------------------------------------------------


def test_peers_are_reported_as_addresses_with_no_device_id(monkeypatch):
    """The design decision, not an omission.

    NetBird knows a public key and a hostname. Neither is a flanner device
    id, and mapping one onto the other would put the network in charge of
    identity, which the signed handshake exists to prevent.
    """
    runtime, _ = a_runtime(monkeypatch)
    peers = runtime.peers()

    assert [p.endpoint for p in peers] == ["100.64.0.2", "100.64.0.3"]
    assert all(p.device_id == "" for p in peers)


def test_a_relayed_peer_is_distinguished_from_a_direct_one(monkeypatch):
    """Diagnostics only: the application protocol behaves the same either way."""
    runtime, _ = a_runtime(monkeypatch)
    peers = {p.endpoint: p.connection for p in runtime.peers()}

    assert peers["100.64.0.2"] == DIRECT
    assert peers["100.64.0.3"] == RELAY


def test_a_peer_that_is_not_connected_reads_as_offline(monkeypatch):
    payload = CONNECTED.replace(
        '"status": "Connected",\n        "connectionType": "P2P"',
        '"status": "Disconnected",\n        "connectionType": "P2P"',
    )
    runtime, _ = a_runtime(monkeypatch, output=payload)

    assert runtime.peers()[0].connection == OFFLINE


def test_an_unknown_connection_type_does_not_crash(monkeypatch):
    payload = CONNECTED.replace('"connectionType": "P2P"', '"connectionType": "Something New"')
    runtime, _ = a_runtime(monkeypatch, output=payload)

    assert runtime.peers()[0].connection == DIRECT


def test_connection_type_answers_for_an_address_and_shrugs_otherwise(monkeypatch):
    runtime, _ = a_runtime(monkeypatch)

    assert runtime.connection_type("100.64.0.3") == RELAY
    assert runtime.connection_type("dev_some_flanner_id") == OFFLINE


# --- driving it -------------------------------------------------------------------


def test_enrolling_passes_the_setup_key(monkeypatch):
    runtime, calls = a_runtime(monkeypatch)
    runtime.enroll(Enrollment(credential="SECRET-KEY", device_id="dev_a", expires_at=SOON))

    assert calls[0][:3] == ["netbird", "up", "--setup-key"]
    assert "SECRET-KEY" in calls[0]


def test_a_self_hosted_management_url_is_passed_through(monkeypatch):
    runtime, calls = a_runtime(monkeypatch, management_url="https://mesh.acme.test:443")
    runtime.enroll(Enrollment(credential="k", device_id="dev_a", expires_at=SOON))

    assert "--management-url" in calls[0]
    assert "https://mesh.acme.test:443" in calls[0]


def test_leaving_is_idempotent(monkeypatch):
    """Some versions exit non-zero when already down, and down is down."""
    runtime, _ = a_runtime(monkeypatch, output=("", 1, "not running"))
    runtime.leave()


def test_a_failing_command_reports_the_clients_own_message(monkeypatch):
    """An error that only says "exit 1" sends someone to the wrong place."""
    runtime, _ = a_runtime(monkeypatch, output=("", 1, "setup key is invalid or expired"))

    with pytest.raises(MeshError, match="setup key is invalid or expired"):
        runtime.enroll(Enrollment(credential="bad", device_id="dev_a", expires_at=SOON))


def test_a_missing_binary_is_reported_as_unavailable(monkeypatch):
    runtime, _ = a_runtime(monkeypatch, output=FileNotFoundError())

    with pytest.raises(MeshUnavailableError, match="not installed"):
        runtime.enroll(Enrollment(credential="k", device_id="dev_a", expires_at=SOON))


def test_a_hanging_client_does_not_hang_flanner(monkeypatch):
    runtime, _ = a_runtime(
        monkeypatch, output=subprocess.TimeoutExpired(cmd="netbird", timeout=30)
    )

    with pytest.raises(MeshUnavailableError, match="did not finish"):
        runtime.enroll(Enrollment(credential="k", device_id="dev_a", expires_at=SOON))


# --- surviving their next release ---------------------------------------------------


def test_unparseable_output_degrades_rather_than_raising(monkeypatch):
    """A status command someone ran to find out what is wrong must answer."""
    runtime, _ = a_runtime(monkeypatch, output="not json at all")

    assert runtime.status().enrolled is False
    assert runtime.peers() == []


def test_a_status_shaped_differently_degrades_too(monkeypatch):
    """If they rename a field, this reports unknown instead of crashing."""
    runtime, _ = a_runtime(monkeypatch, output='{"somethingElse": true}')

    assert runtime.status().enrolled is False
    assert runtime.private_endpoints() == []
    assert runtime.peers() == []


def test_a_peer_entry_without_an_address_is_skipped(monkeypatch):
    payload = '{"peers": {"details": [{"fqdn": "x"}, {"netbirdIp": "100.64.0.9"}]}}'
    runtime, _ = a_runtime(monkeypatch, output=payload)

    assert [p.endpoint for p in runtime.peers()] == ["100.64.0.9"]


def test_it_satisfies_the_runtime_seam():
    """Typed against the protocol, so a swap really is one class."""
    from flanner.mesh import MeshRuntimeProvider

    runtime: MeshRuntimeProvider = NetBirdRuntime()
    assert runtime is not None


def test_no_code_outside_the_wrapper_names_the_vendor():
    """The rule the control adapter is held to, stated precisely (§10.1).

    Code, not prose. `mesh` names NetBird in a docstring as an example of
    what a swap replaces, and the conformance suite names it in a worked
    example; both are the seam being explained rather than assumed.

    One file may name it in code: `cli`, which has to choose an
    implementation and is the client's composition root, exactly as
    `providers` is the control plane's. So the honest claim is "an adapter
    plus one line", which is what this checks rather than an unachievable
    zero.
    """
    import ast
    from pathlib import Path

    def code_mentions_vendor(source: str) -> bool:
        tree = ast.parse(source)

        # Docstrings by identity, not by value: ast.get_docstring cleans and
        # dedents what it returns, so comparing strings never matches and
        # every docstring reads as a literal.
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
                continue
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))

        for node in ast.walk(tree):
            if isinstance(node, ast.Name | ast.Attribute | ast.alias):
                if "netbird" in ast.dump(node).lower():
                    return True
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings and "netbird" in node.value.lower():
                    return True
        return False

    offenders = [
        path.name
        for path in Path("flanner").glob("*.py")
        if path.name not in {"mesh_netbird.py", "cli.py"}
        and code_mentions_vendor(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders


def test_the_composition_root_names_it_in_exactly_one_place():
    """Confined to one function, so the claim above survives the CLI growing.

    Counting mentions would be brittle. What matters is that choosing an
    implementation happens in a single named place, so a second provider is
    a change there and nowhere else.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("flanner/cli.py").read_text(encoding="utf-8"))
    holders = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and "netbird" in ast.dump(node).lower()
    }

    assert holders == {"_runtime"}, holders
