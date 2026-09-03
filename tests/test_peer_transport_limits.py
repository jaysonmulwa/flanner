"""What the http fallback transport does before it trusts anything.

Every other limit in `peer.py` runs *after* the body has been read and
decoded into a dict: batch sizes, payload bytes, the rate limiter. Those
bound what an authorised peer may store. None of them bound what an
unauthenticated caller can make this device allocate, because reaching any
of them means the body was already parsed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from flanner import peer, sync


@pytest.fixture
def client() -> Any:
    """The peer app with nothing behind it.

    Nothing here reaches a handler, which is the point: the refusals under
    test happen before any of them runs, so a session and a catalog would
    only be scenery.
    """

    def no_sessions() -> Any:  # pragma: no cover - never called
        raise AssertionError("a refused request reached the handler")

    return TestClient(
        peer.create_peer_app(no_sessions, lambda: None), raise_server_exceptions=False
    )


def test_an_oversized_request_is_refused_before_it_is_parsed(client) -> None:
    """The hole. A gigabyte of json parsed before a signature is looked at."""
    response = client.post(
        "/peer/push",
        content=b"x" * 16,
        headers={
            "content-type": "application/json",
            "content-length": str(sync.MAX_REQUEST_BYTES + 1),
        },
    )

    assert response.status_code == 413
    assert str(sync.MAX_REQUEST_BYTES) in response.text


def test_a_body_with_no_declared_length_is_refused(client) -> None:
    """A chunked body has no length to check, so there is nothing to bound.

    Refusing it is safe here because every client of this protocol is one of
    ours and frames its requests, and the alternative is a limit that any
    caller can opt out of by omitting a header.
    """
    response = client.post(
        "/peer/push",
        content=(chunk for chunk in (b'{"a":1}',)),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 411


def test_a_normal_request_still_gets_through(client) -> None:
    """The limit must not be the thing that breaks ordinary syncing.

    A 500 here is the handler being reached and failing on the deliberately
    broken session factory, which is exactly the proof wanted: the request
    passed the transport checks.
    """
    response = client.post("/peer/manifest", json={"workspace_id": "ws"})

    assert response.status_code not in (411, 413)


def test_the_transport_ceiling_clears_a_full_push() -> None:
    """The two limits have to stay in step.

    `MAX_PUSH_BYTES` of artifacts travel base64-encoded inside json, which
    costs about a third. A transport ceiling below that would refuse a push
    the protocol says is legal, and the failure would look like a network
    fault rather than a limit.
    """
    assert sync.MAX_REQUEST_BYTES > sync.MAX_PUSH_BYTES * 4 // 3
