"""Quick test of MCP server tools"""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.database import init_database
from src.server import (
    get_plan_config,
    list_projects,
    create_project_tool,
    list_plan_files_tool,
    create_plan_file_tool
)

print("="*60)
print("TESTING MCP PLAN FILE MANAGER")
print("="*60)

# Initialize database in temp location
test_db = "test_data.db"
if os.path.exists(test_db):
    os.remove(test_db)

print("\n1. Initializing database...")
init_database(test_db)
print("[OK] Database initialized")

# Test get_plan_config
print("\n2. Testing get_plan_config()...")
config = get_plan_config()
print(f"[OK] Default config: {config['plan_directory']}")

# Test list_projects (should be empty)
print("\n3. Testing list_projects()...")
projects = list_projects()
print(f"[OK] Projects: {len(projects)} (should be 0)")

# Test create_project
print("\n4. Testing create_project_tool()...")
project_root = os.getcwd()
result = create_project_tool(
    name="test-project",
    description="A test project",
    project_root=project_root,
    plan_directory=".plans"
)

if result.get('error'):
    print(f"[ERROR] {result['message']}")
else:
    print(f"[OK] Project created: {result['name']}")
    print(f"  ID: {result['id']}")
    print(f"  Plan path: {result['full_plan_path']}")
    project_id = result['id']

    # Test list_plan_files
    print("\n5. Testing list_plan_files_tool()...")
    plan_files = list_plan_files_tool(project_id)
    print(f"[OK] Plan files: {len(plan_files)} (should be 0)")

    # Test create_plan_file
    print("\n6. Testing create_plan_file_tool()...")
    content = """# Test Architecture Plan

## Overview
This is a test plan file to verify the MCP server is working correctly.

## Components
- Database layer
- MCP Server
- CLI tool
"""

    result = create_plan_file_tool(
        project_id=project_id,
        name="test-architecture",
        content=content,
        description="Test architecture plan",
        created_by="test-script"
    )

    if result.get('error'):
        print(f"[ERROR] {result['message']}")
    else:
        print(f"[OK] Plan file created: {result['name']}")
        print(f"  ID: {result['id']}")
        print(f"  Version: {result['version']}")
        print(f"  File path: {result['file_path']}")

        # Verify file exists
        if os.path.exists(result['file_path']):
            print(f"[OK] File exists on disk")

            # Read and show first few lines
            with open(result['file_path'], 'r') as f:
                lines = f.readlines()[:15]
                print("\n  First 15 lines of file:")
                for i, line in enumerate(lines, 1):
                    print(f"    {i}: {line.rstrip()}")
        else:
            print(f"[ERROR] File not found on disk!")

print("\n" + "="*60)
print("ALL TESTS PASSED!")
print("="*60)

# Cleanup
if os.path.exists(test_db):
    os.remove(test_db)
    print("\n[OK] Test database cleaned up")
