# Contributing to flanner

## Branches

- `development` is the working branch. Day-to-day work and PRs target it.
- `main` is the release branch and stays always-releasable.
- CI runs on pushes and PRs to both.
- To release: open a PR from `development` to `main`, merge it, then tag and
  cut a GitHub Release from `main` (see Releasing). Publishing to PyPI only
  happens from a Release on `main`, so nothing ships until it reaches `main`.

## Setup

With [uv](https://docs.astral.sh/uv/) (recommended, cross-platform, one step):

```bash
git clone https://github.com/jaysonmulwa/flanner.git
cd flanner
uv sync --extra dev        # creates .venv and installs everything from uv.lock
uv run pre-commit install
```

Or with pip and a manual virtualenv:

```bash
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

## Before you open a PR

All of these must pass; CI enforces them. With uv you can run them without
activating anything (drop the `uv run` prefix if your venv is active):

```bash
uv run ruff check flanner/ tests/ benchmarks/
uv run ruff format --check flanner/ tests/ benchmarks/
uv run mypy --strict flanner/
uv run pytest --cov=flanner --cov-fail-under=80
```

## Ground rules

- Commit titles are semantic: `type(scope): description` (`feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `perf`)
- New code ships with tests in the same PR; bug fixes include a regression test
- The import-boundary test (`tests/test_architecture.py`) encodes the layering
  contract; if your change needs a new edge, argue for it in the PR
- New dependencies must justify their weight over the stdlib or an existing dep
- Keep user-facing changes in `CHANGELOG.md` under `[Unreleased]`
- Architecture decisions get an ADR in `docs/adr/`

## Running the app

In an activated venv the `flanner` command is on your PATH:

```bash
flanner init          # database + MCP registration + project
flanner web           # browser UI at http://localhost:8080
python -m flanner.server  # MCP server over stdio
```

Without activating, use the module form (inside the repo, `uv run flanner` is
shadowed by the `flanner/` source directory, so prefer `-m`):

```bash
uv run python -m flanner.cli init
uv run python -m flanner.cli web
uv run python -m flanner.server
```

## Benchmarks

Reproduce with `python benchmarks/bench.py` (throwaway temp database). Reference
numbers on Windows 11, Python 3.12, SQLite on NVMe: `create_project` ~36 ms,
`create_plan_file` (2.4 KB body) ~40 ms, `list_plan_files` (100 plans) ~5 ms.

## Releasing

1. Move the `[Unreleased]` CHANGELOG entries under a new version heading.
2. Bump `version` in `pyproject.toml` and `server.json` (both the top-level
   `version` and `packages[0].version`). `flanner/__init__.py` needs no edit:
   it reads the installed package metadata, so it follows `pyproject.toml`
   on its own. `tests/test_release.py` fails if the two files disagree.
3. Commit, then tag and push: `git tag vX.Y.Z && git push origin main --tags`.
4. Publish a GitHub Release for the tag. The `publish` workflow then builds the
   package and uploads it to PyPI via Trusted Publishing (OIDC, no tokens).
5. Only submit `server.json` to the MCP registry *after* that version is live on
   PyPI; its `packages[0].version` must resolve to a published release.

The publish job runs in the `pypi` GitHub Environment. Add a **required
reviewer** to that environment (repo *Settings -> Environments -> pypi ->
Required reviewers*) so every publish pauses for a human to approve before it
uploads to PyPI. The one-time PyPI-side setup (registering the trusted
publisher) is in the workflow header at `.github/workflows/publish.yml`.
