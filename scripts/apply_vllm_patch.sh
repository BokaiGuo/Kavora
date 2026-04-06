#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATCH_FILE="${PATCH_FILE:-${ROOT}/patches/vllm-kvcache-metrics.patch}"

if [[ ! -f "${PATCH_FILE}" ]]; then
  echo "[patch] no file at ${PATCH_FILE} (set PATCH_FILE=... to override)" >&2
  exit 0
fi

cd "${ROOT}"
git apply "${PATCH_FILE}"
echo "[patch] applied ${PATCH_FILE}"
