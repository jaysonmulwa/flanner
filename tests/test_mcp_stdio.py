"""End-to-end MCP test: drive `python -m flanner.server` over stdio.

This is the path a real MCP client (Claude Desktop/Code) uses; direct
tool-function tests can't catch startup bugs like a missing database init.
"""

import json
import os
import subprocess
import sys
import threading


def test_stdio_server_initialize_and_list_projects(tmp_path):
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        },
    ]

    env = dict(os.environ, FLANNER_HOME=str(tmp_path / "home"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "flanner.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    watchdog = threading.Timer(60, proc.kill)
    watchdog.start()
    replies = {}
    try:
        assert proc.stdin and proc.stdout
        # keep stdin open until the last reply arrives: the server shuts
        # down on EOF and would drop queued requests
        proc.stdin.write("".join(json.dumps(m) + "\n" for m in messages))
        proc.stdin.flush()
        while 3 not in replies:
            line = proc.stdout.readline()
            if not line:  # server died (or watchdog killed it)
                break
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in m:
                replies[m["id"]] = m
        proc.stdin.close()
        proc.wait(timeout=15)
    finally:
        watchdog.cancel()
        proc.kill()

    assert replies[1]["result"]["serverInfo"], replies.get(1)
    tool_names = {t["name"] for t in replies[2]["result"]["tools"]}
    assert "list_projects" in tool_names and "create_plan_file_tool" in tool_names

    # The regression: a fresh server process must serve DB-backed tools
    call = replies[3]["result"]
    assert not call.get("isError"), call
