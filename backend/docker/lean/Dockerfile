# syntax=docker/dockerfile:1.7
#
# Lean AGS kernel image — python-slim + kernel + e2b envd + s6-overlay.
#
# Same capability as the sandbox-code variant (AGS login via envd on :49983,
# kernel on :8000, non-root) but WITHOUT the ~4.7GB sandbox-code base: we
# self-install the open-source s6-overlay as PID1 and copy just the static
# `envd` binary (16.7MB, github.com/e2b-dev/infra) out of the AGS base at BUILD
# time. The runtime image does not depend on sandbox-code/aio.
#
# Build context = backend/. Build:
#   docker buildx build --platform linux/amd64 \
#     -f docker/lean/Dockerfile -t <registry>/valuz-kernel-lean:<tag> --push .

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.1
ARG S6_OVERLAY_VERSION=3.2.0.2
# envd is extracted (build-time only) from the AGS base; override to any image
# that carries /usr/bin/envd.
ARG ENVD_SRC_IMAGE=ccr.ccs.tencentyun.com/ags-image/sandbox-code:v1.2

# ── envd source (build-time extraction only) ───────────────────────────────
FROM ${ENVD_SRC_IMAGE} AS envdsrc

# ── builder: resolve + install the dependency closure ──────────────────────
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
ARG UV_DEFAULT_INDEX=
ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX} \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
RUN uv sync --no-install-project --frozen --no-dev

# ── runtime ────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG S6_OVERLAY_VERSION

# ca-certificates: TLS to LLM providers + host callback. git: task worktrees.
# bash: envd spawns login/exec shells + the valuz-apply-env helper. curl:
# health/debug + the helper's kernel readiness poll. xz-utils: unpack s6.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git bash curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Codex CLI binary: as of openai-codex 0.1.0b3 (valuz-agent #442) the pinned
# openai-codex-cli-bin==0.137.0a4 publishes manylinux (glibc) wheels, so it lands
# in the `uv sync` closure above natively — no more musl-wheel extraction hack.
# The kernel's codex runtime resolves it from the bundled openai-codex-cli-bin.

# s6-overlay (open source) as PID1 init — supervises envd + the kernel.
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp/s6-noarch.tar.xz
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz /tmp/s6-x86_64.tar.xz
RUN tar -C / -Jxpf /tmp/s6-noarch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-x86_64.tar.xz \
    && rm /tmp/s6-noarch.tar.xz /tmp/s6-x86_64.tar.xz

# The AGS login daemon (static Go binary; no deps).
COPY --from=envdsrc /usr/bin/envd /usr/bin/envd

WORKDIR /app
# Dependency closure + kernel source + migration chain.
COPY --from=builder /app/.venv /app/.venv
COPY kernel /app/kernel
COPY alembic/kernel /app/alembic/kernel
# Single-process launcher: alembic API migrate + uvicorn.run in ONE interpreter
# (the old two-process boot imported sqlalchemy/alembic twice, ~0.5-1s wasted).
COPY docker/lean/kernel-launch.py /app/kernel-launch.py

# Unprivileged user (uid 1000) the kernel service drops to; owns /app so it can
# read the venv and write the sandbox-local SQLite.
RUN useradd --create-home --uid 1000 user \
    && mkdir -p /app/data \
    && chown -R 1000:1000 /app

# s6 services: envd (root, login) + valuz-kernel (non-root). Register both in the
# s6-overlay `user` bundle so /init supervises them. valuz-kernel starts at boot
# and blocks in the kernel's CONFIG GATE (KERNEL_CONFIG_WAIT below): the process
# imports everything, runs the migration, then waits in lifespan for the host to
# write /run/valuz/env — so a snapshot freezes a fully-imported, waiting kernel
# and configuration at resume costs milliseconds, no restart. (The earlier s6
# `down`-file gate is gone: s6-rc longruns never honored it anyway.)
COPY docker/lean/s6-rc.d/envd         /etc/s6-overlay/s6-rc.d/envd
COPY docker/lean/s6-rc.d/valuz-kernel /etc/s6-overlay/s6-rc.d/valuz-kernel
RUN chmod +x /etc/s6-overlay/s6-rc.d/envd/run /etc/s6-overlay/s6-rc.d/valuz-kernel/run \
    && mkdir -p /etc/s6-overlay/s6-rc.d/user/contents.d \
    && touch /etc/s6-overlay/s6-rc.d/user/contents.d/envd \
             /etc/s6-overlay/s6-rc.d/user/contents.d/valuz-kernel

# KERNEL_CONFIG_WAIT: kernel lifespan blocks until /run/valuz/env appears (the
# host writes it post-resume over envd), then rebuilds its config from the
# applied env. See kernel app/config_gate.py (valuz-agent #446).
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    S6_KEEP_ENV=1 \
    IS_SANDBOX=1 \
    KERNEL_CONFIG_WAIT=1

EXPOSE 8000 49983
ENTRYPOINT ["/init"]
