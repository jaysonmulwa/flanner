# ADR 0002: Expose tools through MCP using the official SDK's FastMCP

Status: accepted (retroactive; documents the design as built)

## Context

AI assistants (Claude Code, Codex) need to create and update plan files
without asking the user where things go. The integration surface must be
discoverable by the assistant and require near-zero setup.

## Decision

Expose plan operations as MCP tools using `mcp.server.fastmcp.FastMCP` from
the official `mcp` Python SDK. `flanner init` registers the server in the
assistant's config automatically (`python -m flanner.server`).

## Alternatives considered

- **Third-party `fastmcp` package:** duplicates what the official SDK ships;
  one more dependency to track. Rejected (it was in requirements once and was
  never imported).
- **Plain REST API:** assistants would need custom glue per client; MCP is the
  protocol both target clients already speak.
- **CLI-only integration (assistant shells out):** no schema for arguments,
  no typed results, fragile parsing of console output.

## Consequences

- Tool functions in `flanner/server.py` return plain dicts with an
  `{error, message}` shape so assistants get structured failures.
- The web UI and CLI reuse the same database/storage layer underneath; MCP is
  just another composition root (enforced by tests/test_architecture.py).
