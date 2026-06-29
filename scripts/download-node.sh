#!/usr/bin/env bash
# download-node.sh — fetch a pinned, checksum-verified Node.js binary.
#
# Unlike the JS engine tree (committed under
# backend/vendor/chrome-devtools-mcp/), the Node *binary* is ~100 MB/platform —
# too large to commit. It is downloaded + SHA256-verified at build time instead.
# ``scripts/build-desktop.sh`` (Phase A4) calls this to stage ``libexec/node``.
#
# Only the single ``node`` executable is extracted (we invoke
# ``node <chrome-devtools entry>`` directly — npm/npx are not needed at runtime).
#
# Usage:
#   ./scripts/download-node.sh                          # host platform → default out
#   ./scripts/download-node.sh --target=darwin-arm64 --out=/path/to/node
#   NODE_VERSION=22.12.0 ./scripts/download-node.sh     # override the pin
#
# --target uses Node's own tokens: darwin-arm64 | darwin-x64 | linux-arm64 |
#          linux-x64 | win-x64 | win-arm64 (defaults to the host).
# --out    output binary path (defaults to
#          frontend/apps/desktop/resources/libexec/node[.exe]).

set -euo pipefail

# Node 22 LTS ("Jod") — satisfies chrome-devtools-mcp engines
# (^20.19.0 || ^22.12.0 || >=23). Bump here (and re-test) to take security
# updates; CI may override via the NODE_VERSION env.
NODE_VERSION="${NODE_VERSION:-22.12.0}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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

detect_target() {
  local os arch
  os="$(uname -s)"; arch="$(uname -m)"
  case "$os" in
    Darwin) case "$arch" in
              arm64|aarch64) echo "darwin-arm64" ;;
              x86_64)        echo "darwin-x64" ;;
              *) echo "unsupported arch: $arch" >&2; exit 1 ;; esac ;;
    Linux)  case "$arch" in
              aarch64|arm64) echo "linux-arm64" ;;
              x86_64)        echo "linux-x64" ;;
              *) echo "unsupported arch: $arch" >&2; exit 1 ;; esac ;;
    MINGW*|MSYS*|CYGWIN*) case "$arch" in
              x86_64) echo "win-x64" ;;
              aarch64|arm64) echo "win-arm64" ;;
              *) echo "unsupported arch: $arch" >&2; exit 1 ;; esac ;;
    *) echo "unsupported OS: $os" >&2; exit 1 ;;
  esac
}

[ -n "$TARGET" ] || TARGET="$(detect_target)"

case "$TARGET" in
  # NB: ``win-*`` not ``*win*`` — ``*win*`` also matches "dar**win**", which
  # picked the .zip archive for macOS and 404'd both mac jobs on the v0.2.1 build.
  win-*) EXT="zip";    INNER="node.exe" ;;
  linux-*) EXT="tar.xz"; INNER="bin/node" ;;
  darwin-*) EXT="tar.gz"; INNER="bin/node" ;;
  *) echo "Unsupported target: $TARGET" >&2; exit 1 ;;
esac

if [ -z "$OUT" ]; then
  OUT="$REPO_ROOT/frontend/apps/desktop/resources/libexec/node"
  [ "$EXT" = "zip" ] && OUT="${OUT}.exe"
fi

ARCHIVE="node-v${NODE_VERSION}-${TARGET}.${EXT}"

# Mirror bases tried in order until one serves the file. The official dist is
# first; npmmirror (Alibaba) is a full mirror of the same layout, used as a
# fallback when nodejs.org's CDN serves a transient 404 or is unreachable — it
# hit both macOS runners on the v0.2.1 build. Override the whole list via the
# NODE_MIRRORS env (space-separated bases; "/v<version>/<file>" is appended).
NODE_MIRRORS="${NODE_MIRRORS:-https://nodejs.org/dist https://registry.npmmirror.com/-/binary/node}"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# fetch <relative-file> <out-path>: try each mirror, succeed on the first hit.
# --retry-all-errors also retries a 4xx (plain --retry only covers network/5xx),
# so a momentary CDN 404 doesn't kill the build. The SHA256 check below stays the
# integrity boundary; SHASUMS is fetched independently (so it still prefers the
# official list) — a mirror serving a bad archive is caught against it.
fetch() {
  local rel="$1" out="$2" base
  for base in $NODE_MIRRORS; do
    if curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors \
         -o "$out" "${base}/v${NODE_VERSION}/${rel}"; then
      return 0
    fi
    echo "  ${base} failed for ${rel}; trying next mirror ..." >&2
  done
  echo "ERROR: could not download ${rel} from any mirror (${NODE_MIRRORS})" >&2
  return 1
}

echo "Downloading Node ${NODE_VERSION} for ${TARGET} ..."
fetch "$ARCHIVE" "$TMPDIR/$ARCHIVE"
fetch "SHASUMS256.txt" "$TMPDIR/SHASUMS256.txt"

echo "Verifying SHA256 ..."
EXPECTED="$(grep "  ${ARCHIVE}\$" "$TMPDIR/SHASUMS256.txt" | awk '{print $1}')"
[ -n "$EXPECTED" ] || { echo "ERROR: $ARCHIVE not found in SHASUMS256.txt" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "$TMPDIR/$ARCHIVE" | awk '{print $1}')"
else
  ACTUAL="$(shasum -a 256 "$TMPDIR/$ARCHIVE" | awk '{print $1}')"
fi
[ "$EXPECTED" = "$ACTUAL" ] || { echo "ERROR: SHA256 mismatch for $ARCHIVE" >&2; exit 1; }

echo "Extracting node binary ..."
case "$EXT" in
  tar.gz) tar xzf "$TMPDIR/$ARCHIVE" -C "$TMPDIR" ;;
  tar.xz) tar xJf "$TMPDIR/$ARCHIVE" -C "$TMPDIR" ;;
  zip)    unzip -q "$TMPDIR/$ARCHIVE" -d "$TMPDIR" ;;
esac

SRC="$TMPDIR/node-v${NODE_VERSION}-${TARGET}/$INNER"
[ -f "$SRC" ] || { echo "ERROR: node binary not found at $SRC" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
cp "$SRC" "$OUT"
chmod +x "$OUT"
echo "Installed: $OUT"

# Only run --version when the binary is native to this host (cross-platform
# fetches, e.g. windows on a mac, can't execute here).
if [ "$TARGET" = "$(detect_target 2>/dev/null || echo none)" ]; then
  "$OUT" --version
fi
