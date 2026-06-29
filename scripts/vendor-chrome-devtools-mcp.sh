#!/usr/bin/env bash
# Vendor refresh helper — NOT called by the build pipeline.
#
# (Re)installs the pinned ``chrome-devtools-mcp`` package into
# ``backend/vendor/chrome-devtools-mcp/`` to refresh / bump the pin. Only
# ``package.json`` + ``package-lock.json`` are committed (node_modules is
# gitignored); ``scripts/build-desktop.sh`` (Phase A4) runs ``npm ci`` from the
# lockfile at packaging time and stages the result into libexec.
#
# The JS tree is platform-independent (deps are bundled into the package's
# build/), so one install serves every platform. The node *binary* that runs it
# is downloaded separately at build time — see scripts/download-node.sh.
#
# Usage:
#   ./scripts/vendor-chrome-devtools-mcp.sh            # reinstall current pin
#   ./scripts/vendor-chrome-devtools-mcp.sh 1.3.0      # bump pin, reinstall
#
# After running: review the diff, commit node_modules + package-lock.json, and
# keep infra/config.py::chrome_devtools_version + the dev npx fallback in sync.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/backend/vendor/chrome-devtools-mcp"

command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }

cd "$VENDOR_DIR"

VERSION="${1:-}"
if [ -n "$VERSION" ]; then
  echo "Pinning chrome-devtools-mcp@$VERSION in package.json ..."
  # Portable sed: rewrite the pinned dependency version.
  sed -i.bak -E 's/("chrome-devtools-mcp"[[:space:]]*:[[:space:]]*)"[^"]*"/\1"'"$VERSION"'"/' package.json
  rm -f package.json.bak
fi

echo "Reinstalling vendored tree (production deps only) ..."
rm -rf node_modules
npm install --omit=dev --no-audit --no-fund --loglevel=error

ENTRY="node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools.js"
[ -f "$ENTRY" ] || { echo "ERROR: CLI entry missing after install: $ENTRY" >&2; exit 1; }

INSTALLED="$(node -e "console.log(require('./node_modules/chrome-devtools-mcp/package.json').version)")"
echo "Pinned chrome-devtools-mcp@$INSTALLED (node_modules is gitignored — built at packaging via npm ci)."
echo "Entry (after build-time npm ci): $VENDOR_DIR/$ENTRY"
echo "Now: review the diff and commit package.json + package-lock.json."
