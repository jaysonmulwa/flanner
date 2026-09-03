"""Starting and stopping the background MCP server.

These commands used to describe a process that was never created: `start`
printed a config snippet and a comment in the source said "For now, we'll
just show instructions", while `stop` and `status` read a pid file nothing
ever wrote. Every reading of the state was therefore correct by accident.

The process is real now, so the pid handling has to be. That is mostly one
platform trap, tested here because it is silent and destructive.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from flanner import cli, server


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("FLANNER_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def sleeper():
    """A real child process to ask questions about, and its cleanup."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield child
    finally:
        child.kill()
        child.wait(timeout=10)


# --- the platform trap ---------------------------------------------------


def test_asking_whether_a_process_is_alive_leaves_it_alone(sleeper) -> None:
    """The check is a question, and must not also be an action."""
    assert cli._process_alive(sleeper.pid)
    assert cli._process_alive(sleeper.pid)
    assert sleeper.poll() is None


def test_a_pid_that_has_exited_reads_as_gone(sleeper) -> None:
    """The trap, and the reason `os.kill(pid, 0)` is not used here.

    On Windows that idiom reports a process as alive for as long as a handle
    to it can be opened, which outlives the process itself: a child run to
    completion still answers "running". A crashed server would then be
    reported as up for good, and `start` would refuse to replace it.
    """
    pid = sleeper.pid
    sleeper.kill()
    sleeper.wait(timeout=10)

    assert not cli._process_alive(pid)


# --- the pid file --------------------------------------------------------


def test_a_pid_file_left_by_a_crash_is_cleared(home, sleeper) -> None:
    """What is on disk after a hard kill, and what it must not be read as.

    Reporting a dead pid as running sends somebody looking for a process
    that is not there, and makes `start` refuse to start.
    """
    pid_file = home / "server.pid"
    pid = sleeper.pid
    sleeper.kill()
    sleeper.wait(timeout=10)
    pid_file.write_text(str(pid))

    assert cli._running_pid(pid_file) is None
    assert not pid_file.exists(), "the stale file was left for the next run to trip on"


def test_a_pid_file_that_is_not_a_number_is_cleared(home) -> None:
    """A truncated write, or somebody's editor. Not a reason to crash."""
    pid_file = home / "server.pid"
    pid_file.write_text("not a pid")

    assert cli._running_pid(pid_file) is None
    assert not pid_file.exists()


def test_a_live_pid_is_reported(home, sleeper) -> None:
    pid_file = home / "server.pid"
    pid_file.write_text(str(sleeper.pid))

    assert cli._running_pid(pid_file) == sleeper.pid
    assert pid_file.exists(), "a running server's pid file was deleted"


# --- the commands --------------------------------------------------------


def test_stopping_nothing_is_not_an_error(home) -> None:
    """Somebody who never started it should not be shown a failure."""
    result = CliRunner().invoke(cli.stop, [])

    assert result.exit_code == 0
    assert "not running" in result.output.lower()


def test_start_refuses_when_one_is_already_running(home, sleeper) -> None:
    """It must not spawn a second server onto a port already held."""
    (home / "server.pid").write_text(str(sleeper.pid))

    result = CliRunner().invoke(cli.start, ["--port", "8999"])

    assert result.exit_code == 0
    assert "already running" in result.output.lower()
    assert str(sleeper.pid) in result.output


def test_a_server_that_dies_is_reported_without_waiting_out_the_timeout(home, monkeypatch) -> None:
    """The wait for the port is ninety seconds, because a cold start is slow.

    That is only affordable if a child that has already exited ends the wait
    immediately. Otherwise every typo in the port argument costs a minute
    and a half of somebody staring at a terminal.
    """
    real_popen = subprocess.Popen

    def die_at_once(argv, **kwargs):
        return real_popen([sys.executable, "-c", "raise SystemExit(1)"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", die_at_once)

    result = CliRunner().invoke(cli.start, ["--port", "8998"])

    assert result.exit_code == 2
    assert "did not come up" in result.output
    assert "server.log" in result.output
    assert not (home / "server.pid").exists(), "a pid file was left for a process that never ran"


# --- what the http transport will and will not serve ---------------------


def test_the_http_transport_binds_loopback_only() -> None:
    """No option widens it, and that is deliberate.

    Every tool acts with the full authority of whoever started the server:
    create a project, rewrite a plan, delete one. There is no signature,
    token or entitlement in front of any of them, because stdio needed
    none. On another machine's network this is a remote shell over somebody
    else's design documents.
    """
    assert server.HTTP_HOST == "127.0.0.1"

    source = Path(server.__file__).read_text(encoding="utf-8")
    assert "--host" not in source, "an option to widen the bind was added"


def test_starting_names_a_port_the_argument_chose(home, monkeypatch) -> None:
    """The port reaches the child, rather than the child's own default."""
    seen: dict[str, list[str]] = {}
    real_popen = subprocess.Popen

    def capture(argv, **kwargs):
        seen["argv"] = list(argv)
        return real_popen([sys.executable, "-c", "raise SystemExit(1)"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture)
    CliRunner().invoke(cli.start, ["--port", "8997"])

    assert seen["argv"][1:] == ["-m", "flanner.server", "--http", "--port", "8997"]
    assert os.path.basename(seen["argv"][0]).startswith("python")
