#!/usr/bin/env bash
# install-coscli.sh — download and install Tencent coscli for the host platform.
#
# tccli does NOT support COS as a subcommand (COS has its own API surface,
# separate from the Tencent Cloud API 3.0 that tccli wraps). coscli is the
# official Go CLI for COS — this helper downloads the prebuilt binary so CI
# doesn't need a Go toolchain.
#
# Used by .github/workflows/release-desktop.yml to set up coscli before
# scripts/upload-to-cos.sh runs. Mirrors the install-node.sh / download-rg.sh
# pattern: pinned version, SHA256-verified, single binary on PATH.
#
# Usage:
#   scripts/install-coscli.sh               # host platform → /usr/local/bin (POSIX)
#                                           #                → ~/bin (Windows)
#   scripts/install-coscli.sh --to=/path    # install to a specific dir
#   COSCLI_VERSION=v1.0.8 scripts/install-coscli.sh

set -euo pipefail

# Pin the upstream release. Bump here (and re-test) to take updates.
COSCLI_VERSION="${COSCLI_VERSION:-v1.0.8}"

TO=""
for arg in "$@"; do
  case "$arg" in
    --to=*) TO="${arg#--to=}" ;;
    --help|-h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

# Detect OS + arch → coscli release-asset token.
EXE=""
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64|Darwin/aarch64)       TARGET="darwin-arm64" ;;
  Darwin/x86_64|Darwin/amd64)        TARGET="darwin-amd64" ;;
  Linux/aarch64|Linux/arm64)         TARGET="linux-arm64" ;;
  Linux/x86_64|Linux/amd64)          TARGET="linux-amd64" ;;
  MINGW*/x86_64|MSYS*/x86_64|CYGWIN*/x86_64)
    TARGET="windows-amd64"; EXE=".exe" ;;
  *) echo "ERROR: unsupported platform: $(uname -s)/$(uname -m)" >&2; exit 1 ;;
esac

# Default install dir: /usr/local/bin on POSIX (runners are sudo-less but the
# dir is writable on GitHub Actions images); ~/bin on Windows where
# /usr/local/bin doesn't exist.
if [ -z "$TO" ]; then
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) TO="$HOME/bin" ;;
    *)                    TO="/usr/local/bin" ;;
  esac
fi

ASSET="coscli-${COSCLI_VERSION}-${TARGET}${EXE}"
URL="https://github.com/tencentyun/coscli/releases/download/${COSCLI_VERSION}/${ASSET}"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading coscli ${COSCLI_VERSION} for ${TARGET} ..."
# --fail surfaces a 4xx; --retry-all-errors also retries it so a momentary
# release-asset 404 doesn't kill the build.
curl -fSL --retry 5 --retry-delay 3 --retry-all-errors \
  -o "$TMPDIR/coscli${EXE}" "$URL"

# SHA256-verify against the published sha256sum.log (every coscli release ships
# one). Optional — if the file is unreachable we warn but don't fail, since the
# download itself came over HTTPS from github.com.
SHA_URL="https://github.com/tencentyun/coscli/releases/download/${COSCLI_VERSION}/sha256sum.log"
if curl -fsSL --retry 3 -o "$TMPDIR/sha256sum.log" "$SHA_URL"; then
  EXPECTED="$(grep "  ${ASSET}\$" "$TMPDIR/sha256sum.log" | awk '{print $1}')"
  if [ -n "$EXPECTED" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
      ACTUAL="$(sha256sum "$TMPDIR/coscli${EXE}" | awk '{print $1}')"
    else
      ACTUAL="$(shasum -a 256 "$TMPDIR/coscli${EXE}" | awk '{print $1}')"
    fi
    [ "$EXPECTED" = "$ACTUAL" ] || { echo "ERROR: SHA256 mismatch for coscli" >&2; exit 1; }
    echo "SHA256 verified."
  else
    echo "WARN: ${ASSET} not in sha256sum.log — skipping verification" >&2
  fi
else
  echo "WARN: could not fetch sha256sum.log — skipping verification" >&2
fi

mkdir -p "$TO"
mv "$TMPDIR/coscli${EXE}" "$TO/coscli${EXE}"
chmod +x "$TO/coscli${EXE}"
echo "Installed: $TO/coscli${EXE}"

"$TO/coscli${EXE}" --version
