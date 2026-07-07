# syntax=docker/dockerfile:1.7
#
# Valuz agent-harness KERNEL image for the **Tencent AGS AIO sandbox**.
#
# The AIO base (ccr.ccs.tencentyun.com/ags-image/sandbox-aio) is an s6-overlay
# image (PID1 = /init) that bundles Browser/Shell/File/Jupyter/VSCode + an nginx
# gateway. Our kernel runs INSIDE it as an **s6-overlay service** (added to the
# base's `user` bundle), started by the base's /init alongside the built-in
# services. We do NOT override the entrypoint.
#
# AGS reachability: AGS port-maps container ports to https://<port>-<instance>.
# <domain> (e2b-compatible), and the host's AgsConfig uses :8000, so the kernel
# just listens on 0.0.0.0:8000 — NO gateway route needed (unlike the veFaaS
# single-domain model). Auth = the AGS-injected KERNEL_AUTH_TOKEN.
#
# Build context = backend/. Build (the AGS AIO base is public):
#   docker buildx build --platform linux/amd64 \
#     -f docker/aio/Dockerfile \
#     -t ccr.ccs.tencentyun.com/xiaobang/valuz-kernel:<tag> --push .
# or use scripts/build-kernel-image.sh with VARIANT=aio.

# The Tencent AGS all-in-one sandbox base (the "基础镜像依赖"). Override
# BASE_IMAGE to pin a digest/tag.
ARG BASE_IMAGE=ccr.ccs.tencentyun.com/ags-image/sandbox-aio:latest
FROM ${BASE_IMAGE}

# uv installs a self-contained python + the dependency closure into /app/.venv,
# so the kernel never depends on the base's python (the base ships its own at
# /root/.server for its API server).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Optional PyPI mirror for faster CN builds, e.g.
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

# s6-overlay service: drop the service dir into the base's s6-rc.d and register
# it in the `user` bundle (an empty file named after the service) so /init starts
# it alongside the built-in AIO services.
COPY docker/aio/s6-rc.d/valuz-kernel /etc/s6-overlay/s6-rc.d/valuz-kernel
RUN chmod +x /etc/s6-overlay/s6-rc.d/valuz-kernel/run \
    && mkdir -p /app/data \
    && touch /etc/s6-overlay/s6-rc.d/user/contents.d/valuz-kernel

# Declare the kernel port (the base exposes its own 9000/49999/49983 but not
# 8000; :8000 is free). AGS port-maps it to https://8000-<instance>.<domain> —
# same as the standalone image, so the host needs no config change.
EXPOSE 8000

# NB: no ENTRYPOINT / CMD — inherit the base's /init (s6-overlay), which brings
# up the `user` bundle (now including valuz-kernel). The kernel is reached at
# https://8000-<instance>.<traffic-domain>/api/v1/...
