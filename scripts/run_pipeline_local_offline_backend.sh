#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
USER_TOOL_PYTHON="${TOOL_PYTHON:-}"

# shellcheck source=./lib/pipeline_common.sh
source "${ROOT}/scripts/lib/pipeline_common.sh"

if [[ -z "${USER_TOOL_PYTHON}" ]] && [[ -x "${ROOT}/.venv/bin/python" ]]; then
  TOOL_PYTHON="${ROOT}/.venv/bin/python"
fi

ensure_results_tree

if [[ "${1:-}" == "stop" ]]; then
  TARGET="${2:-all}"
  case "${TARGET}" in
    vllm)
      stop_with_pid_file "${RESULTS_DIR}/vllm.pid" "vllm"
      stop_exporter_with_pid
      ;;
    sglang)
      stop_with_pid_file "${RESULTS_DIR}/sglang.pid" "sglang"
      stop_exporter_with_pid
      ;;
    all)
      stop_with_pid_file "${RESULTS_DIR}/vllm.pid" "vllm"
      stop_with_pid_file "${RESULTS_DIR}/sglang.pid" "sglang"
      stop_exporter_with_pid
      ;;
    *)
      echo "usage: $0 stop [vllm|sglang|all]" >&2
      exit 1
      ;;
  esac
  exit 0
fi

BACKEND="${1:?usage: $0 <vllm|sglang>}"
shift || true

case "${BACKEND}" in
  vllm | sglang) ;;
  *)
    echo "usage: $0 <vllm|sglang>" >&2
    exit 1
    ;;
esac

if [[ "${BACKEND}" == "vllm" ]]; then
  KEEP_BACKEND="${KEEP_VLLM:-${KEEP_BACKEND:-false}}"
else
  KEEP_BACKEND="${KEEP_SGLANG:-${KEEP_BACKEND:-false}}"
fi
KEEP_EXPORTER="${KEEP_EXPORTER:-false}"

write_run_metadata_cli "${BACKEND}"

case "${BACKEND}" in
  vllm)
    export PID_FILE="${RESULTS_DIR}/vllm.pid"
    bash "${ROOT}/scripts/launch_vllm.sh"
    wait_http_ok "${VLLM_HEALTH_URL}"
    echo "[pipeline] vLLM ready"
    BACKEND_METRICS_URL="${VLLM_METRICS_URL}"
    BENCH_BASE_URL="${VLLM_BASE_URL}"
    BACKEND_PID_FILE="${RESULTS_DIR}/vllm.pid"
    ;;
  sglang)
    export PID_FILE="${RESULTS_DIR}/sglang.pid"
    export SGLANG_HOST
    export SGLANG_PORT
    export SERVED_MODEL_NAME
    bash "${ROOT}/scripts/launch_sglang.sh"
    wait_http_ok "${SGLANG_HEALTH_URL}"
    echo "[pipeline] SGLang ready"
    BACKEND_METRICS_URL="${SGLANG_METRICS_URL}"
    BENCH_BASE_URL="${SGLANG_BASE_URL}"
    BACKEND_PID_FILE="${RESULTS_DIR}/sglang.pid"
    ;;
esac

if [[ "${START_EXPORTER}" == "true" ]]; then
  start_exporter_with_pid "${BACKEND}" "${BACKEND_METRICS_URL}"
  wait_http_ok "${EXPORTER_READY_URL}"
  echo "[pipeline] exporter ready"
fi

run_benchmark_runner "${BENCH_BASE_URL}" "${BENCH_COMPLETIONS_ENDPOINT}" "${SERVED_MODEL_NAME}"

if [[ "${START_EXPORTER}" == "true" ]]; then
  scrape_exporter_metrics_to_file
fi

if [[ "${KEEP_BACKEND}" != "true" ]]; then
  stop_with_pid_file "${BACKEND_PID_FILE}" "${BACKEND}"
fi

if [[ "${KEEP_EXPORTER}" != "true" ]] && [[ "${START_EXPORTER}" == "true" ]]; then
  stop_exporter_with_pid
fi

echo "[pipeline] done backend=${BACKEND}"
