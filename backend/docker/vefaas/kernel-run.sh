#!/bin/sh
# supervisord program command for the Valuz kernel inside the AIO sandbox.
# Self-migrates the kernel DB, then serves on loopback; the AIO nginx gateway
# (:8080) fronts it at /kernel/ and applies the sandbox's auth.
set -eu

: "${DATABASE_URL:=sqlite+aiosqlite:////app/data/kernel.db}"
: "${KERNEL_PORT:=8000}"
: "${LOG_LEVEL:=info}"
export DATABASE_URL
export PYTHONPATH=/app/kernel

cd /app

echo "[valuz-kernel] migrating ${DATABASE_URL}"
/app/.venv/bin/python -m alembic -c alembic/kernel/alembic.ini upgrade head

# Bind loopback only: the kernel is reached THROUGH the AIO gateway, never
# directly. That lets it run unauthenticated-on-loopback (the gateway's JWT is
# the outer auth); set KERNEL_AUTH_TOKEN in the supervisord env instead if you
# want defence-in-depth.
echo "[valuz-kernel] serving app.main:app on 127.0.0.1:${KERNEL_PORT}"
exec /app/.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port "${KERNEL_PORT}" --log-level "${LOG_LEVEL}"
