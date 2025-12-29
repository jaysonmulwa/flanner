# Flanner Installation Guide

## Quick Start (Without Installation)

You can run Flanner directly without installation:

```bash
# Windows
cd path\to\flanner
flanner --help

# Or use Python directly
python -m src.cli --help
```

## Proper Installation (Recommended)

### Option 1: Install with pip (Editable Mode)

This allows you to run `flanner` from anywhere:

```bash
cd flanner
pip install -e .
```

After installation, you can use `flanner` command directly:

```bash
flanner init
flanner status
flanner sync
```

### Option 2: Add to PATH (Windows)

1. Add the flanner directory to your PATH
2. Use the `flanner.bat` wrapper:

```bash
set PATH=%PATH%;C:\path\to\flanner
flanner --help
```

### Option 3: Create Alias (Linux/Mac)

Add to your `.bashrc` or `.zshrc`:

```bash
alias flanner='python /path/to/flanner/src/cli.py'
```

Then reload your shell:

```bash
source ~/.bashrc  # or ~/.zshrc
flanner --help
```

## Verify Installation

```bash
flanner --help
# Should show: Flanner - Manage plan files for AI assistants
```

## Post-Installation

Initialize Flanner in your project:

```bash
cd your-project
flanner init
```

This will:
- Create `~/.flanner/` directory for database
- Set up your project with plan file tracking
- Automatically register with Claude Code (optional)

## Upgrading

If you installed with pip:

```bash
cd flanner
git pull  # if using git
pip install -e . --upgrade
```

## Uninstallation

If installed with pip:

```bash
pip uninstall flanner
```

To remove data:

```bash
# Windows
rmdir /s %USERPROFILE%\.flanner

# Linux/Mac
rm -rf ~/.flanner
```

## Command Reference

All commands now use `flanner` prefix:

| Old Command | New Command |
|-------------|-------------|
| `python -m src.cli init` | `flanner init` |
| `python -m src.cli status` | `flanner status` |
| `python -m src.cli sync` | `flanner sync` |
| `python -m src.cli list` | `flanner list` |
| `python -m src.cli web` | `flanner web` |

See `flanner --help` for full command list.
