"""Reading the control plane's reason, rather than its wording.

A refusal used to be a sentence. Matching on English meant that improving a
message was a breaking change, which pointed the pressure exactly the wrong
way — the messages most worth improving are the ones confusing somebody.

These pin the half of that contract this repository owns: the vocabulary is
here, in the public package, because a client cannot be asked to understand
something it cannot read.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from flanner import refusals
from flanner.account import SessionError, _refusal


def _http_error(
    status: int, body: dict | None, headers: dict | None = None
) -> urllib.error.HTTPError:
    payload = json.dumps(body).encode("utf-8") if body is not None else b"not json"
    return urllib.error.HTTPError(
        "https://app.flanner.io/v1/entitlements",
        status,
        "refused",
        headers or {},  # type: ignore[arg-type]
        io.BytesIO(payload),
    )


def test_the_code_is_read_from_the_body() -> None:
    message, code = _refusal(
        _http_error(403, {"detail": "your clock is 246s out of date", "code": refusals.CLOCK_SKEW})
    )
    assert code == refusals.CLOCK_SKEW
    assert "246s" in message


def test_a_refusal_without_a_code_is_unknown_rather_than_a_crash() -> None:
    """An older control plane, and a failure that never reached one at all."""
    _, code = _refusal(_http_error(403, {"detail": "nope"}))
    assert code == refusals.UNKNOWN

    _, code = _refusal(_http_error(500, None))
    assert code == refusals.UNKNOWN


def test_a_code_this_version_has_never_heard_of_survives() -> None:
    """A newer server will send these. It must not be an error.

    The whole point of a vocabulary is that both ends can be at different
    versions of it.
    """
    _, code = _refusal(_http_error(403, {"detail": "x", "code": "invented_next_year"}))
    assert code == "invented_next_year"
    assert not refusals.is_retryable(code)


def test_the_error_carries_the_code_to_whoever_catches_it() -> None:
    error = SessionError("lapsed", code=refusals.SUBSCRIPTION_INACTIVE)
    assert error.code == refusals.SUBSCRIPTION_INACTIVE
    assert not error.retryable


def test_a_throttle_is_retryable_and_says_when() -> None:
    message, code = _refusal(
        _http_error(
            429, {"detail": "slow down", "code": refusals.THROTTLED}, {"Retry-After": "30"}
        )
    )
    assert code == refusals.THROTTLED
    assert refusals.is_retryable(code)
    assert "30s" in message


def test_the_default_error_has_a_code_so_callers_need_not_guess() -> None:
    """`SessionError` is raised locally too — before any request is made."""
    assert SessionError("no endpoint").code == refusals.UNKNOWN


@pytest.mark.parametrize("code", sorted(refusals.KNOWN))
def test_every_declared_code_is_a_plain_lowercase_token(code: str) -> None:
    """These end up in logs, dashboards and other people's `if` statements.

    A code with a space or a capital in it is one somebody will normalise
    differently at each end.
    """
    assert code == code.lower()
    assert " " not in code
    assert code.replace("_", "").isalnum()


def test_unknown_is_never_one_of_the_codes_we_send() -> None:
    """It is the client's word for "I did not recognise that", not a state."""
    assert refusals.UNKNOWN not in refusals.KNOWN


def test_retrying_everything_unrecognised_is_not_the_default() -> None:
    """The failure mode this avoids is specific.

    A client that retried what it did not understand would hammer a control
    plane at the exact moment it had learned to refuse something new.
    """
    assert refusals.RETRYABLE < refusals.KNOWN
    assert len(refusals.RETRYABLE) < len(refusals.KNOWN) / 2
