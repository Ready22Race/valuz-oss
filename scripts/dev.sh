#!/usr/bin/env bash
# One-button developer launcher: backend + frontend dev shell.
#
# Starts:
#   1. Vendored Agent Harness V5 kernel migrations (run inside backend startup).
#   2. valuz_agent backend on http://127.0.0.1:${VALUZ_BACKEND_PORT:-8000}
#      (uses ``python -m valuz_agent`` so the kernel routes mount under
#      ``/api/v1/*`` automatically).
#   3. Frontend desktop dev shell (Vite renderer on :1420 + main/preload
#      watch builds + Electron when both are ready).
#
# Stops everything on Ctrl+C via a trap.
#
# Usage:
#   scripts/dev.sh                     # backend + desktop (default)
#   scripts/dev.sh backend             # just the backend
#   scripts/dev.sh frontend            # just the desktop dev shell
#   VALUZ_BACKEND_PORT=18080 scripts/dev.sh
#   VALUZ_RELOAD=1 scripts/dev.sh      # uvicorn --reload
#
# Kernel sandbox (flags may appear before or after the target):
#   scripts/dev.sh --seatbelt          # run the kernel in a local Seatbelt sandbox (macOS)
#   scripts/dev.sh --ags backend       # run the kernel in a remote AGS cloud sandbox
#   (no flag)                          # kernel in-process (default)
#
# ``--ags`` additionally sources ``backend/.env`` (pydantic Settings has no
# env_file, so VALUZ_AGS_* / VALUZ_COS_* must be in the process env) and syncs
# the ``ags`` uv extra (the e2b SDK). It is the env-driven counterpart to the
# UI-driven config in Settings → Kernel.
#
# Logs: backend writes to .ai/dev/backend.log, frontend to .ai/dev/frontend.log.
# Both also tee to the foreground so Ctrl+C surfaces failures fast.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/.ai/dev"
BACKEND_PORT="${VALUZ_BACKEND_PORT:-8000}"
RELOAD_FLAG=""
[[ "${VALUZ_RELOAD:-}" == "1" ]] && RELOAD_FLAG="--reload"

# uv extras the backend venv needs. ``dev`` = pytest/ruff/mypy. ``ags`` =
# e2b + boto3, needed by the Settings → Cloud Sandbox panel (which is ALWAYS
# present): configuring AGS/COS from the UI and clicking Sync/Save touches the
# COS client even on a plain ``make dev``, so the dev venv carries it too —
# otherwise the UI 500s with "No module named 'boto3'". (Packaged builds gate
# this separately.) Honour an inherited VALUZ_SANDBOX_DRIVER before flag parsing.
EXTRAS=(--extra dev --extra ags)
SANDBOX_DRIVER="${VALUZ_SANDBOX_DRIVER:-}"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${CYAN}[dev]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok ]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*"; }

mkdir -p "$LOG_DIR"

# ── Prerequisites ──────────────────────────────────────────────────────────
need() { command -v "$1" >/dev/null 2>&1 || { err "$1 not found"; exit 1; }; }
need uv
need pnpm

