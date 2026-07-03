#!/bin/sh
# Kernel container entrypoint: self-migrate the kernel DB, then serve.
#
# The cloud kernel can't be migrated host-side (the host can't subprocess into
# a remote sandbox), so the image migrates its OWN schema on every start —
# idempotent: `alembic upgrade head` on an up-to-date DB is a no-op. For the
# v1 cloud model the DB is sandbox-local SQLite, fresh per sandbox.
set -eu

# Sandbox-local SQLite by default. A deployer whose platform only mounts /tmp
# writable can override, e.g. DATABASE_URL=sqlite+aiosqlite:////tmp/kernel.db
: "${DATABASE_URL:=sqlite+aiosqlite:////app/data/kernel.db}"
: "${PORT:=8000}"
: "${LOG_LEVEL:=info}"
export DATABASE_URL

# ---------------------------------------------------------------------------
# Out-of-band boot diagnostics. The AGS sandbox exposes no exec/log access, so
# to see WHAT env the kernel process actually received (e.g. whether
# KERNEL_STORE=remote + VALUZ_DATA_API_* were delivered by AGS) we dump the env
# to a file on a MOUNTED volume, readable host-side via the same mount (COS).
# Enabled whenever VALUZ_KERNEL_DIAG_DIR resolves to a writable path; defaults
# to the COS mount root. Best-effort: never blocks boot, and if the dir isn't
# writable we fall back to plain stdout serving (behaviour unchanged).
# ---------------------------------------------------------------------------
_diag_dir="${VALUZ_KERNEL_DIAG_DIR:-/data/valuz_data/_kernel_diag}"
_diag_ok=0
if mkdir -p "$_diag_dir" 2>/dev/null && [ -w "$_diag_dir" ]; then
    _diag_ok=1
    _host="$(hostname 2>/dev/null || echo unknown)"
    {
        echo "==== kernel boot $(date -u 2>/dev/null || true) host=${_host} ===="
        echo "KERNEL_STORE=${KERNEL_STORE:-<unset>}"
        echo "VALUZ_DATA_API_KIND=${VALUZ_DATA_API_KIND:-<unset>}"
        echo "VALUZ_DATA_API_URL=${VALUZ_DATA_API_URL:-<unset>}"
        if [ -n "${VALUZ_DATA_API_TOKEN:-}" ]; then
            echo "VALUZ_DATA_API_TOKEN=<set,len=${#VALUZ_DATA_API_TOKEN}>"
        else
            echo "VALUZ_DATA_API_TOKEN=<unset>"
        fi
        echo "VALUZ_DURABLE_DATABASE_URL=${VALUZ_DURABLE_DATABASE_URL:-<unset>}"
        echo "DATABASE_URL=${DATABASE_URL}"
        if [ -n "${KERNEL_AUTH_TOKEN:-}" ]; then
            echo "KERNEL_AUTH_TOKEN=<set,len=${#KERNEL_AUTH_TOKEN}>"
        else
            echo "KERNEL_AUTH_TOKEN=<unset>"
        fi
        echo "---- full env (TOKEN/SECRET/KEY/PASSWORD values masked) ----"
        env | sed -E 's/(TOKEN|SECRET|KEY|PASSWORD)=[^ ]*/\1=***/'
    } > "${_diag_dir}/boot-${_host}.log" 2>&1 || true
    echo "[kernel] boot diagnostics -> ${_diag_dir}/boot-${_host}.log"
else
    echo "[kernel] diag dir ${_diag_dir} not writable; skipping boot dump" >&2
fi

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
# When the mounted diag dir is available, also persist the kernel's own stdout+
# stderr there so runtime logs survive out-of-band (container stdout is
# unreachable in the AGS sandbox). ``exec`` keeps uvicorn as PID 1 (clean signal
# handling); the redirect just points its fds at the mounted file.
if [ "$_diag_ok" = "1" ]; then
    exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT}" \
        --log-level "${LOG_LEVEL}" >> "${_diag_dir}/serve-${_host}.log" 2>&1
else
    exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT}" --log-level "${LOG_LEVEL}"
fi
