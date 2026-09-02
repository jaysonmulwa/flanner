"""Properties that must hold for every input, not just the ones we thought of.

The example-based suite covers the cases somebody sat down and imagined. These
cover the ones nobody did. Everything here is an invariant the protocol depends
on, so a counterexample is a wire-format bug rather than a style question:

- `canonical_bytes` is the artifact id. If two machines can serialise the same
  envelope differently, they compute different ids for the same content, and
  sync silently duplicates instead of deduplicating.
- Signature verification must be exactly as strict as signing. A round-trip
  that fails is a device that cannot talk to itself; one that passes on a
  mutated payload is a forgery.
- A device id is a hash of a public key. Two keys must never collide onto one
  id, or one device can impersonate another.
- Anchoring resolves review comments to quoted text. Its normalisation has to
  be idempotent, or a comment's anchor drifts every time it is re-resolved.

Kept in one file rather than scattered, because these are the claims the rest
of the system is allowed to assume.
"""

from __future__ import annotations

import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from flanner import anchors, artifacts, identity

# Envelope fields are protocol strings, not prose: no surrogates, and nothing
# that json cannot round-trip. Text that cannot cross the wire is a separate
# concern from text that serialises inconsistently.
text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=200,
)
scalars = st.one_of(text, st.integers(), st.booleans(), st.none())
envelopes = st.dictionaries(text, scalars, max_size=8)


# --- canonical serialisation ------------------------------------------------


@given(envelopes)
def test_canonical_bytes_is_stable(fields: dict) -> None:
    """The same envelope serialises identically every time it is asked."""
    assert artifacts.canonical_bytes(fields) == artifacts.canonical_bytes(fields)


@given(envelopes)
def test_canonical_bytes_ignores_key_order(fields: dict) -> None:
    """Two machines that built the same envelope differently agree on its id.

    This is the property the whole content-addressed store rests on. Without
    it, the same plan version pushed from two devices deduplicates on one and
    not the other.
    """
    shuffled = dict(reversed(list(fields.items())))
    assert artifacts.canonical_bytes(shuffled) == artifacts.canonical_bytes(fields)


@given(envelopes)
def test_canonical_bytes_round_trips(fields: dict) -> None:
    """What is hashed is what was meant, not a lossy rendering of it."""
    assert json.loads(artifacts.canonical_bytes(fields).decode("utf-8")) == fields


@given(st.binary(max_size=500))
def test_hashing_is_deterministic_and_labelled(payload: bytes) -> None:
    """The `sha256:` prefix is protocol, not decoration.

    It names the algorithm in the value, so a future migration to another one
    is a readable distinction rather than a silent reinterpretation of the
    same-length hex. Asserted here because content hashes cross the wire.
    """
    first = artifacts.hash_bytes(payload)
    assert first == artifacts.hash_bytes(payload)
    algorithm, _, digest = first.partition(":")
    assert algorithm == "sha256"
    assert len(digest) == 64 and int(digest, 16) >= 0


# --- signatures -------------------------------------------------------------


@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(st.binary(max_size=300))
def test_a_signature_verifies_against_its_own_payload(payload: bytes) -> None:
    key = Ed25519PrivateKey.generate()
    public = identity.public_key_b64(key.public_key())
    assert identity.verify(public, payload, identity.sign(payload, key))


@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(st.binary(max_size=300), st.binary(max_size=300))
def test_a_signature_does_not_verify_against_other_bytes(a: bytes, b: bytes) -> None:
    """The negative half. A check that only ever passes proves nothing."""
    if a == b:
        return
    key = Ed25519PrivateKey.generate()
    public = identity.public_key_b64(key.public_key())
    assert not identity.verify(public, b, identity.sign(a, key))


@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(st.binary(max_size=200))
def test_another_devices_key_cannot_verify_your_signature(payload: bytes) -> None:
    mine, theirs = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    signature = identity.sign(payload, mine)
    assert not identity.verify(identity.public_key_b64(theirs.public_key()), payload, signature)


# --- device identity --------------------------------------------------------


@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(min_value=0, max_value=2**16))
def test_a_device_id_is_a_function_of_the_key_alone(_seed: int) -> None:
    """Same key, same id, every time and on every machine."""
    key = Ed25519PrivateKey.generate()
    once = identity.device_id_for(key.public_key())
    assert once == identity.device_id_for(key.public_key())
    assert once.startswith("dev_")


@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(min_value=0, max_value=2**16))
def test_two_keys_never_share_a_device_id(_seed: int) -> None:
    """A collision here is one device able to answer as another."""
    a, b = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    assert identity.device_id_for(a.public_key()) != identity.device_id_for(b.public_key())


# --- anchoring --------------------------------------------------------------


@given(text)
def test_normalising_twice_changes_nothing(body: str) -> None:
    """Idempotent, or a comment's anchor drifts each time it is resolved."""
    once = anchors.normalise(body)
    assert anchors.normalise(once) == once


@given(text)
def test_counting_occurrences_never_raises(body: str) -> None:
    """Runs against text a reviewer pasted, so it must survive anything."""
    assert anchors.occurrences(body, body) >= 0 if body.strip() else True
