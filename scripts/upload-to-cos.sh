#!/usr/bin/env bash
# upload-to-cos.sh — Upload desktop release artifacts to Tencent COS.
#
# Uploads two things from a release directory:
#   1. Every distributable artifact (*.dmg, *.zip, *.exe, *.AppImage, *.deb,
#      *.blockmap, latest*.yml) to ${EDITION}/v${VERSION}/ — immutable per release.
#   2. The named manifest(s) (e.g. "latest-mac.yml") also overwrite
#      ${EDITION}/<name> — the live feed URL electron-updater reads.
#
# Env (required unless --dry-run):
#   TENCENT_SECRET_ID, TENCENT_SECRET_KEY, TENCENT_COS_BUCKET, TENCENT_COS_REGION
#
# Usage:
#   scripts/upload-to-cos.sh \
#     --edition=oss \
#     --version=0.1.5 \
#     --release-dir=frontend/apps/desktop/release/ \
#     --manifests="latest-mac.yml"
#
#   scripts/upload-to-cos.sh ... --dry-run   # print actions, upload nothing

set -euo pipefail

EDITION=""
VERSION=""
RELEASE_DIR=""
MANIFESTS=""
DRY_RUN=false

while [ $# -gt 0 ]; do
  case "$1" in
    --edition=*)     EDITION="${1#--edition=}" ;;
    --version=*)     VERSION="${1#--version=}" ;;
    --release-dir=*) RELEASE_DIR="${1#--release-dir=}" ;;
    --manifests=*)   MANIFESTS="${1#--manifests=}" ;;
    --dry-run)       DRY_RUN=true ;;
    --help|-h)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

[ -n "$EDITION" ]     || { echo "ERROR: --edition required" >&2; exit 1; }
[ -n "$VERSION" ]     || { echo "ERROR: --version required" >&2; exit 1; }
[ -n "$RELEASE_DIR" ] || { echo "ERROR: --release-dir required" >&2; exit 1; }
[ -d "$RELEASE_DIR" ] || { echo "ERROR: release dir not found: $RELEASE_DIR" >&2; exit 1; }

VERSIONED_PREFIX="${EDITION}/v${VERSION}"
LIVE_PREFIX="${EDITION}"
BUCKET_DISPLAY="${TENCENT_COS_BUCKET:-<bucket>}"

if $DRY_RUN; then
  echo "[dry-run] Artifacts in $RELEASE_DIR → cos://${BUCKET_DISPLAY}/${VERSIONED_PREFIX}/"
  echo "[dry-run] Manifests → cos://${BUCKET_DISPLAY}/${LIVE_PREFIX}/:"
  for m in $MANIFESTS; do echo "    - $m"; done
  exit 0
fi

for v in TENCENT_SECRET_ID TENCENT_SECRET_KEY TENCENT_COS_BUCKET TENCENT_COS_REGION; do
  [ -n "${!v:-}" ] || { echo "ERROR: env $v required when not --dry-run" >&2; exit 1; }
done
command -v tccli >/dev/null 2>&1 || { echo "ERROR: tccli not installed (pip install tccli)" >&2; exit 1; }

tccli configure set \
  secretId   "$TENCENT_SECRET_ID" \
  secretKey  "$TENCENT_SECRET_KEY" \
  region     "$TENCENT_COS_REGION"

echo "[cos] Uploading artifacts → /${VERSIONED_PREFIX}/"
tccli cos UploadBunch \
  --bucket     "$TENCENT_COS_BUCKET" \
  --local-path "$RELEASE_DIR" \
  --cos-dir    "/${VERSIONED_PREFIX}/" \
  --include    "*.dmg;*.zip;*.exe;*.AppImage;*.deb;*.blockmap;latest*.yml" \
  --skip-dotdir \
  --recursive

for m in $MANIFESTS; do
  if [ ! -f "$RELEASE_DIR/$m" ]; then
    echo "WARN: manifest $m not in $RELEASE_DIR — skipping live copy" >&2
    continue
  fi

  # The live manifest sits at ${LIVE_PREFIX}/<name>, but its artifacts live
  # one level down at ${VERSIONED_PREFIX}/. electron-builder emits the
  # manifest with bare filenames (url: Valuz-x.y.z-arm64.dmg), which would
  # resolve to ${LIVE_PREFIX}/Valuz-x.y.z-arm64.dmg — a 404. Rewrite the
  # url:/path: fields to carry the v${VERSION}/ prefix so they resolve
  # correctly. The original manifest is already archived unchanged at
  # ${VERSIONED_PREFIX}/<name> by UploadBunch above; its relative URLs
  # work bare because the artifacts sit next to it.
  tmp="$(mktemp)"
  sed -e 's|url: |url: v'"${VERSION}"'/|g' \
      -e 's|^path: |path: v'"${VERSION}"'/|' \
      "$RELEASE_DIR/$m" > "$tmp"

  echo "[cos] $m → /${LIVE_PREFIX}/${m} (artifacts prefixed with v${VERSION}/)"
  tccli cos PutObject \
    --bucket     "$TENCENT_COS_BUCKET" \
    --local-path "$tmp" \
    --cos-path   "/${LIVE_PREFIX}/${m}"
  rm -f "$tmp"
done

echo "[cos] Done."
