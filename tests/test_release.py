"""The version has to say the same thing everywhere it is written down.

`server.json` sat at 0.7.1 while `pyproject.toml` said 0.9.3 — four releases
of drift, in the file the MCP registry reads to find the package. Nothing
caught it, because nothing was looking: the release checklist said to update
both, and a checklist is only as good as the last person to follow it.

So the checklist gets a test. This is the whole of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent


def _declared_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def test_server_json_matches_pyproject() -> None:
    """The registry entry must name a version that actually exists on PyPI."""
    manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    expected = _declared_version()

    assert (
        manifest["version"] == expected
    ), f"server.json version is {manifest['version']}, pyproject is {expected}"
    assert manifest["packages"][0]["version"] == expected, (
        f"server.json packages[0].version is {manifest['packages'][0]['version']}, "
        f"pyproject is {expected}"
    )


def test_the_package_reports_the_declared_version() -> None:
    """`flanner --version` and `flanner.__version__` follow pyproject.

    Only true of an installed package; a source tree with nothing installed
    reports the sentinel instead, which is a different correct answer.
    """
    import flanner

    if flanner.__version__ == "0.0.0+unknown":
        return  # running from a source tree, nothing installed to read
    assert flanner.__version__ == _declared_version()


def test_the_changelog_mentions_the_version_being_released() -> None:
    """A release with no changelog entry is a release nobody can read about."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = _declared_version()
    assert version in changelog, f"CHANGELOG.md has no entry for {version}"


def test_the_cli_module_does_not_import_sqlalchemy() -> None:
    """Cold start is a feature, and this is the regression that would undo it.

    Importing SQLAlchemy at module level cost 630 ms on every invocation,
    including `flanner --version`. The database is reached through the thin
    wrappers in cli.py, which import it on first use. A future edit that adds
    `from .database import ...` back to the top of the file would silently
    restore the old start time; this fails instead.
    """
    import ast

    tree = ast.parse((ROOT / "flanner" / "cli.py").read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:  # module level only; imports inside functions are the point
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("sqlalchemy") or node.module.endswith("database"):
                offenders.append(node.module)
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.startswith("sqlalchemy")]

    assert not offenders, f"cli.py imports {offenders} at module level; import them lazily"


# --- deprecation has a mechanism, and the mechanism has rules --------------


def test_a_deprecation_must_name_a_replacement() -> None:
    """A deprecation without one is a removal with extra steps."""
    import pytest

    from flanner import deprecation

    with pytest.raises(ValueError, match="needs a replacement"):
        deprecation.warn("--old-flag", instead="", removed_in="1.0", since="0.9")


def test_a_deprecation_says_what_when_and_instead_of_what() -> None:
    import warnings

    from flanner import deprecation

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deprecation.warn(
            "doctor --output json returning an array",
            instead="the object form, which carries `catalog` and `enrollment`",
            removed_in="1.0.0",
            since="0.9.2",
        )

    assert len(caught) == 1
    assert issubclass(caught[0].category, DeprecationWarning)
    message = str(caught[0].message)
    assert "0.9.2" in message and "1.0.0" in message and "Use " in message
