"""Getting an entitlement onto a device, and living without one."""

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from flanner import identity
from flanner import session as account
from flanner.entitlements import EXPIRED, IN_GRACE, MALFORMED, VALID


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path))
    return tmp_path


def a_session(**overrides):
    defaults = dict(
        endpoint="https://api.example.test",
        device_id="dev_abc",
        organization_id="org_1",
        user_id="maria",
        entitlement="claims.signature",
        keyring={"sk_1": "key"},
    )
    return account.Session(**{**defaults, **overrides})


# --- the cache -------------------------------------------------------------


def test_a_saved_session_survives_a_round_trip(home):
    account.save(a_session())
    loaded = account.load()
    assert loaded == a_session()


def test_nothing_saved_means_not_logged_in(home):
    assert account.load() is None


@pytest.mark.skipif(os.name != "posix", reason="Windows has no POSIX mode bits")
def test_the_cache_is_readable_only_by_its_owner(home):
    """It is not a secret worth stealing, but it is nobody else's business."""
    path = account.save(a_session())
    mode = stat.S_IMODE(path.stat().st_mode)
    assert not mode & (stat.S_IRGRP | stat.S_IROTH), oct(mode)


def test_the_cache_is_created_restricted_rather_than_widened_afterwards(home, monkeypatch):
    """Runs everywhere, including where mode bits are not enforced.

    Creating then chmod-ing would leave a window where the file is readable,
    so the mode has to be an argument to the create itself.
    """
    seen = []
    real_open = os.open
    monkeypatch.setattr(
        os,
        "open",
        lambda path, flags, *mode: (seen.append(mode), real_open(path, flags, *mode))[1],
    )
    account.save(a_session())
    assert seen and all(mode == (0o600,) for mode in seen), seen


def test_a_corrupt_cache_reads_as_never_logged_in(home):
    account.session_path().write_text("{ not json", encoding="utf-8")
    assert account.load() is None


def test_a_truncated_cache_reads_as_never_logged_in(home):
    """Missing fields must not crash a command that only wanted the device id."""
    account.session_path().write_text(json.dumps({"user_id": "maria"}), encoding="utf-8")
    assert account.load() is None


def test_saving_leaves_no_temporary_file_behind(home):
    account.save(a_session())
    assert sorted(p.name for p in home.iterdir()) == ["session.json"]


def test_logging_out_forgets_the_session_but_keeps_the_device_key(home):
    identity.load_or_create_device_key()
    before = identity.device_id()
    account.save(a_session())

    assert account.clear() is True
    assert account.load() is None
    assert identity.device_id() == before, "the same machine must not look like a new one"


def test_logging_out_twice_is_harmless(home):
    assert account.clear() is False


# --- the network edge ------------------------------------------------------


def test_login_requires_a_code(home):
    with pytest.raises(account.SessionError, match="code is required"):
        account.login("   ", endpoint="https://api.example.test")


def test_a_non_http_endpoint_is_refused(home):
    """Otherwise urllib would happily open a file:// URL."""
    with pytest.raises(account.SessionError, match="not an http endpoint"):
        account.login("code", endpoint="file:///etc/passwd")


def test_refreshing_without_a_session_says_so(home):
    with pytest.raises(account.SessionError, match="not logged in"):
        account.refresh()


def test_an_unusable_response_is_reported_not_cached(home):
    with pytest.raises(account.SessionError, match="unusable"):
        account.Session.from_response("https://api.example.test", {"device_id": "dev_abc"})


# --- graded expiry ---------------------------------------------------------


def test_a_session_with_no_token_reports_malformed(home):
    assert a_session(entitlement="").status().status == MALFORMED


def test_ensure_fresh_returns_nothing_when_never_logged_in(home):
    assert account.ensure_fresh() is None


def test_ensure_fresh_does_not_call_out_for_a_valid_entitlement(home, monkeypatch):
    """A working entitlement is used as-is; the network is not consulted."""
    account.save(a_session())
    monkeypatch.setattr(
        account, "refresh", lambda *a, **k: pytest.fail("should not have refreshed")
    )
    monkeypatch.setattr(account.Session, "status", lambda self, **k: _verdict(VALID))
    assert account.ensure_fresh() is not None


