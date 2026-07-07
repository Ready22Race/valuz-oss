# syntax=docker/dockerfile:1.7
#
# Valuz agent-harness KERNEL image for the **Tencent AGS AIO sandbox**.
#
# Unlike the generic standalone image (../kernel.Dockerfile, python-slim), the
# AIO sandbox base brings the File API, Browser/Shell/Code tools the agents use,
# and the unified :8080 nginx gateway. Our kernel runs INSIDE it as a
# supervisord-managed **custom service**, reachable through the gateway. We do
# NOT override the base entrypoint — the base's supervisord (PID1) starts our
# program alongside the built-in AIO services (the AIO "Custom Image" path).
#
# Coexist contract (from the agent-infra/sandbox AIO base — VERIFY against the
# actual Tencent base with the CI `inspect-aio-base` step; the include dirs are
# the load-bearing assumption here):
#   - a supervisord program  → /opt/tiger/run/supervisord/<name>.conf
#   - an nginx route          → /opt/gem/nginx/<name>.conf   (unified :8080)
#
# Build context = backend/. Build (needs pull access to the AGS AIO base):
#   docker login ccr.ccs.tencentyun.com
#   docker buildx build --platform linux/amd64 \
#     -f docker/aio/Dockerfile \
#     -t ccr.ccs.tencentyun.com/xiaobang/valuz-kernel:<tag> --push .
# or use scripts/build-kernel-image.sh with VARIANT=aio.

# The Tencent AGS all-in-one sandbox base. Pinned (not :latest) for
# reproducibility once the exact tag is known — override BASE_IMAGE at build
# time. This is the "基础镜像依赖" the kernel layers onto.
ARG BASE_IMAGE=ccr.ccs.tencentyun.com/ags-image/sandbox-aio:latest
FROM ${BASE_IMAGE}

# uv installs a self-contained python + the dependency closure, so we never
# depend on whatever python layout the AIO base ships.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Optional PyPI mirror for faster builds behind the Great Firewall, e.g.
#   --build-arg UV_DEFAULT_INDEX=https://mirrors.tencent.com/pypi/simple
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

# Custom-service wiring: kernel-run.sh is the supervisord program command; the
# .conf files are dropped into the base's include dirs so its supervisord starts
# the kernel and its nginx fronts it at /kernel/ on the unified :8080 gateway.
COPY docker/aio/kernel-run.sh            /app/kernel-run.sh
COPY docker/aio/supervisord.kernel.conf  /opt/tiger/run/supervisord/valuz-kernel.conf
COPY docker/aio/nginx.kernel.conf        /opt/gem/nginx/valuz-kernel.conf
RUN chmod +x /app/kernel-run.sh && mkdir -p /app/data

# NB: no ENTRYPOINT / CMD — inherit the AIO base's supervisord init, which picks
# up /opt/tiger/run/supervisord/valuz-kernel.conf and starts the kernel
# alongside the built-in services. The host reaches it at
# https://<sandbox-public-domain>/kernel/api/v1/...
