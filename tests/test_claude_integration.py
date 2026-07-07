"""Claude Code MCP registration config tests (no filesystem writes)."""

from flanner.claude_integration import (
    get_claude_config_path,
    get_cloud_server_config,
    get_local_server_config,
)


def test_config_path_detection():
    assert get_claude_config_path()


def test_local_server_config():
    config = get_local_server_config()
    assert config["command"] == "python"
    assert config["args"] == ["-m", "flanner.server"]
    assert "cwd" in config


def test_cloud_server_config():
    config = get_cloud_server_config(server_url="https://api.example.com", api_key="test_key_123")
    assert config["type"] == "cloud"
    assert config["url"] == "https://api.example.com"
    assert config["apiKey"] == "test_key_123"
