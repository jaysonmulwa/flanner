"""
Storage layer for Flanner

Handles file system operations for plan files.
"""

import logging
import os
from pathlib import Path
from typing import Any

from .exceptions import PlanFileNotFoundError
from .frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


def init_storage(base_path: str) -> None:
    """
    Initialize storage directory structure.

    Args:
        base_path: Base path for Flanner (e.g., ~/.flanner)
    """
    base = Path(base_path)
    base.mkdir(parents=True, exist_ok=True)

    logger.info("Storage initialized at: %s", base_path)


def save_plan_file_with_frontmatter(
    project_root: str, plan_directory: str, file_name: str, content: str
) -> str:
    """
    Save plan file to project's plan directory.

    Args:
        project_root: Absolute path to project root
        plan_directory: Relative path for plan files (e.g., ".plans")
        file_name: File name (e.g., "architecture_v1.md")
        content: Complete content including frontmatter

    Returns:
        Absolute path to saved file
    """
    # Construct full path
    full_plan_path = Path(project_root) / plan_directory
    full_plan_path.mkdir(parents=True, exist_ok=True)

    file_path = full_plan_path / file_name

    # Normalize to LF and write without OS newline translation. Browsers submit
    # textarea content as CRLF; text-mode writing on Windows would translate the
    # LF again, yielding CRLF-CR (\r\r\n). On the next read universal-newlines
    # turns that into an extra blank line, so the file degrades on every edit.
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    with open(file_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(normalized)

    return str(file_path)


def load_plan_file(file_path: str) -> tuple[dict[str, Any], str]:
    """
    Load plan file and return frontmatter and content.

    Args:
        file_path: Absolute path to plan file

    Returns:
        Tuple of (frontmatter_dict, content_body)

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    if not os.path.exists(file_path):
        raise PlanFileNotFoundError(f"Plan file not found: {file_path}")

    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    return parse_frontmatter(content)


def load_plan_file_full(file_path: str) -> str:
    """
    Load complete plan file content.

    Args:
        file_path: Absolute path to plan file

    Returns:
        Complete file content (frontmatter + body)

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    if not os.path.exists(file_path):
        raise PlanFileNotFoundError(f"Plan file not found: {file_path}")

    with open(file_path, encoding="utf-8") as f:
        return f.read()


def generate_file_path(project_root: str, plan_directory: str, file_name: str) -> str:
    """
    Generate absolute path for a plan file.

    Args:
        project_root: Absolute path to project root
        plan_directory: Relative path for plan files
        file_name: File name

    Returns:
        Absolute path to file
    """
    return str(Path(project_root) / plan_directory / file_name)


def delete_plan_file(file_path: str) -> bool:
    """
    Delete a plan file.

    Args:
        file_path: Absolute path to file

    Returns:
        True if deleted, False if file didn't exist
    """
    if not os.path.exists(file_path):
        return False

    os.remove(file_path)
    return True


def list_plan_files_in_directory(directory: str) -> list[str]:
    """
    List all markdown files in a directory.

    Args:
        directory: Path to directory

    Returns:
        List of filenames
    """
    if not os.path.exists(directory):
        return []

    path = Path(directory)
    return [f.name for f in path.glob("*.md")]


def get_file_stats(file_path: str) -> dict[str, Any] | None:
    """
    Get file statistics.

    Args:
        file_path: Path to file

    Returns:
        Dictionary with file stats or None if file doesn't exist
    """
    if not os.path.exists(file_path):
        return None

    stat = os.stat(file_path)

    return {
        "size": stat.st_size,
        "created": stat.st_ctime,
        "modified": stat.st_mtime,
        "accessed": stat.st_atime,
    }


def backup_plan_file(file_path: str, backup_suffix: str = ".backup") -> str:
    """
    Create a backup copy of a plan file.

    Args:
        file_path: Path to original file
        backup_suffix: Suffix for backup file

    Returns:
        Path to backup file

    Raises:
        FileNotFoundError: If original file doesn't exist
    """
    if not os.path.exists(file_path):
        raise PlanFileNotFoundError(f"File not found: {file_path}")

    backup_path = file_path + backup_suffix

    # Read original
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    # Write backup
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(content)

    return backup_path


def ensure_plan_directory_exists(project_root: str, plan_directory: str) -> str:
    """
    Ensure plan directory exists within project root.

    Args:
        project_root: Absolute path to project root
        plan_directory: Relative path for plan directory

    Returns:
        Absolute path to plan directory
    """
    full_path = Path(project_root) / plan_directory
    full_path.mkdir(parents=True, exist_ok=True)
    return str(full_path)


def move_plan_file(old_path: str, new_path: str) -> bool:
    """
    Move a plan file to a new location.

    Args:
        old_path: Current file path
        new_path: New file path

    Returns:
        True if successful

    Raises:
        FileNotFoundError: If old file doesn't exist
    """
    if not os.path.exists(old_path):
        raise PlanFileNotFoundError(f"File not found: {old_path}")

    # Ensure destination directory exists
    dest_dir = Path(new_path).parent
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Move file
    os.rename(old_path, new_path)
    return True
