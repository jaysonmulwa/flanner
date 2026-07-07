"""
Git integration for Flanner

Provides git repository detection and .gitignore management.
"""

import os
import subprocess
from pathlib import Path


def find_git_root(start_path: str | None = None) -> str | None:
    """
    Find the git repository root by looking for .git directory.

    Args:
        start_path: Starting path to search from (defaults to current directory)

    Returns:
        Absolute path to git root, or None if not found
    """
    if start_path is None:
        start_path = os.getcwd()

    current_path = Path(start_path).resolve()

    # Traverse up the directory tree
    while True:
        git_dir = current_path / ".git"
        if git_dir.exists() and git_dir.is_dir():
            return str(current_path)

        # Check if we've reached the root
        parent = current_path.parent
        if parent == current_path:
            # Reached filesystem root without finding .git
            return None

        current_path = parent


def validate_git_repo(path: str) -> bool:
    """
    Validate that a path is a git repository.

    Args:
        path: Path to check

    Returns:
        True if path is a git repository, False otherwise
    """
    git_dir = Path(path) / ".git"
    return git_dir.exists() and git_dir.is_dir()


def get_gitignore_path(repo_root: str) -> str:
    """
    Get the path to .gitignore file.

    Args:
        repo_root: Path to git repository root

    Returns:
        Absolute path to .gitignore
    """
    return str(Path(repo_root) / ".gitignore")


def read_gitignore(repo_root: str) -> list[str]:
    """
    Read .gitignore file and return lines.

    Args:
        repo_root: Path to git repository root

    Returns:
        List of lines from .gitignore (empty list if file doesn't exist)
    """
    gitignore_path = get_gitignore_path(repo_root)

    if not os.path.exists(gitignore_path):
        return []

    with open(gitignore_path, encoding="utf-8") as f:
        return f.readlines()


def is_pattern_in_gitignore(repo_root: str, pattern: str) -> bool:
    """
    Check if a pattern is already in .gitignore.

    Args:
        repo_root: Path to git repository root
        pattern: Pattern to check

    Returns:
        True if pattern is already in .gitignore
    """
    lines = read_gitignore(repo_root)

    # Normalize pattern (remove trailing slashes for comparison)
    normalized_pattern = pattern.rstrip("/")

    for line in lines:
        line = line.strip()
        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        # Check if this line matches the pattern
        normalized_line = line.rstrip("/")
        if normalized_line == normalized_pattern:
            return True

    return False


def update_gitignore(repo_root: str, pattern: str, comment: str | None = None) -> bool:
    """
    Add a pattern to .gitignore if it's not already there.

    Args:
        repo_root: Path to git repository root
        pattern: Pattern to add (e.g., ".plans/" or "*.log")
        comment: Optional comment to add above the pattern

    Returns:
        True if pattern was added, False if it was already there
    """
    # Check if pattern already exists
    if is_pattern_in_gitignore(repo_root, pattern):
        return False

    gitignore_path = get_gitignore_path(repo_root)

    # Read existing content
    if os.path.exists(gitignore_path):
        with open(gitignore_path, encoding="utf-8") as f:
            content = f.read()
    else:
        content = ""

    # Ensure file ends with newline
    if content and not content.endswith("\n"):
        content += "\n"

    # Add section separator if file is not empty
    if content:
        content += "\n"

    # Add comment if provided
    if comment:
        content += f"# {comment}\n"
    else:
        content += "# MCP Plan Manager - Auto-generated\n"

    # Add pattern
    content += f"{pattern}\n"

    # Write back
    with open(gitignore_path, "w", encoding="utf-8") as f:
        f.write(content)

    return True


def remove_from_gitignore(repo_root: str, pattern: str) -> bool:
    """
    Remove a pattern from .gitignore.

    Args:
        repo_root: Path to git repository root
        pattern: Pattern to remove

    Returns:
        True if pattern was removed, False if it wasn't found
    """
    gitignore_path = get_gitignore_path(repo_root)

    if not os.path.exists(gitignore_path):
        return False

    lines = read_gitignore(repo_root)

    # Normalize pattern
    normalized_pattern = pattern.rstrip("/")

    # Filter out the pattern
    new_lines = []
    removed = False

    for line in lines:
        stripped = line.strip()
        normalized_line = stripped.rstrip("/")

        # Keep line if it's not the pattern
        if normalized_line != normalized_pattern:
            new_lines.append(line)
        else:
            removed = True

    if not removed:
        return False

    # Write back
    with open(gitignore_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return True


def is_path_ignored(repo_root: str, path: str) -> bool:
    """
    Check if a path is ignored by git.

    Args:
        repo_root: Path to git repository root
        path: Relative path to check

    Returns:
        True if path is ignored, False otherwise
    """
    try:
        # Use git check-ignore command
        result = subprocess.run(  # noqa: S603,S607 - fixed git command, no user input in argv[0]
            ["git", "check-ignore", path],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Exit code 0 means path is ignored
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        # git missing, timed out, or failed to spawn
        # Fallback: check .gitignore file manually
        return _check_gitignore_manually(repo_root, path)


def _check_gitignore_manually(repo_root: str, path: str) -> bool:
    """
    Manually check if path matches any .gitignore patterns.

    This is a simple fallback when git command is not available.

    Args:
        repo_root: Path to git repository root
        path: Relative path to check

    Returns:
        True if path appears to be ignored
    """
    lines = read_gitignore(repo_root)

    # Normalize path
    path = path.strip("/")

    for line in lines:
        pattern = line.strip()

        # Skip comments and empty lines
        if not pattern or pattern.startswith("#"):
            continue

        # Simple pattern matching (not full gitignore spec)
        pattern = pattern.rstrip("/")

        # Exact match
        if pattern == path:
            return True

        # Directory prefix match
        if pattern.endswith("/") and path.startswith(pattern):
            return True

        # Path starts with pattern
        if path.startswith(pattern + "/"):
            return True

    return False


def get_git_status() -> str | None:
    """
    Get git status of current repository.

    Returns:
        Git status output or None if not in a git repository
    """
    try:
        result = subprocess.run(  # noqa: S603,S607 - fixed git command, no user input in argv[0]
            ["git", "status", "--short"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return None


def update_plan_directory_in_gitignore(repo_root: str, old_pattern: str, new_pattern: str) -> bool:
    """
    Update plan directory pattern in .gitignore.

    Args:
        repo_root: Path to git repository root
        old_pattern: Old pattern to remove
        new_pattern: New pattern to add

    Returns:
        True if successful
    """
    # Remove old pattern
    if old_pattern and is_pattern_in_gitignore(repo_root, old_pattern):
        remove_from_gitignore(repo_root, old_pattern)

    # Add new pattern
    return update_gitignore(repo_root, new_pattern, comment="MCP Plan Manager")
