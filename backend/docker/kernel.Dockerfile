# syntax=docker/dockerfile:1.7
#
# Valuz agent-harness KERNEL image — the data plane that runs inside a cloud
# sandbox (e.g. Volcengine veFaaS). The host (control plane) talks to it over
# HTTP via HttpKernelClient; see docs/design/kernel-sandbox-deployment.md.
#
# What ships in here (and what does NOT):
#   - backend/kernel/  — the kernel app (app.main:app) + runtimes (src/)
#   - backend/alembic/kernel/ — the kernel migration chain (self-migrates on boot)
#   - the dependency closure from pyproject.toml + uv.lock, installed with
#     `uv sync --no-install-project` so the HOST package (valuz_agent) is
#     deliberately EXCLUDED — the kernel is boundary-clean (it imports no
#     valuz_agent) and must stay that way in the image.
#
# Runtime CLIs come for free via the dependency closure — no Node.js needed:
#   - claude-agent-sdk bundles a self-contained `claude` binary (Bun-compiled,
#     in site-packages/claude_agent_sdk/_bundled/claude); the LINUX wheel
#     bundles the LINUX binary, matched automatically when uv sync runs here.
#   - openai-codex pulls a self-contained `codex` Rust binary. NOTE: pyproject
#     `[tool.uv] override-dependencies` drops `openai-codex-cli-bin` on linux,
#     so codex MAY be absent in this image — verify after the first linux build.
#     claude_agent + deepagents (what valuz routes to today) are unaffected.
#
# Build (from the backend/ directory as context):
#   docker buildx build --platform linux/amd64 \
#     -f docker/kernel.Dockerfile -t <registry>/<ns>/valuz-kernel:<tag> --push .
# or use scripts/build-kernel-image.sh.

# uv must be recent enough to parse pyproject's [tool.uv] (e.g.
# `required-environments`, added in 0.11) — match the repo's uv.
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.1

# ── Stage 1: builder — resolve + install the dependency closure ────────────
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

COPY --from=uv /uv /usr/local/bin/uv

# Some sdists (rare here — most deps are wheels) need a compiler; kept in the
# builder stage only, never in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer-cache the dependency install: copy ONLY the manifests first, so a
# kernel source change doesn't bust the (slow) dependency layer.
COPY pyproject.toml uv.lock ./

# Optional PyPI mirror for faster builds behind the Great Firewall, e.g.
#   --build-arg UV_DEFAULT_INDEX=https://mirrors.volces.com/pypi/simple
# (or the Volcengine/Tsinghua mirror). Empty = default PyPI.
ARG UV_DEFAULT_INDEX=
ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# --no-install-project: install the deps but NOT valuz_agent (the project
# package). --frozen: use uv.lock exactly (reproducible). --no-dev: skip the
# dev group (pytest/mypy/ruff). The bundled claude/codex binaries land inside
# /app/.venv/.../site-packages so the venv is fully self-contained.
RUN uv sync --no-install-project --frozen --no-dev

# ── Stage 2: runtime — the shipped image ───────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

# ca-certificates: TLS to the LLM providers + host callback.
# git: the task runtime uses `git worktree` for member subruns.
# tini: a real init so the many CLI subprocesses the runtimes fork get reaped
#       (no zombie pile-up in a long-lived kernel).
# curl: health probes / debugging.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The dependency closure (incl. the bundled claude/codex binaries).
COPY --from=builder /app/.venv /app/.venv

# Kernel source + its migration chain. The sibling layout under /app is
# load-bearing: alembic/kernel/env.py resolves the kernel package as
# `<env.py>.parents[2]/kernel` → /app/kernel.
COPY kernel /app/kernel
COPY alembic/kernel /app/alembic/kernel

COPY docker/kernel-entrypoint.sh /app/kernel-entrypoint.sh
RUN chmod +x /app/kernel-entrypoint.sh

# Non-root. /app/data holds the sandbox-local SQLite (ephemeral with the
# sandbox); /workspace is the unified project root the host stages into via
# the File API (cloud bind_workspace). Both must be writable by the runtime
# user.
RUN useradd --create-home --uid 10001 kernel \
    && mkdir -p /app/data /workspace \
    && chown -R kernel:kernel /app/data /workspace
USER kernel

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/kernel \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0
# NB: DATABASE_URL defaults inside the entrypoint (sandbox-local SQLite under
# /app/data). KERNEL_AUTH_TOKEN MUST be supplied at deploy time — the kernel
# refuses to serve unauthenticated on a non-loopback bind. KERNEL_SANDBOX_CONTROL
# is deliberately UNSET: the macOS-extension control plane is local-only; cloud
# dynamic mount goes through the File API, not sandbox extensions.

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/app/kernel-entrypoint.sh"]
