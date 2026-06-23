#!/usr/bin/env bash
# download-claude-code.sh — fetch an official Claude Code CLI binary for Linux.
#
# PyInstaller bundles whatever ``claude`` ships inside the ``claude_agent_sdk``
# Python package (at ``_internal/claude_agent_sdk/_bundled/claude``). On Linux
# we override that with a pinned official release from
# ``github.com/anthropics/claude-code`` — the SDK-bundled binary's CLI surface
# is not what we want to ship. macOS/Windows keep the SDK's binary; the caller
# (``scripts/build-desktop.sh`` Phase A1) gates this script on Linux.
#
# The Claude Code GitHub releases publish no SHASUMS file, so the HTTPS transport
# is the only integrity boundary (download comes straight from anthropics' repo).
#
# Usage:
#   ./scripts/download-claude-code.sh --target=linux-x64 --out=/path/to/claude
#   ./scripts/download-claude-code.sh --target=linux-arm64 --out=/path/to/claude
#   CLAUDE_CODE_VERSION=2.1.185 ./scripts/download-claude-code.sh --target=...
#
# --target uses the release-asset token: linux-x64 | linux-arm64.
# --out    output binary path (caller picks — typically the staged libexec path).

set -euo pipefail

# Pinned by default; override with CLAUDE_CODE_VERSION to bump.
CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION:-2.1.185}"

TARGET=""
OUT=""
for arg in "$@"; do
  case "$arg" in
    --target=*) TARGET="${arg#--target=}" ;;
    --out=*)    OUT="${arg#--out=}" ;;
    --help|-h)  grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

case "$TARGET" in
  linux-x64|linux-arm64) ;;
  *) echo "ERROR: --target must be linux-x64 or linux-arm64 (got: ${TARGET:-<empty>})" >&2; exit 1 ;;
esac

if [ -z "$OUT" ]; then
  echo "ERROR: --out is required" >&2; exit 1
fi

URL="https://github.com/anthropics/claude-code/releases/download/v${CLAUDE_CODE_VERSION}/claude-${TARGET}.tar.gz"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading Claude Code v${CLAUDE_CODE_VERSION} for ${TARGET} ..."
# --fail makes a 4xx visible (default curl silently writes the error body);
# --retry-all-errors also retries 4xx a few times so a momentary release-asset
# 404 doesn't kill the build.
curl -fSL --retry 5 --retry-delay 3 --retry-all-errors \
  -o "$TMPDIR/claude.tar.gz" "$URL"

echo "Extracting claude binary ..."
# The release-asset layout is not documented; extract defensively and locate
# the binary by name. Absorbs both ``./claude`` and ``claude-linux-x64/claude``
# (or any other wrapper directory) without hard-coding either.
tar xzf "$TMPDIR/claude.tar.gz" -C "$TMPDIR"
SRC="$(find "$TMPDIR" -type f -name claude | head -n 1 || true)"
if [ -z "$SRC" ]; then
  echo "ERROR: no 'claude' binary found inside the tarball" >&2; exit 1
fi

mkdir -p "$(dirname "$OUT")"
cp "$SRC" "$OUT"
chmod +x "$OUT"
echo "Installed: $OUT ($(du -h "$OUT" | cut -f1))"
