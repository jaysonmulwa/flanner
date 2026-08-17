"""Local daemon discovery and IPC client.

The long-running local process (today: the web UI started by ``flanner web``)
is the single writer for plan state (PRD Phase 1). It advertises itself by
writing ``daemon.json`` (port, token, pid) into the flanner home directory.
Other processes — the stdio MCP server, the CLI — discover it there and
forward write operations over authenticated localhost HTTP. If no daemon is
running they fall back to in-process execution, which stays safe because the
write path itself takes the cross-process plan lock.

Stdlib only; every function fails soft (returns None) so callers can always
fall back to local execution.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TOKEN_ENV = "FLANNER_IPC_TOKEN"
_TIMEOUT_S = 10.0


def flanner_home() -> Path:
    """Flanner data directory (override with FLANNER_HOME)."""
    return Path(os.environ.get("FLANNER_HOME", Path.home() / ".flanner"))


def info_path() -> Path:
    return flanner_home() / "daemon.json"


def new_token() -> str:
    return secrets.token_hex(32)


def write_daemon_info(port: int, token: str) -> None:
    """Advertise a running daemon. Best effort; failure disables forwarding only."""
    with contextlib.suppress(OSError):
        path = info_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"port": port, "token": token, "pid": os.getpid()}),
            encoding="utf-8",
        )


def clear_daemon_info() -> None:
    with contextlib.suppress(OSError):
        info_path().unlink()


def read_daemon_info() -> dict[str, Any] | None:
    try:
        info = json.loads(info_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or "port" not in info or "token" not in info:
        return None
    return info


def call_daemon(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST to the local daemon; None when no daemon is reachable.

    An HTTP error response with a JSON body is returned as that body (the
    daemon speaks the same error-dict dialect as the MCP tools), so callers
    can pass it straight through.
    """
    info = read_daemon_info()
    if info is None:
        return None
    request = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Flanner-Token": str(info["token"]),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        with contextlib.suppress(Exception):
            return json.loads(e.read().decode("utf-8"))
        return None
    except (urllib.error.URLError, OSError, ValueError):
        # Daemon gone or unreachable: stale advertisement, fall back locally.
        return None
