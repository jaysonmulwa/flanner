"""What flanner records about itself, and what it refuses to record.

The privacy rule is the interesting half. This tool's whole promise is that
plan contents stay on the machine that wrote them, and a log is copied into
issues and pasted into chats far more casually than a database is. The first
time somebody finds a paragraph of their design doc in one is the last time
they trust this with anything.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from flanner import observe

SECRET = "the paragraph nobody should ever see in a log"


@pytest.fixture(autouse=True)
def _fresh() -> None:
    observe.reset()
    observe._tool_logger = None


# --- the timing breakdown ---------------------------------------------------


def test_a_command_that_measured_nothing_prints_nothing() -> None:
    """No empty heading for a command that does no timed work."""
    assert observe.breakdown() == []


def test_the_breakdown_names_the_steps_and_totals_them() -> None:
    with observe.step("resolve peer"):
        pass
    with observe.step("fetch and verify"):
        pass
    lines = observe.breakdown()
    assert any("resolve peer" in line for line in lines)
    assert any("fetch and verify" in line for line in lines)
    assert any("total" in line for line in lines)


def test_a_single_step_is_not_given_a_total() -> None:
    """A total identical to the only row is noise."""
    with observe.step("one thing"):
        pass
    assert not any("total" in line for line in observe.breakdown())


def test_a_step_that_raises_is_still_timed() -> None:
    """The failing step is the one you most want the number for."""
    with pytest.raises(ValueError), observe.step("doomed"):
        raise ValueError("boom")
    assert any("doomed" in line for line in observe.breakdown())


def test_counts_appear_beside_the_times() -> None:
    """Fourteen artifacts in 1.8s and one in 1.8s are different problems."""
    with observe.step("fetch"):
        pass
    observe.count(accepted=14, rejected=1)
    assert any("accepted=14" in line and "rejected=1" in line for line in observe.breakdown())


# --- the MCP log ------------------------------------------------------------


def test_a_tool_call_is_recorded_with_its_outcome(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(observe.LOG_PATH_ENV, str(tmp_path / "mcp.log"))
    observe.tool_call("create_plan_file_tool", ms=31.2, ok=True, name="architecture")
    observe.tool_call("update_plan_file_tool", ms=8.1, ok=False, error="plan is locked")

    written = (tmp_path / "mcp.log").read_text(encoding="utf-8")
    assert "tool=create_plan_file_tool" in written
    assert "ok" in written
    assert "failed" in written
    assert "error=plan is locked" in written


def test_a_field_cannot_forge_a_second_line(tmp_path: Path, monkeypatch) -> None:
    """Names come from an agent, so they are not ours to trust.

    A newline in one would let whoever supplied it append a line of its own
    choosing — the same hole the control plane's log line had.
    """
    monkeypatch.setenv(observe.LOG_PATH_ENV, str(tmp_path / "mcp.log"))
    observe.tool_call("t", ms=1, ok=True, name="a\nfake ok tool=something_else")

    written = (tmp_path / "mcp.log").read_text(encoding="utf-8")
    assert len([line for line in written.splitlines() if line.strip()]) == 1


def test_a_log_that_cannot_be_written_does_not_fail_the_call(tmp_path: Path, monkeypatch) -> None:
    """An agent that could not create a plan because a log path was unusable
    would be a worse tool than one that did not log at all.

    The path here has a *file* where its parent directory should be, which
    is the realistic version: somebody's `~/.flanner` is a file, or a
    directory got replaced. An earlier draft used a null byte and tested
    nothing — `monkeypatch.setenv` refuses to hold one, so the failure was
    in the test rather than anywhere near the code.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("i am a file")
    monkeypatch.setenv(observe.LOG_PATH_ENV, str(blocker / "mcp.log"))

    observe.tool_call("t", ms=1, ok=True)  # must not raise


def test_the_plan_body_never_reaches_the_log(tmp_path: Path, monkeypatch) -> None:
    """The rule the whole module exists to keep.

    Driven through the real MCP tool rather than through `tool_call`, so it
    tests the allowlist in `server._loggable` rather than a promise about it.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FLANNER_HOME", str(home))
    monkeypatch.delenv(observe.LOG_PATH_ENV, raising=False)

    from flanner import server
    from flanner.database import init_database

    init_database(str(home / "data.db"))
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)  # noqa: S607

    project = server.create_project_tool(
        name="demo", project_root=str(root), plan_directory=".plans"
    )
    server.create_plan_file_tool(
        project_id=project["id"], name="architecture", content=f"# design\n\n{SECRET}"
    )

    written = (home / "mcp.log").read_text(encoding="utf-8")
    assert "tool=create_plan_file_tool" in written, "the call was not recorded at all"
    assert SECRET not in written
    assert "design" not in written


def test_a_tool_that_returns_an_error_dict_counts_as_failed(tmp_path: Path, monkeypatch) -> None:
    """The case that made this worth building.

    Thirty tools return `{"error": ...}` rather than raising. An agent has to
    work around those exactly as it would an exception, so a log that only
    noticed exceptions would miss every one of them.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FLANNER_HOME", str(home))
    monkeypatch.delenv(observe.LOG_PATH_ENV, raising=False)

    from flanner import server
    from flanner.database import init_database

    init_database(str(home / "data.db"))
    server.create_plan_file_tool(project_id="not-a-uuid", name="x", content="y")

    written = (home / "mcp.log").read_text(encoding="utf-8")
    assert "failed" in written
    assert "Invalid UUID" in written
