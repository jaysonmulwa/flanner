"""Retrying a blip, and never a request that a repeat would spoil.

The client's rule is the control plane's rule: a connection that never
completed is safe to try again; a response with a status is not, because the
far side received it.

There is one wrinkle the server does not have. A signed request carries a
nonce that is spent on use, so the *same* envelope sent twice is refused as
a replay. Every retry here has to sign afresh, and the test for that is the
one most likely to catch a future mistake.
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from flanner import account, refusals


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account.time, "sleep", lambda _: None)


def _refuses(status: int, body: bytes, headers: Any = None) -> urllib.error.HTTPError:
    import io

    return urllib.error.HTTPError(
        "http://x.test/v1/x", status, "no", headers or {}, io.BytesIO(body)
    )


def test_a_connection_failure_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def flaky(request: Any, timeout: float = 0) -> Any:
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError("connection refused")
        raise _refuses(403, b'{"detail":"done","code":"not_admin"}')

    monkeypatch.setattr(account.urllib.request, "urlopen", flaky)
    with pytest.raises(account.SessionError) as caught:
        account._post("http://x.test", "/v1/x", {}, repeatable=True)
    assert len(attempts) == 3
    assert caught.value.code == refusals.NOT_ADMIN


def test_a_request_that_is_not_repeatable_fails_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enrolment spends a one-shot code. A blind repeat is not an improvement."""
    attempts = []

    def down(request: Any, timeout: float = 0) -> Any:
        attempts.append(1)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(account.urllib.request, "urlopen", down)
    with pytest.raises(account.SessionError):
        account._post("http://x.test", "/v1/devices/enroll", {})
    assert attempts == [1]


def test_a_server_error_is_never_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    """It answered. Repeating it is how one create becomes two."""
    attempts = []

    def boom(request: Any, timeout: float = 0) -> Any:
        attempts.append(1)
        raise _refuses(500, b'{"detail":"boom"}')

    monkeypatch.setattr(account.urllib.request, "urlopen", boom)
    with pytest.raises(account.SessionError):
        account._post("http://x.test", "/v1/x", {}, repeatable=True)
    assert attempts == [1]


def test_each_attempt_is_signed_afresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wrinkle a server-side retry does not have.

    A nonce is spent on use, so resending the same envelope is refused as a
    replay. If this ever regresses, a retry stops renewing anything and
    starts reporting `replayed`, which reads like an attack rather than a
    bug.
    """
    import json

    bodies = []

    def capture(request: Any, timeout: float = 0) -> Any:
        bodies.append(json.loads(request.data.decode()))
        if len(bodies) < 3:
            raise urllib.error.URLError("connection refused")
        raise _refuses(403, b'{"detail":"stop","code":"not_admin"}')

    monkeypatch.setattr(account.urllib.request, "urlopen", capture)
    counter = iter(range(100))
    with pytest.raises(account.SessionError):
        account._post(
            "http://x.test",
            "/v1/x",
            lambda: {"nonce": f"n{next(counter)}"},
            repeatable=True,
        )
    assert len(bodies) == 3
    assert len({b["nonce"] for b in bodies}) == 3, "the same envelope was sent twice"


def test_a_short_retry_after_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 means nothing was applied, so trying again is safe regardless."""
    from email.message import Message

    headers = Message()
    headers["Retry-After"] = "2"
    attempts = []

    def throttled(request: Any, timeout: float = 0) -> Any:
        attempts.append(1)
        raise _refuses(429, b'{"detail":"slow down","code":"throttled"}', headers)

    monkeypatch.setattr(account.urllib.request, "urlopen", throttled)
    with pytest.raises(account.SessionError) as caught:
        account._post("http://x.test", "/v1/x", {})
    assert len(attempts) == account.ATTEMPTS
    assert caught.value.code == refusals.THROTTLED


def test_a_long_retry_after_is_reported_rather_than_waited_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody is watching a prompt. Half a minute of nothing is not help."""
    from email.message import Message

    headers = Message()
    headers["Retry-After"] = "300"
    attempts = []

    def throttled(request: Any, timeout: float = 0) -> Any:
        attempts.append(1)
        raise _refuses(429, b'{"detail":"slow down","code":"throttled"}', headers)

    monkeypatch.setattr(account.urllib.request, "urlopen", throttled)
    with pytest.raises(account.SessionError) as caught:
        account._post("http://x.test", "/v1/x", {})
    assert attempts == [1]
    assert "300s" in str(caught.value)


def test_the_operations_that_may_repeat_are_the_harmless_ones() -> None:
    """Read off the source, so a new caller has to make the choice.

    Minting a code and sending an invitation are deliberately absent: one
    leaves a second credential valid, the other emails somebody twice.
    """
    import inspect

    source = inspect.getsource(account)
    for safe in ("/v1/devices/list", "/v1/devices/keyring", "/v1/members/list"):
        line = next(line for line in source.splitlines() if safe in line)
        assert "repeatable=True" in line, f"{safe} is a read and could repeat"
    for unsafe in ("/v1/devices/codes", "/v1/members/invite"):
        line = next(line for line in source.splitlines() if unsafe in line)
        assert "repeatable=True" not in line, f"{unsafe} must not repeat"
