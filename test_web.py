"""
Test script for web interface
"""

from fastapi.testclient import TestClient
from src.web import app
from src.database import init_database
import os
import tempfile

# Initialize test database
test_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
test_db_path = test_db.name
test_db.close()

print(f"Testing with database: {test_db_path}")
init_database(test_db_path)

# Create test client
client = TestClient(app)

print("\nTesting web interface routes...")
print("-" * 50)

# Test dashboard
print("1. Testing dashboard (/)...")
response = client.get("/")
assert response.status_code == 200
assert "Dashboard" in response.text or "MCP Plan Manager" in response.text
print("   OK - Dashboard loads successfully")

# Test projects list
print("2. Testing projects list (/projects)...")
response = client.get("/projects")
assert response.status_code == 200
print("   OK - Projects list loads successfully")

# Test new project form
print("3. Testing new project form (/projects/new)...")
response = client.get("/projects/new")
assert response.status_code == 200
assert "Create New Project" in response.text or "New Project" in response.text
print("   OK - New project form loads successfully")

# Test API endpoints
print("4. Testing API - list projects (/api/projects)...")
response = client.get("/api/projects")
assert response.status_code == 200
assert isinstance(response.json(), list)
print("   OK - API projects endpoint works")

print("\n" + "=" * 50)
print("All web interface tests passed!")
print("=" * 50)

# Cleanup
os.unlink(test_db_path)
print(f"\nCleaned up test database: {test_db_path}")
