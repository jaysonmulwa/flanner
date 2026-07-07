"""Unit tests for flanner.utils."""

from datetime import datetime, timedelta

from flanner.utils import (
    count_words,
    ensure_directory_exists,
    extract_markdown_title,
    format_datetime,
    format_relative_time,
    generate_file_name,
    get_file_size_formatted,
    hash_content,
    sanitize_filename,
    truncate_string,
    validate_path,
)


def test_hash_content_deterministic():
    assert hash_content("abc") == hash_content("abc")
    assert hash_content("abc") != hash_content("abd")
    assert len(hash_content("")) == 64


def test_sanitize_filename():
    assert sanitize_filename("my plan") == "my_plan"
    assert sanitize_filename('a<>:"/\\|?*b') == "ab"
    assert sanitize_filename("..name..") == "name"
    assert sanitize_filename("???") == "unnamed"
    assert sanitize_filename("") == "unnamed"


def test_validate_path():
    assert validate_path("plans/foo.md") is True
    assert validate_path("../etc/passwd") is False
    assert validate_path("/absolute/path") is False
    assert validate_path("C:\\windows") is False


def test_format_datetime():
    assert format_datetime(None) == "N/A"
    assert format_datetime(datetime(2024, 1, 2, 3, 4, 5)) == "2024-01-02 03:04:05"
    assert format_datetime(datetime(2024, 1, 2), "%Y") == "2024"


def test_format_relative_time_buckets():
    now = datetime.utcnow()
    assert format_relative_time(now) == "just now"
    assert format_relative_time(now - timedelta(minutes=1)) == "1 minute ago"
    assert format_relative_time(now - timedelta(minutes=5)) == "5 minutes ago"
    assert format_relative_time(now - timedelta(hours=1, minutes=5)) == "1 hour ago"
    assert format_relative_time(now - timedelta(hours=3)) == "3 hours ago"
    assert format_relative_time(now - timedelta(days=1, hours=1)) == "1 day ago"
    assert format_relative_time(now - timedelta(days=8)) == "1 week ago"
    assert format_relative_time(now - timedelta(days=40)) == "1 month ago"
    assert format_relative_time(now - timedelta(days=400)) == "1 year ago"
    assert format_relative_time(now - timedelta(days=800)) == "2 years ago"


def test_ensure_directory_exists(tmp_path):
    target = tmp_path / "a" / "b"
    ensure_directory_exists(str(target))
    assert target.is_dir()
    # Idempotent
    ensure_directory_exists(str(target))


def test_get_file_size_formatted(tmp_path):
    small = tmp_path / "small.txt"
    small.write_text("x" * 10)
    assert get_file_size_formatted(str(small)) == "10.0 B"

    big = tmp_path / "big.txt"
    big.write_text("x" * 2048)
    assert get_file_size_formatted(str(big)) == "2.0 KB"

    assert get_file_size_formatted(str(tmp_path / "missing.txt")) == "Unknown"


def test_truncate_string():
    assert truncate_string("short") == "short"
    assert truncate_string("abcdefghij", max_length=5) == "ab..."
    assert truncate_string("abcde", max_length=5) == "abcde"


def test_extract_markdown_title():
    assert extract_markdown_title("# Title\n\nBody") == "Title"
    assert extract_markdown_title("text\n## Sub\n# Real") == "Real"
    assert extract_markdown_title("no header here") is None


def test_count_words():
    assert count_words("") == 0
    assert count_words("one two  three") == 3


def test_generate_file_name():
    assert generate_file_name("my plan", 2) == "my_plan_v2.md"
