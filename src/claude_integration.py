"""
Claude Code Integration for Flanner

Handles automatic registration of MCP server with Claude Code.
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any
import platform


def get_claude_config_path() -> Optional[Path]:
    """
    Get the path to Claude Desktop configuration file.

    Returns:
        Path to claude_desktop_config.json or None if not found
    """
    system = platform.system()
    home = Path.home()

    # Possible Claude Desktop config locations
    possible_paths = []

    if system == "Windows":
        # Windows paths
        possible_paths = [
            home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json",
            home / ".claude" / "claude_desktop_config.json",
        ]
    elif system == "Darwin":  # macOS
        possible_paths = [
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            home / ".claude" / "claude_desktop_config.json",
        ]
    else:  # Linux
        possible_paths = [
            home / ".config" / "claude" / "claude_desktop_config.json",
            home / ".claude" / "claude_desktop_config.json",
        ]

    # Return first existing path
    for path in possible_paths:
        if path.exists():
            return path

    # Return default path (even if it doesn't exist yet)
    return possible_paths[0] if possible_paths else None


def read_claude_config() -> Dict[str, Any]:
    """
    Read Claude Code MCP settings.

    Returns:
        Dictionary of MCP settings, or empty dict with mcpServers key
    """
    config_path = get_claude_config_path()

    if not config_path or not config_path.exists():
        return {"mcpServers": {}}

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # Ensure mcpServers key exists
        if "mcpServers" not in config:
            config["mcpServers"] = {}

        return config
    except Exception as e:
        print(f"Warning: Could not read Claude config: {e}")
        return {"mcpServers": {}}


def write_claude_config(config: Dict[str, Any]) -> bool:
    """
    Write Claude Code MCP settings.

    Args:
        config: Configuration dictionary

    Returns:
        True if successful, False otherwise
    """
    config_path = get_claude_config_path()

    if not config_path:
        return False

    try:
        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Write config with pretty formatting
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

        return True
    except Exception as e:
        print(f"Error: Could not write Claude config: {e}")
        return False


def get_local_server_config() -> Dict[str, Any]:
    """
    Get the configuration for local MCP server.

    Returns:
        Server configuration dictionary
    """
    # Get absolute path to the project directory
    project_dir = Path(__file__).resolve().parent.parent

    return {
        "command": "python",
        "args": ["-m", "src.server"],
        "cwd": str(project_dir),
        "env": {
            # Add any environment variables if needed
        }
    }


def get_cloud_server_config(server_url: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Get the configuration for cloud-based MCP server (future use).

    Args:
        server_url: URL of the cloud MCP server
        api_key: Optional API key for authentication

    Returns:
        Server configuration dictionary
    """
    config = {
        "type": "cloud",
        "url": server_url,
    }

    if api_key:
        config["apiKey"] = api_key

    return config


def is_server_registered(server_name: str = "flanner") -> bool:
    """
    Check if MCP server is registered in Claude Code.

    Args:
        server_name: Name of the server to check

    Returns:
        True if server is registered, False otherwise
    """
    config = read_claude_config()
    return server_name in config.get("mcpServers", {})


def get_server_config_from_claude(server_name: str = "flanner") -> Optional[Dict[str, Any]]:
    """
    Get the current server configuration from Claude Code.

    Args:
        server_name: Name of the server

    Returns:
        Server configuration or None if not found
    """
    config = read_claude_config()
    return config.get("mcpServers", {}).get(server_name)


def register_mcp_server(
    server_name: str = "flanner",
    server_type: str = "local",
    server_url: Optional[str] = None,
    api_key: Optional[str] = None,
    force: bool = False
) -> tuple[bool, str]:
    """
    Register MCP server with Claude Code.

    Args:
        server_name: Name to register the server under
        server_type: "local" or "cloud"
        server_url: URL for cloud servers
        api_key: API key for cloud servers
        force: If True, overwrite existing configuration

    Returns:
        Tuple of (success: bool, message: str)
    """
    # Get current config
    config = read_claude_config()

    # Check if already registered
    if server_name in config["mcpServers"] and not force:
        return True, f"Server '{server_name}' is already registered in Claude Code"

    # Get server configuration based on type
    if server_type == "local":
        server_config = get_local_server_config()
    elif server_type == "cloud":
        if not server_url:
            return False, "Cloud server requires a URL"
        server_config = get_cloud_server_config(server_url, api_key)
    else:
        return False, f"Invalid server type: {server_type}"

    # Add server to config
    config["mcpServers"][server_name] = server_config

    # Write config
    if write_claude_config(config):
        action = "Updated" if force else "Registered"
        return True, f"{action} MCP server '{server_name}' in Claude Code"
    else:
        return False, "Failed to write Claude Code configuration"


