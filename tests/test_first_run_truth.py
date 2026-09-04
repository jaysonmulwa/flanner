"""What the first few commands tell a new person, and whether it is true.

`flanner status` read Claude Desktop's config file and reported the result
as "Claude Code". They are different programs with different files, and
nothing `init` writes for Claude Code lives in the one being checked — so a
correct setup read as "not registered", on the command somebody runs to
find out whether it worked. Codex was not mentioned at all, though `init`
hands it an AGENTS.md block that talks about MCP tools it has no way to
reach until a file it never heard of is edited.

And `init` died with "Aborted!" when nothing was attached to stdin, which
made the very first command unusable from a script.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import flanner.claude_integration as ci
from flanner.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def desktop(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(ci, "get_claude_config_path", lambda: path)
    return path


# --- init without a terminal -----------------------------------------------


def test_init_with_nothing_on_stdin_takes_the_offered_name(runner, git_repo) -> None:
    """A script, CI, `< /dev/null`: the default is the answer, not "Aborted!"."""
    result = runner.invoke(cli, ["init", "--skip-claude", "--project-root", str(git_repo)])

    assert result.exit_code == 0, result.output
    assert "No terminal to ask" in result.output
    listed = runner.invoke(cli, ["list", "--output", "json"])
    assert [p["name"] for p in json.loads(listed.output)] == [git_repo.name]


def test_init_still_takes_a_typed_name(runner, git_repo) -> None:
    result = runner.invoke(
        cli, ["init", "--skip-claude", "--project-root", str(git_repo)], input="typed\n"
    )

    assert result.exit_code == 0, result.output
    listed = runner.invoke(cli, ["list", "--output", "json"])
    assert [p["name"] for p in json.loads(listed.output)] == ["typed"]


# --- status, one row per agent ----------------------------------------------


def _rows(output: str) -> dict[str, str]:
    """The status table as {label: rest of line}, whitespace collapsed."""
    rows: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("  ", 1)
        if len(parts) == 2 and parts[0].strip():
            rows[parts[0].strip()] = " ".join(parts[1].split())
    return rows


def test_status_names_each_agent_and_does_not_call_desktop_claude_code(runner, desktop) -> None:
    result = runner.invoke(cli, ["status"])

    rows = _rows(result.output)
    assert "Claude Desktop" in rows
    assert "Claude Code" in rows
    assert "Codex" in rows
    assert "not registered" in rows["Claude Code"]


def test_status_sees_a_project_mcp_json_where_claude_code_would(
    runner, desktop, git_repo, monkeypatch
) -> None:
    """The file `init` writes, found by walking up as Claude Code does."""
    (git_repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"flanner": {"command": "flanner-mcp", "args": []}}}),
        encoding="utf-8",
    )
    nested = git_repo / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    rows = _rows(runner.invoke(cli, ["status"]).output)

    assert "registered" in rows["Claude Code"] and "not registered" not in rows["Claude Code"]
    assert ".mcp.json" in rows["Claude Code"]


def test_status_sees_a_user_scope_registration(runner, desktop) -> None:
    """What `claude mcp add -s user` leaves behind."""
    ci.claude_code_user_config_path().write_text(
        json.dumps({"mcpServers": {"flanner": {"command": "flanner-mcp"}}}), encoding="utf-8"
    )

    rows = _rows(runner.invoke(cli, ["status"]).output)

    assert "user scope" in rows["Claude Code"]


def test_status_does_not_mistake_desktop_registration_for_claude_code(runner, desktop) -> None:
    """The exact confusion this replaces, kept as a test so it stays gone."""
    ci.register_mcp_server()

    rows = _rows(runner.invoke(cli, ["status"]).output)

    assert (
        "registered" in rows["Claude Desktop"] and "not registered" not in rows["Claude Desktop"]
    )
    assert "not registered" in rows["Claude Code"]


def test_status_reads_codex_config(runner, desktop) -> None:
    path = ci.codex_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[mcp_servers.other]\ncommand = "x"\n', encoding="utf-8")
    assert "not registered" in _rows(runner.invoke(cli, ["status"]).output)["Codex"]

    path.write_text('[mcp_servers.flanner]\ncommand = "flanner-mcp"\n', encoding="utf-8")
    assert "not registered" not in _rows(runner.invoke(cli, ["status"]).output)["Codex"]


# --- setup says the Codex step out loud ---------------------------------------


def test_setup_prints_the_codex_lines_it_cannot_write(runner, desktop, monkeypatch) -> None:
    """No TOML writer on 3.10, and editing somebody's editor config by hand
    is how the .mcp.json clobbering happened. So the step is shown."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)  # no `claude` binary here

    result = runner.invoke(cli, ["setup"])

    assert "Codex: not registered" in result.output
    assert "[mcp_servers.flanner]" in result.output
    # Whitespace collapsed on both sides: Rich wraps a long temp path.
    said = "".join(result.output.split())
    assert "".join(str(ci.codex_config_path()).split()) in said


def test_a_registration_file_that_is_not_json_reads_as_not_registered(desktop, tmp_path) -> None:
    """Never a traceback from a status command."""
    ci.claude_code_user_config_path().write_text("{not json", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text("{not json either", encoding="utf-8")

    assert ci.claude_code_registration(tmp_path) == ""
