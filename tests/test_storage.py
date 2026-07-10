"""Unit tests for flanner.storage."""

import pytest

from flanner.exceptions import PlanFileNotFoundError
from flanner.storage import (
    backup_plan_file,
    delete_plan_file,
    ensure_plan_directory_exists,
    generate_file_path,
    get_file_stats,
    init_storage,
    list_plan_files_in_directory,
    load_plan_file,
    load_plan_file_full,
    move_plan_file,
    save_plan_file_with_frontmatter,
)

CONTENT = "---\nmcp_plan_file: true\nversion: 1\n---\n\n# Body\n"


def test_init_storage(tmp_path):
    base = tmp_path / "home"
    init_storage(str(base))
    assert base.is_dir()


def test_save_and_load_plan_file(tmp_path):
    path = save_plan_file_with_frontmatter(str(tmp_path), ".plans", "p_v1.md", CONTENT)
    assert (tmp_path / ".plans" / "p_v1.md").exists()

    fm_data, body = load_plan_file(path)
    assert fm_data["mcp_plan_file"] is True
    assert "# Body" in body

    assert load_plan_file_full(path) == CONTENT


def test_save_normalizes_crlf_to_lf(tmp_path):
    # Browser form submissions arrive as CRLF; the file must end up LF-only so it
    # does not gain a stray CR (and a blank line) on every round-trip.
    crlf = "---\r\nmcp_plan_file: true\r\n---\r\n\r\n# Body\r\n\r\ntext\r\n"
    path = save_plan_file_with_frontmatter(str(tmp_path), ".plans", "p_v1.md", crlf)
    raw = open(path, "rb").read()
    assert b"\r" not in raw  # no carriage returns at all
    # Re-saving the same CRLF content is byte-stable (idempotent).
    save_plan_file_with_frontmatter(str(tmp_path), ".plans", "p_v1.md", crlf)
    assert open(path, "rb").read() == raw


def test_load_plan_file_missing_raises(tmp_path):
    missing = str(tmp_path / "nope.md")
    with pytest.raises(PlanFileNotFoundError):
        load_plan_file(missing)
    with pytest.raises(PlanFileNotFoundError):
        load_plan_file_full(missing)


def test_generate_file_path(tmp_path):
    path = generate_file_path(str(tmp_path), ".plans", "a.md")
    assert path.replace("\\", "/").endswith(".plans/a.md")


def test_delete_plan_file(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x")
    assert delete_plan_file(str(f)) is True
    assert not f.exists()
    assert delete_plan_file(str(f)) is False


def test_list_plan_files_in_directory(tmp_path):
    assert list_plan_files_in_directory(str(tmp_path / "missing")) == []
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.md").write_text("x")
    (tmp_path / "c.txt").write_text("x")
    assert sorted(list_plan_files_in_directory(str(tmp_path))) == ["a.md", "b.md"]


def test_get_file_stats(tmp_path):
    assert get_file_stats(str(tmp_path / "missing.md")) is None
    f = tmp_path / "a.md"
    f.write_text("hello")
    stats = get_file_stats(str(f))
    assert stats["size"] == 5
    assert "modified" in stats


def test_backup_plan_file(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("original")
    backup = backup_plan_file(str(f))
    assert backup == str(f) + ".backup"
    assert (tmp_path / "a.md.backup").read_text() == "original"

    with pytest.raises(PlanFileNotFoundError):
        backup_plan_file(str(tmp_path / "missing.md"))


def test_ensure_plan_directory_exists(tmp_path):
    result = ensure_plan_directory_exists(str(tmp_path), ".plans")
    assert (tmp_path / ".plans").is_dir()
    assert result.replace("\\", "/").endswith(".plans")


def test_move_plan_file(tmp_path):
    src = tmp_path / "a.md"
    src.write_text("content")
    dest = tmp_path / "sub" / "b.md"
    assert move_plan_file(str(src), str(dest)) is True
    assert not src.exists()
    assert dest.read_text() == "content"

    with pytest.raises(PlanFileNotFoundError):
        move_plan_file(str(tmp_path / "missing.md"), str(dest))
