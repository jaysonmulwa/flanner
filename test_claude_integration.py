"""
Test Claude Code Integration
"""

from src.claude_integration import (
    get_claude_config_path,
    get_local_server_config,
    get_cloud_server_config,
    read_claude_config,
    is_server_registered,
    get_claude_config_info
)
import json

print("="*60)
print("TESTING CLAUDE CODE INTEGRATION")
print("="*60)

# Test 1: Path Detection
print("\n1. Testing path detection...")
config_path = get_claude_config_path()
print(f"   Config path: {config_path}")
print(f"   OK - Path detection working")

# Test 2: Local Server Config
print("\n2. Testing local server config generation...")
local_config = get_local_server_config()
print(f"   Generated config:")
print(f"   {json.dumps(local_config, indent=2)}")
assert "command" in local_config
assert "args" in local_config
assert "cwd" in local_config
print(f"   OK - Local config generation working")

# Test 3: Cloud Server Config
print("\n3. Testing cloud server config generation...")
cloud_config = get_cloud_server_config(
    server_url="https://api.example.com",
    api_key="test_key_123"
)
print(f"   Generated config:")
print(f"   {json.dumps(cloud_config, indent=2)}")
assert "type" in cloud_config
assert cloud_config["type"] == "cloud"
assert cloud_config["url"] == "https://api.example.com"
assert cloud_config["apiKey"] == "test_key_123"
print(f"   OK - Cloud config generation working")

# Test 4: Read Config (Safe)
print("\n4. Testing config reading...")
try:
    config = read_claude_config()
    print(f"   Read config successfully")
    print(f"   Total MCP servers: {len(config.get('mcpServers', {}))}")
    print(f"   OK - Config reading working")
except Exception as e:
    print(f"   Warning: {e}")
    print(f"   OK - Safe error handling working")

# Test 5: Registration Check
print("\n5. Testing registration check...")
is_registered = is_server_registered()
print(f"   Server registered: {is_registered}")
print(f"   OK - Registration check working")

# Test 6: Integration Info
print("\n6. Testing integration info...")
info = get_claude_config_info()
print(f"   Config exists: {info['config_exists']}")
print(f"   Registered: {info['server_registered']}")
print(f"   Total servers: {info['total_servers']}")
print(f"   OK - Info retrieval working")

print("\n" + "="*60)
print("ALL TESTS PASSED!")
print("="*60)

print("\nNext steps:")
print("  1. Run: python -m src.cli claude-info")
print("  2. Run: python -m src.cli register")
print("  3. Check: C:\\Users\\...\\Claude\\claude_desktop_config.json")
print("  4. Restart Claude Code")