# ── Trap teardown ──────────────────────────────────────────────────────────
PIDS=()
cleanup() {
    info "shutting down…"
    # ``${PIDS[@]+...}`` guards the expansion: under ``set -u`` (and macOS's
    # stock bash 3.2) a bare ``"${PIDS[@]}"`` on an empty array raises
    # "unbound variable" — which fired in cleanup() when an early ``uv sync``
    # failure tripped the trap before any PID was recorded, masking the real
    # error with a confusing ``PIDS[@]: unbound variable``.
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do
        kill "$pid" 2>/dev/null || true
    done
    # Kill any straggling Electron windows the dev shell spawned.
    pkill -f "Valuz.app" 2>/dev/null || true
    pkill -f "concurrently.*vite" 2>/dev/null || true
    # AGS: the cloud sandbox is 常驻 — it does NOT die with the local backend.
    # Best-effort kill the ONE this session provisioned (parsed from the log),
    # so iterating with ``make dev-ags`` doesn't pile up orphan sandboxes.
    if [[ "${SANDBOX_DRIVER:-}" == "ags" && -f "$LOG_DIR/backend.log" ]]; then
        local sb
        sb=$(grep -oE "kernel running in ags sandbox at https://8000-[a-z0-9]+" "$LOG_DIR/backend.log" 2>/dev/null \
             | tail -1 | sed -E 's#.*8000-##')
        if [[ -n "$sb" ]]; then
            info "killing AGS sandbox $sb…"
            ( cd "$BACKEND_DIR" && uv run --extra ags python -c "import asyncio,os
from e2b import AsyncSandbox
asyncio.run(AsyncSandbox.kill('$sb', api_key=os.environ.get('VALUZ_AGS_API_KEY') or os.environ.get('E2B_API_KEY'), domain=os.environ['VALUZ_AGS_DOMAIN']))" 2>/dev/null ) \
                && ok "AGS sandbox killed" || warn "could not kill AGS sandbox $sb — check the AGS console"
        fi
    fi
    wait 2>/dev/null || true
    ok "stopped"
}
trap cleanup EXIT INT TERM

# ── Service functions ──────────────────────────────────────────────────────
install_backend() {
    info "installing backend deps…"
    cd "$BACKEND_DIR"
    # ``--extra dev`` so the dev launcher provisions the dev toolchain
    # (pytest / ruff / mypy) alongside runtime deps. Plain ``uv sync`` prunes
    # the ``dev`` optional extra from .venv, which would silently break
    # ``uv run pytest`` after every startup (ModuleNotFoundError: pytest).
    # (Runtime OCR deps live in the DEFAULT dependencies, so they are not
    # pruned here and need no extra.) ``--ags`` adds the ``ags`` extra so the
    # e2b SDK is importable when provisioning the cloud kernel.
    uv sync "${EXTRAS[@]}"
    ok "backend deps ready"
}

start_backend() {
    local log_file="$LOG_DIR/backend.log"
    info "backend → http://127.0.0.1:$BACKEND_PORT (log: $log_file)"
    cd "$BACKEND_DIR"
    uv run "${EXTRAS[@]}" python -m valuz_agent --host 127.0.0.1 --port "$BACKEND_PORT" $RELOAD_FLAG \
        2>&1 | python3 "$ROOT_DIR/scripts/devlog.py" "$log_file" &
    PIDS+=("$!")

    # Wait for the backend to come up. ``--noproxy '*'`` keeps the localhost
    # probe off any system/terminal HTTP proxy (curl proxies even 127.0.0.1
    # when http_proxy is exported, e.g. by Clash). AGS provisions the cloud
    # sandbox synchronously BEFORE serving, so it needs a longer window.
    local wait_s=30
    [[ "$SANDBOX_DRIVER" == "ags" ]] && wait_s=150
    for _ in $(seq 1 "$wait_s"); do
        if curl -sS --noproxy '*' -o /dev/null -w "%{http_code}" "http://127.0.0.1:$BACKEND_PORT/v1/system/status" 2>/dev/null | grep -q '^200$'; then
            ok "backend ready"
            return 0
        fi
        sleep 1
    done
    warn "backend did not respond within ${wait_s}s — check $log_file"
}

install_frontend() {
    info "installing frontend deps…"
    cd "$FRONTEND_DIR"
    pnpm install
    ok "frontend deps ready"
}

start_frontend() {
    local log_file="$LOG_DIR/frontend.log"
    info "frontend desktop dev (log: $log_file)"
    cd "$FRONTEND_DIR"
    pnpm --filter @valuz/desktop dev \
        2>&1 | python3 "$ROOT_DIR/scripts/devlog.py" "$log_file" &
    PIDS+=("$!")
}

# ── Arg parsing ────────────────────────────────────────────────────────────
# Flags (--seatbelt / --ags / --in-process) and the target (all|backend|
# frontend) may appear in any order.
TARGET=""
for arg in "$@"; do
    case "$arg" in
        --seatbelt)               SANDBOX_DRIVER="seatbelt" ;;
        --ags)                    SANDBOX_DRIVER="ags" ;;
        --in-process|--inprocess) SANDBOX_DRIVER="" ;;
        all|backend|frontend)     TARGET="$arg" ;;
        -*)  err "unknown flag: $arg (expected --seatbelt|--ags|--in-process)"; exit 1 ;;
        *)   err "unknown target: $arg (expected all|backend|frontend)"; exit 1 ;;
    esac
done
TARGET="${TARGET:-all}"

# ── Sandbox driver ─────────────────────────────────────────────────────────
configure_sandbox() {
    case "$SANDBOX_DRIVER" in
        seatbelt)
            export VALUZ_SANDBOX_DRIVER=seatbelt
            info "kernel sandbox: seatbelt (local macOS)"
            ;;
        ags)
            export VALUZ_SANDBOX_DRIVER=ags
            # EXTRAS already includes --extra ags (default).
            # Settings has no env_file — AGS/COS config must be in the process
            # env. Source backend/.env (the same secrets the kill/e2e scripts
            # use); never commit it.
            if [[ -f "$BACKEND_DIR/.env" ]]; then
                info "loading AGS/COS config from backend/.env"
                set -a; source "$BACKEND_DIR/.env"; set +a
            else
                warn "backend/.env not found — set VALUZ_AGS_* / VALUZ_COS_* yourself"
            fi
            if [[ -z "${VALUZ_AGS_API_KEY:-${E2B_API_KEY:-}}" || -z "${VALUZ_AGS_DOMAIN:-}" || -z "${VALUZ_AGS_KERNEL_TEMPLATE:-}" ]]; then
                warn "AGS config incomplete (need VALUZ_AGS_API_KEY, VALUZ_AGS_DOMAIN, VALUZ_AGS_KERNEL_TEMPLATE)"
                warn "→ the backend will warn and fall back to the in-process kernel"
            else
                info "kernel sandbox: ags (remote cloud → ${VALUZ_AGS_DOMAIN})"
            fi
            warn "AGS: the cloud sandbox is 常驻 (no auto-timeout). On Ctrl+C this script"
            warn "     best-effort kills the one it started; if it's force-killed (kill -9),"
            warn "     remove leftovers via the AGS console."
            ;;
        "")
            : # in-process kernel (default) — nothing to configure
            ;;
        *)
            err "unknown sandbox driver: $SANDBOX_DRIVER (expected seatbelt|ags)"; exit 1 ;;
    esac
}
configure_sandbox

# ── Dispatch ───────────────────────────────────────────────────────────────
case "$TARGET" in
    all)
        install_backend
        install_frontend
        start_backend
        start_frontend
        ;;
    backend)
        install_backend
        start_backend
        ;;
    frontend)
        install_frontend
        start_frontend
        ;;
    *)
        err "unknown target: $TARGET (expected: all|backend|frontend)"
        exit 1
        ;;
esac

ok "all services running — Ctrl+C to stop"
wait
