"""Unit tests for flanner.frontmatter."""

from datetime import datetime
from uuid import uuid4

from flanner.frontmatter import (
    create_plan_file_content,
    extract_metadata_from_frontmatter,
    generate_frontmatter,
    increment_version_in_frontmatter,
    is_mcp_plan_file,
    parse_frontmatter,
    update_frontmatter,
    validate_frontmatter,
)


def _make_content(version=1):
    fm = generate_frontmatter(
        project_id=uuid4(),
        project_name="proj",
        plan_file_id=uuid4(),
        plan_name="myplan",
        version=version,
        created_by="user",
        created_at=datetime(2024, 1, 1),
    )
    return create_plan_file_content(fm, "# My Plan\n\nBody.\n")


def test_generate_and_parse_roundtrip():
    content = _make_content()
    fm_data, body = parse_frontmatter(content)
    assert fm_data["mcp_plan_file"] is True
    assert fm_data["plan_name"] == "myplan"
    assert fm_data["version"] == 1
    assert fm_data["created_by"] == "user"
    assert "# My Plan" in body


def test_generate_frontmatter_default_created_at():
    fm = generate_frontmatter(
        project_id=uuid4(),
        project_name="p",
        plan_file_id=uuid4(),
        plan_name="n",
        version=1,
        created_by="user",
    )
    assert fm.startswith("---\n")
    assert fm.endswith("---")


def test_parse_frontmatter_no_frontmatter():
    fm_data, body = parse_frontmatter("just plain markdown")
    assert fm_data == {}
    assert body == "just plain markdown"


def test_parse_frontmatter_malformed_yaml():
    content = "---\nfoo: [unclosed\n---\nbody"
    fm_data, body = parse_frontmatter(content)
    assert fm_data == {}
    assert body == content


def test_validate_frontmatter():
    fm_data, _ = parse_frontmatter(_make_content())
    assert validate_frontmatter(fm_data) is True

    missing = dict(fm_data)
    del missing["plan_file_id"]
    assert validate_frontmatter(missing) is False

    not_plan = dict(fm_data)
    not_plan["mcp_plan_file"] = False
    assert validate_frontmatter(not_plan) is False


def test_is_mcp_plan_file():
    assert is_mcp_plan_file(_make_content()) is True
    assert is_mcp_plan_file("# Regular markdown") is False


def test_update_frontmatter():
    updated = update_frontmatter(_make_content(), {"created_by": "codex", "extra": 1})
    fm_data, _ = parse_frontmatter(updated)
    assert fm_data["created_by"] == "codex"
    assert fm_data["extra"] == 1


def test_create_plan_file_content_adds_newline():
    result = create_plan_file_content("---\na: 1\n---", "body")
    assert result == "---\na: 1\n---\n\nbody"
    # Already-newline-terminated frontmatter is not doubled
    result2 = create_plan_file_content("---\na: 1\n---\n", "body")
    assert result2 == "---\na: 1\n---\n\nbody"


def test_extract_metadata_from_frontmatter():
    fm_data, _ = parse_frontmatter(_make_content())
    meta = extract_metadata_from_frontmatter(fm_data)
    assert meta["plan_name"] == "myplan"
    assert meta["version"] == 1
    assert meta["plan_manager_version"] == "1.0"
    # Missing keys come back as None / default
    empty = extract_metadata_from_frontmatter({})
    assert empty["project_id"] is None
    assert empty["plan_manager_version"] == "1.0"


def test_increment_version_in_frontmatter():
    bumped = increment_version_in_frontmatter(_make_content(version=3))
    fm_data, _ = parse_frontmatter(bumped)
    assert fm_data["version"] == 4

    # No version key: starts at 1
    bumped = increment_version_in_frontmatter("no frontmatter body")
    fm_data, body = parse_frontmatter(bumped)
    assert fm_data["version"] == 1
    assert "no frontmatter body" in body
