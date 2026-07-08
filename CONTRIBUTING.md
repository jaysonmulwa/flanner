# Contributing to flanner

## Setup

```bash
git clone https://github.com/jaysonmulwa/flanner.git
cd flanner
python -m venv venv && venv/Scripts/activate  # or source venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Before you open a PR

All of these must pass; CI enforces them:

```bash
ruff check flanner/ tests/ benchmarks/
ruff format --check flanner/ tests/ benchmarks/
mypy --strict flanner/
pytest --cov=flanner --cov-fail-under=80
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

```bash
flanner init          # database + MCP registration + project
flanner web           # browser UI at http://localhost:8080
python -m flanner.server  # MCP server over stdio
```
