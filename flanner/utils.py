"""
Utility functions for Flanner
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path


def hash_content(content: str) -> str:
    """
    Generate SHA256 hash of content.

    Args:
        content: Content to hash

    Returns:
        Hexadecimal hash string
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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


def format_relative_time(dt: datetime) -> str:
    """
    Format datetime as relative time (e.g., "2 hours ago").

    Args:
        dt: Datetime object

    Returns:
        Relative time string
    """
    now = datetime.utcnow()
    diff = now - dt

    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    elif seconds < 31536000:
        months = int(seconds / 2592000)
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = int(seconds / 31536000)
        return f"{years} year{'s' if years != 1 else ''} ago"


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
        size = Path(file_path).stat().st_size

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
        Filename (e.g., "architecture_v2.md")
    """
    sanitized = sanitize_filename(plan_name)
    return f"{sanitized}_v{version}.md"
