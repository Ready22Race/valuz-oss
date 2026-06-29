#!/usr/bin/env bash
# Local REMOTE-store dev stack: Postgres (podman) + kernel data service +
# backend in a Seatbelt sandbox talking to it over HTTP. Exercises the SaaS
# data path locally — the sandbox holds NO DB credentials (only a JWT + the
# data-API URL); session/message/event data lives in Postgres and survives
# sandbox restarts.
#
#   make dev-remote                 # PG + data service + backend + desktop
#   make dev-remote TARGET=backend  # …without the desktop
#
# Env knobs: VALUZ_PG_CONTAINER, VALUZ_PG_PORT, VALUZ_DATA_API_PORT,
#            VALUZ_BACKEND_PORT, VALUZ_PG_IMAGE.
#
# Ctrl+C stops the backend/desktop + data service. The Postgres CONTAINER is
# left running (so data persists between runs) — stop it with
# ``podman stop <container>`` or remove with ``podman rm -f <container>``.
#
# Provider keys (OPENAI_API_KEY / ANTHROPIC_API_KEY) are inherited from the
# shell you run this in — agent turns need one. Run from your interactive
# shell (or ``source ~/.zshrc`` first) so the key is exported.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
LOG_DIR="$ROOT_DIR/.ai/dev"
mkdir -p "$LOG_DIR"

PG_CONTAINER="${VALUZ_PG_CONTAINER:-valuz-pg}"
PG_IMAGE="${VALUZ_PG_IMAGE:-docker.io/library/postgres:16-alpine}"
PG_PORT="${VALUZ_PG_PORT:-5432}"
PG_DB="valuz_kernel"
PG_SUPER="valuz"
PG_SUPER_PW="valuz"
PG_APP="valuz_app"
PG_APP_PW="app"
DATA_API_PORT="${VALUZ_DATA_API_PORT:-8400}"
# One shared HS256 secret for this run: the host signer + the data-service
# verifier must match. Fresh per run is fine (each run is a fresh stack).
SECRET="${VALUZ_DATA_SERVICE_JWT_SECRET:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')}"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${CYAN}[dev-remote]${NC} $*"; }
ok()   { echo -e "${GREEN}[ok ]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*"; }

need() { command -v "$1" >/dev/null 2>&1 || { err "$1 not found"; exit 1; }; }
need podman; need uv; need python3; need curl

DATA_PID=""
cleanup() {
    info "stopping data service…"
    [[ -n "$DATA_PID" ]] && kill "$DATA_PID" 2>/dev/null || true
    info "Postgres container '$PG_CONTAINER' left running (podman stop $PG_CONTAINER)"
}
trap cleanup EXIT INT TERM

# ── 1) Postgres via podman ──────────────────────────────────────────────────
info "ensuring Postgres ($PG_CONTAINER, :$PG_PORT)…"
if podman ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    ok "container already running"
elif podman ps -a --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    podman start "$PG_CONTAINER" >/dev/null
    ok "started existing container"
else
    podman machine start >/dev/null 2>&1 || true
    podman run -d --name "$PG_CONTAINER" \
        -e POSTGRES_DB="$PG_DB" -e POSTGRES_USER="$PG_SUPER" -e POSTGRES_PASSWORD="$PG_SUPER_PW" \
        -p "$PG_PORT:5432" "$PG_IMAGE" >/dev/null
    ok "created container ($PG_IMAGE)"
fi
for _ in $(seq 1 30); do
    podman exec "$PG_CONTAINER" pg_isready -U "$PG_SUPER" -d "$PG_DB" >/dev/null 2>&1 && break
    sleep 1
done
podman exec "$PG_CONTAINER" pg_isready -U "$PG_SUPER" -d "$PG_DB" >/dev/null 2>&1 \
    || { err "Postgres not ready"; exit 1; }

