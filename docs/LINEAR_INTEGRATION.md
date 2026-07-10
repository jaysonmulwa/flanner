# Linear integration

Link flanner plan files to Linear issues so a plan and its ticket stay
connected. The integration has two tiers:

- **Link-only (default):** store the issue identifier against a plan and build a
  `linear.app` URL. No network, no credentials.
- **API sync (opt-in):** with a Linear API key, verify the issue exists, cache
  its title and state, attach a URL to the issue, and refresh live status.

## Configure a workspace

The workspace slug is the `urlKey` in your Linear URLs, e.g. `acme` in
`https://linear.app/acme/...`. You can pass the slug or a full URL.

```bash
flanner linear config PROJECT --workspace acme
flanner linear config PROJECT --workspace https://linear.app/acme
```

The workspace is stored per project and is only used to build issue URLs.

## Link a plan to an issue

Linear issue identifiers look like `ENG-123` (team key + number).

```bash
flanner linear link my-plan --issue ENG-123 --project PROJECT
flanner linear link my-plan --issue ENG-123 --notes "design for the new endpoint"
```

Run from inside the project's git repo and you can drop `--project`.

Other commands:

```bash
flanner linear links [--project PROJECT]      # list all links (one project or all)
flanner linear show my-plan [--project ...]   # detailed links for one plan
flanner linear unlink my-plan --issue ENG-123 # remove one link
flanner linear unlink my-plan --all           # remove all links for the plan
```

## API sync (Tier 2)

Set a [Linear personal API key](https://linear.app/settings/api) in the
environment. It is read from `LINEAR_API_KEY` only: never stored in the flanner
database, never passed as a command-line flag (which would leak into shell
history), and never logged.

```bash
export LINEAR_API_KEY=lin_api_xxxxxxxx
```

Verify the key and get the snippet to paste into your MCP settings:

```bash
flanner linear auth
```

`auth` checks the key against Linear (showing who you are), and prints the MCP
server config block with `LINEAR_API_KEY` in its `env`, so the AI agent's
`flanner-mcp` process gets the same access (it does not inherit your terminal's
environment). The key is read from the environment only; `auth` takes no
argument and stores nothing.

With a key set:

- `linear link` **verifies** the issue exists before linking and **caches** its
  title and state. A missing issue aborts the link; a network error links anyway
  and prints a warning (so you can still work offline). Skip verification with
  `--no-verify`.
- `linear link --attach-url URL` attaches that URL to the Linear issue (for
  example, a link to the rendered plan or a PR).
- `linear refresh my-plan` re-pulls the title and state for every link on a
  plan, updating the cached values.

```bash
flanner linear link my-plan --issue ENG-123 --attach-url https://example.com/plan
flanner linear refresh my-plan --project PROJECT
```

The same behaviour is exposed to AI agents over MCP as `configure_linear_tool`,
`link_plan_to_linear_tool`, `get_linear_links_tool`, `list_linear_links_tool`,
`unlink_linear_issue_tool`, and `get_linear_config_tool`.

## How it maps to Linear's API

Live sync uses Linear's GraphQL API (`https://api.linear.app/graphql`) over the
standard library, so it adds no runtime dependency. An issue is looked up by
filtering on its team key and number (the `issue` query needs the internal UUID,
which the human `ENG-123` identifier is not), and attachments use the
`attachmentLinkURL` mutation.