@pytest.mark.parametrize("state", [IN_GRACE, EXPIRED, MALFORMED])
def test_an_unreachable_control_plane_falls_back_to_the_cache(home, monkeypatch, state):
    """Losing the network must cost team features later, not this command now."""
    saved = a_session()
    account.save(saved)
    monkeypatch.setattr(account.Session, "status", lambda self, **k: _verdict(state))
    monkeypatch.setattr(
        account, "refresh", _raising(account.SessionError("could not reach the control plane"))
    )
    assert account.ensure_fresh() == saved


def _verdict(state):
    from flanner.entitlements import Verdict

    return Verdict(state)


def _raising(error):
    def fail(*args, **kwargs):
        raise error

    return fail


# --- what a signed request proves ------------------------------------------


def test_a_refresh_request_is_signed_by_the_device_key(home):
    from flanner.device_auth import sign_request, verify_request

    request = sign_request({"why": "refresh"})
    assert verify_request(request, identity.device_public_key_b64())


def test_a_request_signed_by_another_key_does_not_verify(home, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from flanner.device_auth import sign_request, verify_request

    impostor = Ed25519PrivateKey.generate()
    request = sign_request({}, device_id=identity.device_id(), signing_key=impostor)
    result = verify_request(request, identity.device_public_key_b64())
    assert not result and "signature" in result.reason


def test_changing_the_body_after_signing_invalidates_it(home):
    """The signature covers the arguments, not just the identity."""
    from dataclasses import replace

    from flanner.device_auth import sign_request, verify_request

    request = sign_request({"workspace_id": "ws_mine"})
    tampered = replace(request, body={"workspace_id": "ws_yours"})
    assert not verify_request(tampered, identity.device_public_key_b64())


def test_a_captured_request_stops_working(home):
    from flanner.device_auth import sign_request, verify_request

    request = sign_request({})
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    result = verify_request(request, identity.device_public_key_b64(), now=later)
    assert not result and "out of date" in result.reason


def test_a_request_without_a_nonce_is_refused(home):
    from dataclasses import replace

    from flanner.device_auth import sign_request, verify_request

    request = replace(sign_request({}), nonce="")
    assert not verify_request(request, identity.device_public_key_b64())


def test_two_requests_never_share_a_nonce(home):
    from flanner.device_auth import sign_request

    assert sign_request({}).nonce != sign_request({}).nonce


def test_a_garbled_request_is_refused_rather_than_raising(home):
    from flanner.device_auth import SignedRequest, verify_request

    request = SignedRequest("dev_x", "not-a-date", "n", {}, "sig")
    assert not verify_request(request, identity.device_public_key_b64())


def test_a_request_missing_a_field_is_reported_as_malformed():
    from flanner.device_auth import SignedRequest

    with pytest.raises(ValueError, match="Malformed"):
        SignedRequest.from_dict({"device_id": "dev_x"})


# --- the CLI ---------------------------------------------------------------


def test_whoami_without_an_account_does_not_look_like_a_failure(home):
    """Most users never log in. That is a supported state, not an error."""
    from click.testing import CliRunner

    from flanner.cli import cli

    result = CliRunner(env={"FLANNER_HOME": str(home)}).invoke(cli, ["whoami"])
    assert result.exit_code == 0
    assert "not signed in" in result.output
    assert identity.device_id() in result.output


def test_login_reports_a_refusal_and_exits_nonzero(home):
    from click.testing import CliRunner

    from flanner.cli import cli

    result = CliRunner(env={"FLANNER_HOME": str(home)}).invoke(
        cli, ["login", "bad-code", "--endpoint", "http://127.0.0.1:1"]
    )
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_logout_is_quiet_when_there_is_nothing_to_forget(home):
    from click.testing import CliRunner

    from flanner.cli import cli

    result = CliRunner(env={"FLANNER_HOME": str(home)}).invoke(cli, ["logout"])
    assert result.exit_code == 0
    assert "Not signed in" in result.output


def test_the_session_file_never_holds_the_device_private_key(home):
    """A cached entitlement is copyable; the thing that makes it usable is not."""
    identity.load_or_create_device_key()
    account.save(a_session())
    assert "PRIVATE KEY" not in account.session_path().read_text(encoding="utf-8")
    assert os.path.exists(identity.device_key_path())
