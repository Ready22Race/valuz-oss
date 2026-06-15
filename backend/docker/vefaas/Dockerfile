# syntax=docker/dockerfile:1.7
#
# Valuz KERNEL image for **Volcengine veFaaS**, layered on the AIO Sandbox base.
#
# Unlike the generic standalone image (../kernel.Dockerfile, python-slim), the
# cloud sandbox is the AIO Sandbox — it brings the File API, Browser/Shell/Code
# tools the agents use, the unified :8080 nginx gateway, and the veFaaS
# instance plumbing. Our kernel runs INSIDE it as a supervisord-managed
# **custom service**, reachable through the gateway. We do NOT override the
# base entrypoint — the base's supervisord (PID1) starts our program alongside
# the built-in AIO services (this is AIO's documented "Custom Image" path).
#
# Coexist contract (from AIO docs — VERIFY against the base, see the CI
# introspection job):
#   - a supervisord program  → /opt/tiger/run/supervisord/<name>.conf
#   - an nginx route          → /opt/gem/nginx/<name>.conf  (unified :8080)
#
# Build context = backend/. Build:
#   docker buildx build --platform linux/amd64 \
#     -f docker/vefaas/Dockerfile -t <volc-cr>/<ns>/valuz-kernel:<tag> --push .

# Pinned (not :latest) for reproducibility. The Volcengine vefaas-public image
# and ghcr.io/agent-infra/sandbox are the SAME image at a given version (the
# upstream README publishes both at the same tag — the volc one is the mainland
# China mirror). This default is the CN base (fast to pull in CN where you
# build to push to Volcengine CR); CI overrides BASE_IMAGE to the ghcr mirror
# at the same version for faster pulls on GitHub's runners.
ARG BASE_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.0.0.150
FROM ${BASE_IMAGE}

# uv installs a self-contained python + the dependency closure, so we never
# depend on whatever python layout the base ships. Pin a version recent enough
# to parse pyproject's [tool.uv] (`required-environments`, 0.11+) — match repo.
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /usr/local/bin/uv

ARG UV_DEFAULT_INDEX=
ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_PYTHON_DOWNLOADS=auto \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependency closure first (layer cache). --no-install-project keeps the HOST
# package (valuz_agent) OUT — the kernel is boundary-clean. The linux wheels of
# claude-agent-sdk / openai-codex carry the linux runtime binaries (no Node).
COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project --frozen --no-dev --python 3.12

# Kernel source + migration chain (sibling layout: alembic/kernel/env.py
# resolves the kernel as <env.py>.parents[2]/kernel → /app/kernel).
COPY kernel /app/kernel
COPY alembic/kernel /app/alembic/kernel

# Custom-service wiring.
COPY docker/vefaas/kernel-run.sh            /app/kernel-run.sh
COPY docker/vefaas/supervisord.kernel.conf  /opt/tiger/run/supervisord/valuz-kernel.conf
COPY docker/vefaas/nginx.kernel.conf        /opt/gem/nginx/valuz-kernel.conf
RUN chmod +x /app/kernel-run.sh && mkdir -p /app/data

# NB: no ENTRYPOINT / CMD — inherit the AIO base's supervisord init, which
# picks up /opt/tiger/run/supervisord/valuz-kernel.conf and starts the kernel
# alongside the built-in services. The host reaches it at
# https://<sandbox-public-domain>/kernel/api/v1/...
