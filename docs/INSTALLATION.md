# Installation

For the short version see the [README](../README.md); for the development
setup (uv, gates, release) see [CONTRIBUTING](../CONTRIBUTING.md). This guide
covers install, project setup, upgrade, and removal in more detail.

## Install

Flanner is a Python package. Install it from a clone in editable mode, which
puts the `flanner` command on your PATH:

```bash
cd flanner
pip install -e .
flanner --help
```

Or with uv, which creates and manages the virtualenv for you:

```bash
uv sync
uv run python -m flanner.cli --help
```

Note: inside the repo, prefer `python -m flanner.cli` over `uv run flanner`.
The `flanner/` source directory shadows the installed console script there.

Without installing at all, run the module directly:

```bash
python -m flanner.cli --help
```

## Set up a project

```bash
cd your-project
flanner init
```

`flanner init` is safe to re-run. It:

- creates `~/.flanner/` with the SQLite catalog (override with `FLANNER_HOME`
  or `FLANNER_DB_PATH`)
- creates `.plans/` and adds it to `.gitignore`
- registers the MCP server with Claude Code
- writes a managed block to CLAUDE.md and AGENTS.md, installs the guard-write
  hook in `.claude/settings.json`, and installs the flanner-plan skill

Verify:

```bash
flanner status
```

## Upgrade

```bash
cd flanner
git pull
pip install -e . --upgrade
```

## Uninstall

```bash
pip uninstall flanner
```

Remove the catalog (plan files in your repositories are untouched):

```bash
# Linux/Mac
rm -rf ~/.flanner

# Windows
rmdir /s %USERPROFILE%\.flanner
```
