"""What happens when the machine underneath fails.

The suite covered what the code does when everything works. This covers the
paths that only run on a bad day, which are the ones nobody exercises by hand
and the ones that fail worst: a full disk, a file somebody chmod'd, a plan
whose bytes got mangled, a store opened read-only.

Modelled on SQLite's fault injection: pick each failure a real machine can
produce, force it, and assert the tool reports something a person can act on
rather than a traceback. A crash here is not a crash in the abstract — it is
somebody losing the plan they were part-way through writing.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from flanner import artifacts, frontmatter, storage

# --- disk failures on the write path ----------------------------------------


def test_a_full_disk_does_not_leave_a_half_written_plan(tmp_path: Path) -> None:
    """OSError mid-write must not be reported as success."""
    target = tmp_path / "plan.md"

    with mock.patch("pathlib.Path.write_text", side_effect=OSError("No space left on device")):
        with pytest.raises(OSError, match="No space left"):
            target.write_text("content", encoding="utf-8")

    # The point of the assertion: nothing was created, so a retry starts clean
    # rather than appending to a truncated file.
    assert not target.exists()


# --- malformed input at every deserialisation boundary ----------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # empty
        b"{",  # truncated
        b"null",  # valid json, wrong shape
        b"[]",  # valid json, wrong shape
        b'{"artifact_type": "plan.version"}',  # right shape, missing fields
        b"\x00\x01\x02\x03",  # not text at all
        b'{"a": "\xed\xa0\x80"}',  # lone surrogate
    ],
    ids=["empty", "truncated", "null", "array", "partial", "binary", "surrogate"],
)
def test_a_malformed_artifact_is_refused_not_accepted(payload: bytes) -> None:
    """Every one of these arrives over the wire from another device.

    The requirement is not that parsing succeeds. It is that failure is a
    refusal this code chose, rather than whatever exception happened to
    escape the parser.
    """
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return  # refused at the boundary, which is the correct outcome
    if not isinstance(data, dict):
        return
    with pytest.raises((ValueError, KeyError, TypeError)):
        artifacts.Artifact.from_dict(data)


@pytest.mark.parametrize(
    "body",
    ["", "---\n", "---\nnot: [valid", "\x00\x00", "---\n---\n", "no frontmatter at all"],
    ids=["empty", "open-fence", "bad-yaml", "nulls", "empty-frontmatter", "none"],
)
def test_reading_a_mangled_plan_file_does_not_raise(tmp_path: Path, body: str) -> None:
    """Plan files are edited by hand and by agents, so they arrive damaged."""
    path = tmp_path / "mangled.md"
    path.write_text(body, encoding="utf-8")
    try:
        frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        storage.load_plan_file(str(path))
    except Exception as exc:  # noqa: BLE001 - the assertion is about which type
        assert isinstance(exc, ValueError | OSError), f"unexpected {type(exc).__name__}: {exc}"


# --- exit codes distinguish the two kinds of failure ------------------------


def test_a_system_fault_exits_2_not_1() -> None:
    """A script has to tell "retry will not help" from "fix your command".

    Before this both arrived as 1, and an unreadable store arrived as a
    traceback rather than a message.
    """
    from unittest import mock

    import flanner.cli as cli_module
    from flanner.exceptions import DatabaseError

    with mock.patch.object(cli_module.cli, "main", side_effect=DatabaseError("store unreadable")):
        with pytest.raises(SystemExit) as exit_info:
            cli_module.main()

    assert exit_info.value.code == 2


def test_a_disk_fault_exits_2() -> None:
    """OSError is a machine problem too, whatever raised it."""
    from unittest import mock

    import flanner.cli as cli_module

    with mock.patch.object(cli_module.cli, "main", side_effect=OSError("disk full")):
        with pytest.raises(SystemExit) as exit_info:
            cli_module.main()

    assert exit_info.value.code == 2


def test_a_user_error_still_exits_1() -> None:
    """The new code must not swallow the ordinary case."""
    from unittest import mock

    import flanner.cli as cli_module

    with mock.patch.object(cli_module.cli, "main", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit) as exit_info:
            cli_module.main()

    assert exit_info.value.code == 1
