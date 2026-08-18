"""Verifying entitlements, and using them as real authorization (PRD §11.3)."""

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flanner import identity, workflow
from flanner.entitlements import (
    BAD_SIGNATURE,
    EXPIRED,
    IN_GRACE,
    MALFORMED,
    UNTRUSTED_KEY,
    VALID,
    Claims,
    EntitlementStore,
    WorkspaceCapability,
    encode_token,
    roles_from_entitlement,
    signing_payload,
    verify,
)

KEY_ID = "sk_test"


@pytest.fixture
def key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def keyring(key):
    return {KEY_ID: identity.public_key_b64(key.public_key())}


def make_token(key, *, capabilities=(), lifetime=timedelta(hours=24), issued_at=None, **kw):
    """Sign an entitlement the way the control plane would."""
    moment = issued_at or datetime.now(timezone.utc)
    params = {
        "organization_id": "org_1",
        "user_id": "maria",
        "device_id": "dev_a",
        "key_id": KEY_ID,
        "issued_at": moment.isoformat().replace("+00:00", "Z"),
        "expires_at": (moment + lifetime).isoformat().replace("+00:00", "Z"),
        "workspace_capabilities": tuple(capabilities),
    }
    params.update(kw)
    claims = Claims(**params)
    return encode_token(claims, identity.sign(signing_payload(claims), key))


# --- verification ---


def test_a_well_formed_entitlement_verifies(key, keyring):
    verdict = verify(make_token(key), keyring)
    assert verdict.status == VALID
    assert verdict.usable and bool(verdict) is True
    assert verdict.claims.user_id == "maria"


def test_an_unknown_key_id_is_not_trusted(key):
    assert verify(make_token(key), {"sk_other": "x"}).status == UNTRUSTED_KEY


def test_a_signature_from_the_wrong_key_is_refused(key):
    impostor = Ed25519PrivateKey.generate()
    keyring = {KEY_ID: identity.public_key_b64(impostor.public_key())}
    assert verify(make_token(key), keyring).status == BAD_SIGNATURE


@pytest.mark.parametrize(
    "token",
    ["", ".", "no-separator", "!!!.sig", "eyJhIjoxfQ.sig", "e30.sig"],
)
def test_hostile_tokens_are_reported_never_raised(token, keyring):
    """Verification sits on every connection path; it must not be crashable."""
    verdict = verify(token, keyring)
    assert verdict.usable is False
    assert verdict.status in (MALFORMED, UNTRUSTED_KEY, BAD_SIGNATURE)
    assert verdict.reason


def test_an_unreadable_expiry_is_malformed_not_permanent(key, keyring):
    assert verify(make_token(key, expires_at="whenever"), keyring).status == MALFORMED


# --- expiry and grace ---


def test_a_recently_expired_entitlement_stays_usable(key, keyring):
    token = make_token(key, issued_at=datetime.now(timezone.utc) - timedelta(days=2))
    verdict = verify(token, keyring)
    assert verdict.status == IN_GRACE
    assert verdict.usable is True


def test_a_long_expired_entitlement_stops(key, keyring):
    token = make_token(key, issued_at=datetime.now(timezone.utc) - timedelta(days=60))
    assert verify(token, keyring).usable is False
    assert verify(token, keyring).status == EXPIRED


def test_verification_can_be_asked_about_a_specific_moment(key, keyring):
    token = make_token(key)
    later = datetime.now(timezone.utc) + timedelta(days=2)
    assert verify(token, keyring, now=later).status == IN_GRACE


# --- the store a device holds ---


def test_a_device_with_no_entitlement_says_so(keyring):
    verdict = EntitlementStore(keyring=keyring).current()
    assert verdict.usable is False
    assert "no entitlement held" in verdict.reason


def test_a_cached_entitlement_is_checked_without_the_control_plane(key, keyring):
    """Nothing here reaches the network: an outage must not stop work."""
    store = EntitlementStore(token=make_token(key), keyring=keyring)
    assert store.current().status == VALID


# --- the bridge: a signed capability becomes an authorization decision ---


def test_a_capability_becomes_the_role_map_the_workflow_consumes(key, keyring):
    token = make_token(key, capabilities=[WorkspaceCapability("ws_core", workflow.MAINTAINER)])
    claims = verify(token, keyring).claims
    assert roles_from_entitlement(claims, "ws_core") == {"maria": workflow.MAINTAINER}


def test_a_workspace_the_entitlement_does_not_cover_grants_nothing(key, keyring):
    token = make_token(key, capabilities=[WorkspaceCapability("ws_core", workflow.MAINTAINER)])
    claims = verify(token, keyring).claims
    assert roles_from_entitlement(claims, "ws_unrelated") == {}


def test_a_granted_role_authorizes_review_end_to_end(key, keyring):
    """The payoff: authorization now comes from a signature, not a local map."""
    token = make_token(key, capabilities=[WorkspaceCapability("ws_core", workflow.MAINTAINER)])
    roles = roles_from_entitlement(verify(token, keyring).claims, "ws_core")

    proposal = workflow.make_proposal(
        workspace_id="ws_core",
        plan_file_id="plan_1",
        target_artifact_id="sha256:v1",
        actor_user_id="maria",
    )
    decision = workflow.make_decision(
        workspace_id="ws_core",
        plan_file_id="plan_1",
        proposal_id=proposal.event_id,
        target_artifact_id="sha256:v1",
        action=workflow.APPROVE,
        actor_user_id="maria",
    )
    accepted = workflow.make_accepted_head(
        workspace_id="ws_core",
        plan_file_id="plan_1",
        target_artifact_id="sha256:v1",
        proposal_id=proposal.event_id,
        decision_event_ids=[decision.event_id],
        actor_user_id="maria",
    )

    state = workflow.project([proposal, decision, accepted], roles)
    assert state.accepted_artifact_id == "sha256:v1"


def test_a_reader_capability_cannot_approve(key, keyring):
    """What the local placeholder could never enforce."""
    token = make_token(key, capabilities=[WorkspaceCapability("ws_core", workflow.READER)])
    roles = roles_from_entitlement(verify(token, keyring).claims, "ws_core")

    proposal = workflow.make_proposal(
        workspace_id="ws_core",
        plan_file_id="plan_1",
        target_artifact_id="sha256:v1",
        actor_user_id="maria",
    )
    state = workflow.project([proposal], roles)
    assert state.proposals == {}
    assert any("may not propose" in reason for _, reason in state.rejected)


def test_an_expired_entitlement_still_reports_the_role_it_granted(key, keyring):
    """Grace means a disconnected reviewer keeps working, deliberately."""
    token = make_token(
        key,
        capabilities=[WorkspaceCapability("ws_core", workflow.MAINTAINER)],
        issued_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    verdict = verify(token, keyring)
    assert verdict.status == IN_GRACE
    assert roles_from_entitlement(verdict.claims, "ws_core")["maria"] == workflow.MAINTAINER
