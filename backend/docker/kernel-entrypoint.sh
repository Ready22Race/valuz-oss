#!/bin/sh
# Kernel container entrypoint: self-migrate the kernel DB, then serve.
#
# The cloud kernel can't be migrated host-side (the host can't subprocess into
# a remote sandbox), so the image migrates its OWN schema on every start —
# idempotent: `alembic upgrade head` on an up-to-date DB is a no-op. For the
# v1 cloud model the DB is sandbox-local SQLite, fresh per sandbox.
set -eu

# Be invocation-independent: a cloud sandbox platform (e.g. Tencent AGS) may run
# this as a bare Command without honouring the image WORKDIR/ENV, so anchor the
# cwd and PYTHONPATH ourselves. alembic resolves -c alembic/kernel/alembic.ini
# from /app, and PYTHONPATH lets the kernel (app.main / src.*) import.
cd /app
export PYTHONPATH="${PYTHONPATH:-/app/kernel}"
export PATH="/app/.venv/bin:${PATH}"

# Sandbox-local SQLite by default. A deployer whose platform only mounts /tmp
# writable can override, e.g. DATABASE_URL=sqlite+aiosqlite:////tmp/kernel.db
: "${DATABASE_URL:=sqlite+aiosqlite:////app/data/kernel.db}"
: "${PORT:=8000}"
: "${LOG_LEVEL:=info}"
export DATABASE_URL

# Fail fast with an actionable message instead of a confusing mid-boot
# RuntimeError from the kernel's standalone-auth guard.
if [ -z "${KERNEL_AUTH_TOKEN:-}" ] && [ "${KERNEL_ALLOW_UNAUTHENTICATED:-}" != "1" ]; then
    echo "[kernel] FATAL: KERNEL_AUTH_TOKEN is required (standalone kernel refuses" >&2
    echo "[kernel]        to serve unauthenticated on a non-loopback bind). Set it" >&2
    echo "[kernel]        at deploy time, or KERNEL_ALLOW_UNAUTHENTICATED=1 + HOST=127.0.0.1" >&2
    echo "[kernel]        for a loopback-only smoke test." >&2
    exit 1
fi

echo "[kernel] migrating ${DATABASE_URL}"
# cwd is /app: alembic resolves -c alembic/kernel/alembic.ini, and PYTHONPATH
# (=/app/kernel) lets env.py import the kernel models.
alembic -c alembic/kernel/alembic.ini upgrade head

echo "[kernel] serving app.main:app on ${HOST:-0.0.0.0}:${PORT} (log=${LOG_LEVEL})"
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT}" --log-level "${LOG_LEVEL}"
