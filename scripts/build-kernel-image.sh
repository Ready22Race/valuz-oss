#!/usr/bin/env bash
# Build (and optionally push) the Valuz agent-harness KERNEL container image —
# the data plane that runs inside a cloud sandbox (Volcengine veFaaS et al.).
#
# Usage:
#   KERNEL_IMAGE=<registry>/<namespace>/valuz-kernel:<tag> \
#     scripts/build-kernel-image.sh [--push]
#
# Env knobs:
#   KERNEL_IMAGE   full image ref incl. registry (REQUIRED), e.g.
#                  cn-beijing.cr.volces.com/<ns>/valuz-kernel:0.1.0
#   PLATFORM       target platform (default: linux/amd64 — veFaaS default;
#                  use linux/arm64 for an arm cluster, or a comma list to
#                  build a multi-arch manifest, which requires --push)
#   PYTHON_VERSION base python (default: 3.12)
#
# Push to Volcengine Container Registry (CR) first needs a docker login:
#   docker login <registry>            # e.g. cn-beijing.cr.volces.com
#   # username/password from the CR instance's "访问控制 / 用户凭证" page.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTEXT="${REPO_ROOT}/backend"
DOCKERFILE="${CONTEXT}/docker/kernel.Dockerfile"

KERNEL_IMAGE="${KERNEL_IMAGE:-}"
PLATFORM="${PLATFORM:-linux/amd64}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

PUSH=0
[ "${1:-}" = "--push" ] && PUSH=1

if [ -z "${KERNEL_IMAGE}" ]; then
    echo "error: set KERNEL_IMAGE=<registry>/<ns>/valuz-kernel:<tag>" >&2
    echo "  e.g. KERNEL_IMAGE=cn-beijing.cr.volces.com/myns/valuz-kernel:0.1.0 \\" >&2
    echo "         scripts/build-kernel-image.sh --push" >&2
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker not found on PATH" >&2
    exit 2
fi

echo "▸ image     : ${KERNEL_IMAGE}"
echo "▸ platform  : ${PLATFORM}"
echo "▸ context   : ${CONTEXT}"
echo "▸ dockerfile: ${DOCKERFILE}"
echo "▸ push      : $([ "${PUSH}" = 1 ] && echo yes || echo 'no (local load)')"
echo

OUTPUT_FLAG="--load"
if [ "${PUSH}" = 1 ]; then
    OUTPUT_FLAG="--push"
elif [[ "${PLATFORM}" == *","* ]]; then
    echo "error: multi-arch (${PLATFORM}) cannot --load; pass --push" >&2
    exit 2
fi

# buildx gives reproducible cross-arch builds; the LINUX wheels of
# claude-agent-sdk / openai-codex bundle the LINUX runtime binaries, so the
# resulting image carries a working `claude` (and `codex`, subject to the
# linux override note in the Dockerfile) with no Node.js.
# Optional PyPI mirror (faster builds in CN): UV_DEFAULT_INDEX=https://…
INDEX_ARG=()
[ -n "${UV_DEFAULT_INDEX:-}" ] && INDEX_ARG=(--build-arg "UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX}")

docker buildx build \
    --platform "${PLATFORM}" \
    --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
    "${INDEX_ARG[@]}" \
    -f "${DOCKERFILE}" \
    -t "${KERNEL_IMAGE}" \
    ${OUTPUT_FLAG} \
    "${CONTEXT}"

echo
echo "✓ built ${KERNEL_IMAGE}"
if [ "${PUSH}" != 1 ]; then
    echo "  smoke-test locally:"
    echo "    docker run --rm -p 8000:8000 -e KERNEL_AUTH_TOKEN=devtoken ${KERNEL_IMAGE}"
    echo "    curl -s localhost:8000/health   # {\"status\":\"ok\"}"
fi
