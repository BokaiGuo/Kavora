#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pipeline_common.sh
source "${ROOT}/scripts/lib/pipeline_common.sh"

ensure_results_tree

if [[ "${1:-}" == "stop" ]]; then
  exec "${ROOT}/scripts/one_click_down.sh"
fi

BACKEND="${ONE_CLICK_BACKEND:-vllm}"
export PID_FILE="${RESULTS_DIR}/vllm.pid"
case "${BACKEND}" in
  vllm)
    bash "${ROOT}/scripts/launch_vllm.sh"
    wait_http_ok "${VLLM_HEALTH_URL}"
    METRICS_URL="${VLLM_METRICS_URL}"
    ;;
  sglang)
    export PID_FILE="${RESULTS_DIR}/sglang.pid"
    bash "${ROOT}/scripts/launch_sglang.sh"
    wait_http_ok "${SGLANG_HEALTH_URL}"
    METRICS_URL="${SGLANG_METRICS_URL}"
    ;;
  *)
    echo "ONE_CLICK_BACKEND must be vllm or sglang" >&2
    exit 1
    ;;
esac

export START_EXPORTER="${START_EXPORTER:-true}"
if [[ "${START_EXPORTER}" == "true" ]]; then
  start_exporter_with_pid "${BACKEND}" "${METRICS_URL}"
  wait_http_ok "${EXPORTER_HEALTH_URL}"
fi

echo "[one_click] up backend=${BACKEND} exporter=${START_EXPORTER}"
