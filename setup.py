"""
Setup script for Flanner
"""

from setuptools import setup, find_packages

setup(
    name="flanner",
    version="1.0.0",
    description="Flanner - Plan file management for AI assistants",
    author="Your Name",
    packages=find_packages(),
    install_requires=[
        "click>=8.0.0",
        "rich>=13.0.0",
        "sqlalchemy>=2.0.0",
        "python-frontmatter>=1.0.0",
        "pyyaml>=6.0.0",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "jinja2>=3.1.0",
        "markdown>=3.4.0",
        "pygments>=2.15.0",
        "mcp>=0.1.0",
    ],
    entry_points={
        "console_scripts": [
            "flanner=src.cli:cli",
        ],
    },
    python_requires=">=3.8",
)
