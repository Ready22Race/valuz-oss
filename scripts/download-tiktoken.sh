#!/usr/bin/env bash
# Vendor refresh helper — NOT called by the build pipeline.
#
# Downloads the tiktoken BPE vocabulary used by the goal-mode length fence
# (backend/valuz_agent/adapters/agent_resolver.py ``estimate_tokens``) into the
# project tree so the packaged, offline app can count tokens without ever
# reaching the network.
#
# tiktoken caches a vocab blob under ``$TIKTOKEN_CACHE_DIR/<sha1(blob_url)>``
# and, when that file exists (and its sha256 matches), reads it instead of
# downloading. We pre-seed exactly that file under ``backend/vendor/tiktoken/``
# and point ``TIKTOKEN_CACHE_DIR`` at it at runtime (see
# ``_vendored_tiktoken_cache_dir`` + the PyInstaller spec ``vendor/tiktoken``
# data dir). The file is platform-independent, so a single committed copy
# serves every platform.
#
# Usage:
#   ./scripts/download-tiktoken.sh
#
# Refresh procedure: see backend/vendor/tiktoken/README.md.

set -euo pipefail

# o200k_base — the encoding agent_resolver loads. URL + expected blob sha256 are
# pinned in tiktoken_ext/openai_public.py; bump both here if the encoding changes.
ENCODING="${TIKTOKEN_ENCODING:-o200k_base}"
BLOB_URL="https://openaipublic.blob.core.windows.net/encodings/${ENCODING}.tiktoken"
EXPECTED_SHA256="446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${REPO_ROOT}/backend/vendor/tiktoken"

# tiktoken's cache filename is the sha1 of the blob URL (read_file_cached).
cache_key() {
  printf '%s' "$BLOB_URL" | shasum -a 1 | awk '{print $1}'
}
CACHE_KEY="$(cache_key)"
OUT_FILE="${OUT_DIR}/${CACHE_KEY}"

mkdir -p "$OUT_DIR"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "Downloading tiktoken ${ENCODING} vocab..."
echo "  url:       ${BLOB_URL}"
echo "  cache key: ${CACHE_KEY}"
curl -fsSL -o "$TMP" "$BLOB_URL"

ACTUAL_SHA256="$(shasum -a 256 "$TMP" | awk '{print $1}')"
if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
  echo "ERROR: sha256 mismatch for ${ENCODING}.tiktoken" >&2
  echo "  expected: ${EXPECTED_SHA256}" >&2
  echo "  actual:   ${ACTUAL_SHA256}" >&2
  exit 1
fi

mv "$TMP" "$OUT_FILE"
trap - EXIT
echo "Vendored: ${OUT_FILE}  ($(wc -c < "$OUT_FILE") bytes, sha256 ok)"
