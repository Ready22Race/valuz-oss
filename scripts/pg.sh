#!/usr/bin/env bash
# Local Postgres (podman) helper for the DataService "remote sync" backend.
#
# This ONLY brings up a Postgres — it does not start a data service, a sandbox,
# or the app. Everything else (turn on remote sync, point at this PG, sandbox or
# not) is driven from the OSS settings page: Settings → Data Service (revealed by
# tapping About 9×). This decouples infra from behaviour.
#
# Usage:
#   scripts/pg.sh up      # start Postgres, print the DSN to paste into settings
#   scripts/pg.sh dsn     # just print the connection strings
#   scripts/pg.sh down    # stop the container (data is preserved)
#   scripts/pg.sh nuke     # remove the container + its data
set -euo pipefail

PG_CONTAINER="${VALUZ_PG_CONTAINER:-valuz-pg}"
PG_IMAGE="${VALUZ_PG_IMAGE:-docker.io/library/postgres:16-alpine}"
PG_PORT="${VALUZ_PG_PORT:-5432}"
PG_DB="valuz_kernel"
PG_SUPER="valuz"
PG_SUPER_PW="valuz"
PG_APP="valuz_app"
PG_APP_PW="app"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info() { printf "${CYAN}[pg]${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}[pg]${NC} %s\n" "$*"; }
err()  { printf "${RED}[pg]${NC} %s\n" "$*" >&2; }

print_dsn() {
  echo ""
  ok "Postgres ready on :$PG_PORT — paste into Settings → Data Service:"
  echo "  pg (in-process, owner role):"
  echo "    postgresql+asyncpg://$PG_SUPER:$PG_SUPER_PW@127.0.0.1:$PG_PORT/$PG_DB"
  echo "  remote data service (non-owner role, RLS-enforced):"
  echo "    postgresql://$PG_APP:$PG_APP_PW@127.0.0.1:$PG_PORT/$PG_DB"
  echo ""
}

up() {
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
  # Non-owner login role so the remote data service enforces RLS (the owner role
  # bypasses it). Idempotent.
  podman exec -i -e PGPASSWORD="$PG_SUPER_PW" "$PG_CONTAINER" \
    psql -U "$PG_SUPER" -d "$PG_DB" -v ON_ERROR_STOP=1 >/dev/null <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$PG_APP') THEN
    CREATE ROLE $PG_APP LOGIN PASSWORD '$PG_APP_PW';
  END IF;
END \$\$;
GRANT ALL ON SCHEMA public TO $PG_APP;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $PG_APP;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $PG_APP;
SQL
  print_dsn
}

case "${1:-up}" in
  up) up ;;
  dsn) print_dsn ;;
  down) podman stop "$PG_CONTAINER" >/dev/null && ok "stopped (data preserved)" ;;
  nuke) podman rm -f "$PG_CONTAINER" >/dev/null 2>&1 && ok "removed container + data" || ok "no container" ;;
  *) err "usage: scripts/pg.sh {up|dsn|down|nuke}"; exit 2 ;;
esac
