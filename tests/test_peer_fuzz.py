"""Feeding the peer protocol whatever another machine might send.

`peer.authorize` and `peer.serve_request` are the only functions in the
package that take input from a machine we do not control, before anything
has been proved about the sender. Everything else runs on data that already
passed through here.

So the bar is different. It is not "does it work on valid input" — the
example suite covers that — it is **does hostile input produce a refusal
this code chose, or an exception that escaped**. A `PeerError` is the
correct answer to anything malformed. A `KeyError` is a bug, and on an
unauthenticated endpoint it is a bug an attacker picks the input for.

Structure-aware where it matters: random bytes mostly get rejected by the
JSON parser and prove little, so these build things shaped like real
requests and then break one part at a time.
"""

from __future__ import annotations

from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from flanner import peer

settings.register_profile("fuzz", deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("fuzz")

WORKSPACE = "ws_fuzz"

# Anything JSON can carry, since a peer sends JSON and we control neither
# what it puts in the fields nor whether it sends the fields at all.
json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=60),
)
json_values = st.recursive(
    json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4), st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=8,
)

REQUEST_KEYS = ["device_id", "issued_at", "nonce", "body", "signature"]


def _assert_refused(call: Any) -> None:
    """The contract: a chosen refusal, never a leaked exception."""
    try:
        call()
    except peer.PeerError:
        return  # the whole point
    except Exception as exc:  # noqa: BLE001 - the assertion is which type escaped
        raise AssertionError(
            f"{type(exc).__name__} escaped authorize(): {exc}. Hostile input must "
            f"produce PeerError, not whatever the parser raised."
        ) from exc


@given(st.dictionaries(st.sampled_from(REQUEST_KEYS), json_values, max_size=5))
def test_a_request_with_arbitrary_fields_is_refused(payload: dict) -> None:
    """Right keys, wrong types. The shape a confused or hostile peer sends."""
    _assert_refused(lambda: peer.authorize(payload, WORKSPACE, {}))


@given(st.dictionaries(st.text(max_size=12), json_values, max_size=6))
def test_a_request_with_unknown_fields_is_refused(payload: dict) -> None:
    """Keys we have never heard of, including none of the ones required."""
    _assert_refused(lambda: peer.authorize(payload, WORKSPACE, {}))


@given(st.text(max_size=200))
def test_a_public_key_that_is_not_one_is_refused(key: str) -> None:
    """The key travels with the request and is attacker-chosen.

    It is loaded before anything is verified, because the device id has to be
    derived from it — so the loader runs on untrusted bytes every time.
    """
    payload = {
        "device_id": "dev_0000000000000000",
        "issued_at": "2026-01-01T00:00:00Z",
        "nonce": "aa",
        "body": {"workspace_id": WORKSPACE, "public_key": key, "entitlement": ""},
        "signature": "",
    }
    _assert_refused(lambda: peer.authorize(payload, WORKSPACE, {}))


@given(st.text(max_size=120))
def test_an_entitlement_that_is_not_one_is_refused(entitlement: str) -> None:
    """A signed blob we are handed and must parse before trusting."""
    payload = {
        "device_id": "dev_0000000000000000",
        "issued_at": "2026-01-01T00:00:00Z",
        "nonce": "aa",
        "body": {
            "workspace_id": WORKSPACE,
            "public_key": "not-a-key",
            "entitlement": entitlement,
        },
        "signature": "",
    }
    _assert_refused(lambda: peer.authorize(payload, WORKSPACE, {}))


@given(st.text(max_size=60))
def test_an_unparseable_timestamp_is_refused(issued_at: str) -> None:
    """`issued_at` is parsed with fromisoformat, which raises on nonsense."""
    payload = {
        "device_id": "dev_0000000000000000",
        "issued_at": issued_at,
        "nonce": "aa",
        "body": {"workspace_id": WORKSPACE, "public_key": "x", "entitlement": ""},
        "signature": "",
    }
    _assert_refused(lambda: peer.authorize(payload, WORKSPACE, {}))


@given(st.lists(st.text(max_size=20), max_size=30), st.text(max_size=20))
def test_arbitrary_operations_and_ids_are_refused(wanted: list[str], operation: str) -> None:
    """`serve_request` dispatches on an operation string a peer chooses."""
    payload = {
        "device_id": "dev_0000000000000000",
        "issued_at": "2026-01-01T00:00:00Z",
        "nonce": "aa",
        "body": {
            "workspace_id": WORKSPACE,
            "public_key": "x",
            "entitlement": "",
            "artifact_ids": wanted,
        },
        "signature": "",
    }

    def call() -> None:
        peer.serve_request(operation, payload, _no_sessions, lambda: None)

    _assert_refused(call)


def _no_sessions() -> Any:
    raise AssertionError("a refused request must never reach the database")