def unregister_mcp_server(server_name: str = "flanner") -> tuple[bool, str]:
    """
    Unregister MCP server from Claude Code.

    Args:
        server_name: Name of the server to unregister

    Returns:
        Tuple of (success: bool, message: str)
    """
    config = read_claude_config()

    if server_name not in config.get("mcpServers", {}):
        return False, f"Server '{server_name}' is not registered in Claude Code"

    # Remove server
    del config["mcpServers"][server_name]

    # Write config
    if write_claude_config(config):
        return True, f"Unregistered MCP server '{server_name}' from Claude Code"
    else:
        return False, "Failed to write Claude Code configuration"


def verify_server_config() -> tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Verify that the registered MCP server configuration is correct.

    Returns:
        Tuple of (is_valid: bool, message: str, current_config: Optional[Dict])
    """
    server_name = "flanner"

    # Check if registered
    if not is_server_registered(server_name):
        return False, "MCP server is not registered in Claude Code", None

    # Get current config
    current_config = get_server_config_from_claude(server_name)
    expected_config = get_local_server_config()

    # Compare configurations
    if current_config == expected_config:
        return True, "MCP server configuration is correct", current_config
    else:
        return False, "MCP server configuration has changed", current_config


def get_claude_config_info() -> Dict[str, Any]:
    """
    Get information about Claude Code configuration.

    Returns:
        Dictionary with configuration information
    """
    config_path = get_claude_config_path()

    info = {
        "config_path": str(config_path) if config_path else "Not found",
        "config_exists": config_path.exists() if config_path else False,
        "server_registered": is_server_registered(),
        "total_servers": 0,
        "our_server_config": None
    }

    if config_path and config_path.exists():
        config = read_claude_config()
        info["total_servers"] = len(config.get("mcpServers", {}))
        info["our_server_config"] = config.get("mcpServers", {}).get("flanner")

    return info


def auto_register_on_init() -> tuple[bool, str]:
    """
    Automatically register MCP server on first init.
    This is called during 'mcp-plan init' command.

    Returns:
        Tuple of (success: bool, message: str)
    """
    # Check if already registered
    if is_server_registered():
        # Verify configuration is correct
        is_valid, msg, current_config = verify_server_config()

        if is_valid:
            return True, "MCP server already registered and up-to-date"
        else:
            # Configuration changed, update it
            success, update_msg = register_mcp_server(force=True)
            if success:
                return True, "MCP server configuration updated"
            else:
                return False, f"Failed to update configuration: {update_msg}"
    else:
        # Not registered, register it
        return register_mcp_server()


def check_server_status() -> Dict[str, Any]:
    """
    Check the status of MCP server registration.
    This is called during 'mcp-plan status' command.

    Returns:
        Dictionary with status information
    """
    status = {
        "registered": False,
        "config_valid": False,
        "config_path": None,
        "message": "",
        "action_needed": None
    }

    config_path = get_claude_config_path()
    status["config_path"] = str(config_path) if config_path else "Not found"

    if not config_path:
        status["message"] = "Claude Code configuration path not found"
        status["action_needed"] = "Please ensure Claude Code is installed"
        return status

    if not is_server_registered():
        status["message"] = "MCP server is not registered in Claude Code"
        status["action_needed"] = "Run: flanner init (or manually register)"
        return status

    status["registered"] = True

    # Verify configuration
    is_valid, msg, current_config = verify_server_config()
    status["config_valid"] = is_valid
    status["message"] = msg

    if not is_valid:
        status["action_needed"] = "Run: flanner init (to update configuration)"

    return status


def print_registration_instructions():
    """
    Print manual registration instructions for Claude Code.
    """
    project_dir = Path(__file__).resolve().parent.parent
    server_config = get_local_server_config()

    print("\n" + "="*60)
    print("CLAUDE CODE MCP SERVER CONFIGURATION")
    print("="*60)
    print("\nTo manually add the MCP server to Claude Code:")
    print(f"\n1. Open Claude Code settings")
    print(f"2. Add the following to your MCP settings:\n")
    print(json.dumps({
        "mcpServers": {
            "flanner": server_config
        }
    }, indent=2))
    print(f"\n3. Restart Claude Code")
    print("="*60 + "\n")
