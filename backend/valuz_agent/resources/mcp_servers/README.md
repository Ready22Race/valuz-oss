# Bundled MCP servers

Local stdio MCP servers vendored into the app and exposed through
`resources/connector_catalog.json`. Each subdirectory is one server:
`<slug>/server.py` (FastMCP, `--transport stdio`) + `requirements.txt`.

## How they run

A catalog entry references its entry point with the **`{mcp_dir}`**
placeholder, which `adapters/mcp_resolver.py:_build_stdio_config` expands to
this directory's absolute path (works in a dev checkout and a PyInstaller
build, where `resources/` lands under `_internal/valuz_agent/`). Dependencies
are installed on demand via `uv run --no-project --with <pkg> ...`, so the main
backend env stays clean and the bundle stays small. First launch of a given
server pays a one-time dependency-install cost.

```jsonc
{
  "slug": "akshare-mcp",
  "transport": "stdio",
  "auth_type": "none",
  "command": "uv",
  "args": ["run", "--no-project", "--with", "mcp>=1.0.0", "--with", "akshare>=1.14.0",
           "python", "{mcp_dir}/akshare-mcp/server.py", "--transport", "stdio"]
}
```

## Credentials

Paid servers (`wind-mcp`, `ifind-mcp`) read their API key from the **harness
process env** (`WIND_API_KEY` / `IFIND_AUTH_TOKEN`). When a stdio connector
declares no `env` / `env_vars`, the child inherits the full parent env
(`kernel/src/runtimes/mcp_env.py`), so a key set in the backend `.env` reaches
the server with no extra wiring. The resolver also injects any
`cred_manifest` entry whose `target == "env"` from the secret store — the
forward-compatible path for a future credential UI. Free servers
(`akshare-mcp`, `china-news-mcp`) need no credentials.

## Provenance

Vendored from `claude-for-financial-services-cn/mcp-servers/`. Refresh by
re-copying the upstream `<slug>/` directory.

## Known follow-up

`uv` must be on `PATH` at runtime. It is present in dev; the packaged desktop
app should vendor a `uv` binary the same way `rg` is vendored
(`backend/vendor/rg/`, located via an env the Electron sidecar sets).