# ── 2) deps (+postgres extra), kernel migrations, non-owner app role ─────────
cd "$BACKEND_DIR"
info "syncing backend deps (+postgres extra)…"
uv sync --extra dev --extra postgres >/dev/null
info "migrating kernel schema on Postgres (0001–0003)…"
DATABASE_URL="postgresql+asyncpg://$PG_SUPER:$PG_SUPER_PW@127.0.0.1:$PG_PORT/$PG_DB" \
    uv run alembic -c alembic/kernel/alembic.ini upgrade head >/dev/null
info "ensuring non-owner app role ($PG_APP — RLS-subject)…"
podman exec "$PG_CONTAINER" psql -U "$PG_SUPER" -d "$PG_DB" -q -v ON_ERROR_STOP=1 \
    -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$PG_APP') THEN CREATE ROLE $PG_APP LOGIN PASSWORD '$PG_APP_PW'; END IF; END \$\$;" \
    -c "GRANT USAGE ON SCHEMA public TO $PG_APP;" \
    -c "GRANT SELECT, INSERT, UPDATE, DELETE ON sessions, messages, events TO $PG_APP;" \
    -c "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $PG_APP;" >/dev/null
ok "database ready"

# ── 3) mint a short-lived JWT for the local owner (shared secret) ───────────
read -r OWNER TOKEN < <(VALUZ_SECRET="$SECRET" PYTHONPATH=. uv run python - <<'PY'
import os
import valuz_agent.boot.kernel  # noqa: F401 — sys.path for src.*
from valuz_agent.infra.local_identity import resolve_local_user_id
from src.core.token_signer import TokenSigner

owner = resolve_local_user_id()
print(owner, TokenSigner(os.environ["VALUZ_SECRET"]).sign(user_id=owner, ttl_s=86400))
PY
)
[[ -n "${TOKEN:-}" ]] || { err "failed to mint data-API token"; exit 1; }
ok "owner=$OWNER — 24h token minted"

# ── 4) data service (connects as the non-owner role → RLS enforced) ─────────
info "data service → http://127.0.0.1:$DATA_API_PORT (log: $LOG_DIR/dataservice.log)"
VALUZ_DATA_SERVICE_JWT_SECRET="$SECRET" \
VALUZ_DATA_SERVICE_DATABASE_URL="postgresql://$PG_APP:$PG_APP_PW@127.0.0.1:$PG_PORT/$PG_DB" \
PYTHONPATH=kernel uv run uvicorn app.data_service:build_app_from_env --factory \
    --host 127.0.0.1 --port "$DATA_API_PORT" > "$LOG_DIR/dataservice.log" 2>&1 &
DATA_PID=$!
for _ in $(seq 1 30); do
    curl -sf --noproxy '*' -m2 "http://127.0.0.1:$DATA_API_PORT/health" >/dev/null 2>&1 && break
    sleep 1
done
curl -sf --noproxy '*' -m2 "http://127.0.0.1:$DATA_API_PORT/health" >/dev/null 2>&1 \
    && ok "data service ready" \
    || { err "data service did not come up — see $LOG_DIR/dataservice.log"; exit 1; }

# ── 5) backend (+ desktop) in seatbelt + remote mode, pointed at the service ─
# Provider keys (OPENAI_API_KEY / ANTHROPIC_API_KEY) are inherited from the
# invoking shell — a normal interactive-shell launch reads them from your
# profile and needs nothing extra.
info "starting backend (seatbelt + remote store) + desktop…"
export VALUZ_SANDBOX_DRIVER=seatbelt
export VALUZ_KERNEL_STORE=remote
export VALUZ_KERNEL_DATA_API_URL="http://127.0.0.1:$DATA_API_PORT"
export VALUZ_KERNEL_DATA_API_TOKEN="$TOKEN"
export VALUZ_KERNEL_DATA_API_KIND=http
bash "$ROOT_DIR/scripts/dev.sh" "${TARGET:-all}"
