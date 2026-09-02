"""
Utility functions for Flanner
"""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> datetime:
    """Current UTC time, returned naive to match the SQLite DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_newlines(content: str) -> str:
    """Line endings as LF, whatever the sender used.

    A browser submits textarea content with CRLF regardless of platform, so
    text that came back untouched from an editor is not byte-identical to
    the text that went in. Plans are stored LF-only, so this is what "the
    same content" has to mean.
    """
    return content.replace("\r\n", "\n").replace("\r", "\n")


def hash_content(content: str) -> str:
    """SHA256 of a plan body, over its normalised form.

    Normalising here rather than at each call site is deliberate. This hash
    decides whether saving creates a new version, and ``storage`` writes the
    normalised text. Hashing the raw input meant a save from the browser
    never matched the version it came from, so every save through the editor
    produced an identical new version.

    Only version rows use this. Artifact ids are hashed separately in
    ``artifacts``, over a signed payload, and are unaffected.

    Args:
        content: Content to hash

    Returns:
        Hexadecimal hash string
    """
    return hashlib.sha256(normalize_newlines(content).encode("utf-8")).hexdigest()


def sanitize_filename(name: str) -> str:
    """
    Sanitize a filename by removing/replacing invalid characters.

    Args:
        name: Original filename

    Returns:
        Sanitized filename
    """
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', "", name)

    # Replace spaces with underscores
    sanitized = sanitized.replace(" ", "_")

    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip(". ")

    # Ensure it's not empty
    if not sanitized:
        sanitized = "unnamed"

    return sanitized


def sanitize_plan_path(name: str) -> str:
    """Sanitize a plan name that may address a subdirectory of the plan dir.

    A plan name like ``auth/login-flow`` maps to ``<plan_dir>/auth/login-flow``.
    Backslashes are normalized to forward slashes, each path segment is run
    through :func:`sanitize_filename`, and empty/``.`` segments are dropped.
    Path traversal (any ``..`` segment) and absolute paths are rejected.

    Returns a POSIX-style relative path (no leading slash). Raises ValueError if
    the name is empty or attempts to escape the plan directory.
    """
    segments = [s for s in name.replace("\\", "/").split("/") if s not in ("", ".")]
    if not segments:
        raise ValueError(f"Invalid plan name: {name!r}")
    if any(s == ".." for s in segments):
        raise ValueError(f"Plan name must not contain '..' (path traversal): {name!r}")
    return "/".join(sanitize_filename(s) for s in segments)


def validate_path(path: str) -> bool:
    """
    Validate that a path doesn't contain directory traversal attempts.

    Args:
        path: Path to validate

    Returns:
        True if path is safe, False otherwise
    """
    # Check for directory traversal
    if ".." in path:
        return False

    # Check for absolute path indicators
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return False

    return True


def format_datetime(dt: datetime | None, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format datetime object as string.

    Args:
        dt: Datetime object (can be None)
        format_str: Format string

    Returns:
        Formatted datetime string or "N/A" if None
    """
    if dt is None:
        return "N/A"

    return dt.strftime(format_str)


# Largest first, so the first unit that yields a whole number wins. A month
# is 30 days and a year 365: this is for reading, not for arithmetic.
_RELATIVE_UNITS: tuple[tuple[int, str], ...] = (
    (31536000, "year"),
    (2592000, "month"),
    (604800, "week"),
    (86400, "day"),
    (3600, "hour"),
    (60, "minute"),
)


def format_relative_time(dt: datetime) -> str:
    """How long ago, in the largest unit that gives a whole number.

    Args:
        dt: the moment to describe, in UTC.

    Returns:
        A phrase like "2 hours ago", or "just now" under a minute.
    """
    seconds = (utcnow() - dt).total_seconds()
    for size, unit in _RELATIVE_UNITS:
        if seconds >= size:
            count = int(seconds / size)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"


def ensure_directory_exists(directory: str) -> None:
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        directory: Path to directory
    """
    Path(directory).mkdir(parents=True, exist_ok=True)


def get_file_size_formatted(file_path: str) -> str:
    """
    Get formatted file size.

    Args:
        file_path: Path to file

    Returns:
        Formatted file size (e.g., "1.5 KB")
    """
    try:
        size: float = Path(file_path).stat().st_size

        # Format size
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0

        return f"{size:.1f} TB"
    except OSError:
        return "Unknown"


def truncate_string(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length.

    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated string
    """
    if len(text) <= max_length:
        return text

    return text[: max_length - len(suffix)] + suffix


def extract_markdown_title(content: str) -> str | None:
    """
    Extract the first markdown header from content.

    Args:
        content: Markdown content

    Returns:
        Title string or None
    """
    # Look for # Header
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()

    return None


def count_words(text: str) -> int:
    """
    Count words in text.

    Args:
        text: Text to count

    Returns:
        Word count
    """
    return len(text.split())


def generate_file_name(plan_name: str, version: int) -> str:
    """
    Generate a versioned filename.

    Args:
        plan_name: Plan name
        version: Version number

    Returns:
        Filename (e.g., "architecture_v2.md", or "auth/login_v2.md" for a plan
        whose name addresses a subdirectory)
    """
    sanitized = sanitize_plan_path(plan_name)
    return f"{sanitized}_v{version}.md"
