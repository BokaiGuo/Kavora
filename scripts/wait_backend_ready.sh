#!/usr/bin/env bash
# Wait until the inference backend responds on HTTP /health (application readiness).
# This avoids starting the exporter while /metrics is still unreachable (no scrape spam in logs).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pipeline_common.sh
source "${ROOT}/scripts/lib/pipeline_common.sh"

BACKEND="${1:-vllm}"
case "${BACKEND}" in
  vllm)
    echo "[wait] backend=vllm url=${VLLM_HEALTH_URL}"
    wait_http_ok "${VLLM_HEALTH_URL}"
    ;;
  sglang)
    echo "[wait] backend=sglang url=${SGLANG_HEALTH_URL}"
    wait_http_ok "${SGLANG_HEALTH_URL}"
    ;;
  *)
    echo "usage: $0 [vllm|sglang]" >&2
    exit 1
    ;;
esac
echo "[wait] ok"
