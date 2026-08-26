"""
MCP Plan File Manager

A comprehensive plan file management system for Claude Code and AI assistants.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

try:
    __version__ = _installed_version("flanner")
except PackageNotFoundError:  # running from a source tree with nothing installed
    __version__ = "0.0.0+unknown"
