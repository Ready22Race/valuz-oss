# Kernel container image

The **agent-harness kernel** packaged as a Linux container — the *data plane*
that runs inside a cloud sandbox (Volcengine veFaaS, or any container host).
The Valuz **host** (control plane) drives it over HTTP via `HttpKernelClient`
(`VALUZ_KERNEL_MODE=http`). See
[docs/design/kernel-sandbox-deployment.md](../../docs/design/kernel-sandbox-deployment.md).

This is the cloud counterpart of the local **Seatbelt** sandbox: same kernel,
same `app.main:app`, but confined by the container/cloud platform instead of a
macOS `sandbox-exec` profile.

## What's inside (and what isn't)

| Included | Excluded |
|----------|----------|
| `backend/kernel/` (app + runtimes) | `valuz_agent/` (the HOST package — the kernel is boundary-clean and stays out) |
| `backend/alembic/kernel/` (self-migrates on boot) | host migrations, parser/KB host code |
| dep closure via `uv sync --no-install-project` | dev tooling (pytest/mypy/ruff) |
| bundled `claude` binary (self-contained, no Node) | Node.js |

The runtime CLIs ride in with the dependency closure: `claude-agent-sdk`'s
**linux wheel bundles a self-contained `claude` binary** (Bun-compiled), and
`openai-codex` pulls a self-contained `codex` Rust binary. **No Node.js
required.**

> ⚠️ **codex caveat** — `pyproject.toml [tool.uv] override-dependencies` drops
> `openai-codex-cli-bin` on linux, so `codex` *may* be absent in this image.
> `claude_agent` + `deepagents` (the runtimes Valuz routes to today) are
> unaffected. Verify codex presence after the first linux build; if needed,
> we'll relax the override for the cloud target.

## Build

```bash
KERNEL_IMAGE=<registry>/<ns>/valuz-kernel:<tag> scripts/build-kernel-image.sh [--push]
# e.g.
KERNEL_IMAGE=cn-beijing.cr.volces.com/myns/valuz-kernel:0.1.0 \
  scripts/build-kernel-image.sh --push
```

- Default platform `linux/amd64` (veFaaS default); `PLATFORM=linux/arm64` for arm.
- Build context is `backend/`; the Dockerfile is `backend/docker/kernel.Dockerfile`.

### Push to Volcengine Container Registry (CR)

```bash
docker login <registry>           # e.g. cn-beijing.cr.volces.com
#   username/password: CR instance → 访问控制 / 用户凭证
scripts/build-kernel-image.sh --push
```

## Local smoke test

```bash
docker run --rm -p 8000:8000 -e KERNEL_AUTH_TOKEN=devtoken <image>
curl -s localhost:8000/health                                   # {"status":"ok"}
curl -s -H 'Authorization: Bearer devtoken' localhost:8000/api/v1/sessions
```

## Runtime environment contract

| Env | Required | Default | Purpose |
|-----|----------|---------|---------|
| `KERNEL_AUTH_TOKEN` | **yes** | — | Bearer token every request must carry. The kernel refuses to serve unauthenticated on a non-loopback bind. The host/provider sets it and uses it as the `HttpKernelClient` token. |
| `DATABASE_URL` | no | `sqlite+aiosqlite:////app/data/kernel.db` | Sandbox-local SQLite (ephemeral with the sandbox in the v1 cloud model). Override to `…:////tmp/kernel.db` if only `/tmp` is writable. |
| `PORT` | no | `8000` | Bind port (the platform/APIG routes to it). |
| `HOST` | no | `0.0.0.0` | Bind address. |
| `CODEX_TOOLKIT_BASE_URL` | for codex | `http://127.0.0.1:8000` | Where the codex runtime reaches the **host's** harness toolkit MCP (the ④ callback). Must be set to a host URL reachable *from the sandbox* in cloud. |
| `LOG_LEVEL` | no | `info` | uvicorn log level. |
| `KERNEL_ALLOW_UNAUTHENTICATED` | no | — | `=1` + `HOST=127.0.0.1` only, for a loopback smoke test. Never in cloud. |

Deliberately **unset** in cloud: `KERNEL_SANDBOX_CONTROL` (the macOS
sandbox-extension consume plane is local-only; cloud dynamic mount goes through
the File API, not extensions).

## Boot sequence

`tini` → `kernel-entrypoint.sh`:
1. `alembic -c alembic/kernel/alembic.ini upgrade head` — idempotent self-migrate
   (the host can't subprocess into a remote sandbox, so the image migrates itself).
2. `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

## Egress the kernel needs

- LLM providers (`api.openai.com`, `api.anthropic.com`, or a configured proxy).
- The host callback (`CODEX_TOOLKIT_BASE_URL` and the `harness` MCP server URL
  in the session config) for harness tools (dispatch/orchestration/memory/skills).
