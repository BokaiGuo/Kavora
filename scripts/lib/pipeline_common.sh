#!/usr/bin/env bash
# Shared helpers for offline / MRE pipelines (source from repo root after `cd "$ROOT"`).

_PIPELINE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${_PIPELINE_LIB_DIR}/../.." && pwd)}"

# shellcheck source=./pid_utils.sh
source "${ROOT}/scripts/lib/pid_utils.sh"

TOOL_PYTHON="${TOOL_PYTHON:-python3}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT}/results}"
EXPORTER_PID_FILE="${EXPORTER_PID_FILE:-${RESULTS_DIR}/exporter.pid}"
EXPORTER_HOST="${EXPORTER_HOST:-localhost}"
EXPORTER_PORT="${EXPORTER_PORT:-9108}"
EXPORTER_METRICS_URL="${EXPORTER_METRICS_URL:-http://${EXPORTER_HOST}:${EXPORTER_PORT}/metrics}"
EXPORTER_HEALTH_URL="${EXPORTER_HEALTH_URL:-http://${EXPORTER_HOST}:${EXPORTER_PORT}/healthz}"
START_EXPORTER="${START_EXPORTER:-true}"
KEEP_EXPORTER="${KEEP_EXPORTER:-false}"
WAIT_HTTP_MAX_S="${WAIT_HTTP_MAX_S:-120}"
RUN_METADATA_OUT="${RUN_METADATA_OUT:-${RESULTS_DIR}/run_metadata.json}"
BENCHMARK_SUMMARY_OUT="${BENCHMARK_SUMMARY_OUT:-${RESULTS_DIR}/raw/benchmark_summary.json}"
KVCACHE_RAW_OUT="${KVCACHE_RAW_OUT:-${RESULTS_DIR}/kvcache_raw.txt}"
RUN_ID="${RUN_ID:-}"
VLLM_HEALTH_URL="${VLLM_HEALTH_URL:-http://localhost:8000/health}"
VLLM_BASE_URL="${VLLM_BASE_URL:-http://localhost:8000}"
VLLM_METRICS_URL="${VLLM_METRICS_URL:-http://localhost:8000/metrics}"
SGLANG_HOST="${SGLANG_HOST:-localhost}"
SGLANG_PORT="${SGLANG_PORT:-30000}"
SGLANG_BASE_URL="${SGLANG_BASE_URL:-http://${SGLANG_HOST}:${SGLANG_PORT}}"
SGLANG_HEALTH_URL="${SGLANG_HEALTH_URL:-${SGLANG_BASE_URL}/health}"
SGLANG_METRICS_URL="${SGLANG_METRICS_URL:-${SGLANG_BASE_URL}/metrics}"
BENCH_COMPLETIONS_ENDPOINT="${BENCH_COMPLETIONS_ENDPOINT:-/v1/completions}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-kvcache-local-tiny}"

ensure_results_tree() {
  mkdir -p "${RESULTS_DIR}/raw" "${RESULTS_DIR}/reports" "$(dirname "${EXPORTER_PID_FILE}")"
}

wait_http_ok() {
  local url="$1"
  local max_s="${2:-${WAIT_HTTP_MAX_S}}"
  local start_s now_s
  start_s="$(date +%s)"
  while true; do
    if curl -sf "${url}" >/dev/null 2>&1; then
      return 0
    fi
    now_s="$(date +%s)"
    if [[ $((now_s - start_s)) -ge "${max_s}" ]]; then
      echo "[pipeline] timeout waiting for ${url}" >&2
      return 1
    fi
    sleep 2
  done
}

write_run_metadata_cli() {
  local backend="$1"
  ensure_results_tree
  local rid_args=()
  if [[ -n "${RUN_ID}" ]]; then
    rid_args=(--run-id "${RUN_ID}")
  fi
  "${TOOL_PYTHON}" "${ROOT}/scripts/lib/write_run_metadata.py" \
    --out "${RUN_METADATA_OUT}" \
    --backend "${backend}" \
    "${rid_args[@]}"
}

start_exporter_with_pid() {
  local backend_type="$1"
  local backend_metrics_url="$2"
  if [[ "${START_EXPORTER}" != "true" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "${EXPORTER_PID_FILE}")"
  stop_with_pid_file "${EXPORTER_PID_FILE}" "exporter"
  export KVCACHE_BACKEND_METRICS_URL="${backend_metrics_url}"
  export KVCACHE_BACKEND_TYPE="${backend_type}"
  export KVCACHE_EXPORTER_HOST="${EXPORTER_HOST}"
  export KVCACHE_EXPORTER_PORT="${EXPORTER_PORT}"
  start_with_pid_file "${EXPORTER_PID_FILE}" \
    "${TOOL_PYTHON}" -m uvicorn exporter.app:app \
    --host "${EXPORTER_HOST}" \
    --port "${EXPORTER_PORT}"
}

stop_exporter_with_pid() {
  stop_with_pid_file "${EXPORTER_PID_FILE}" "exporter"
}

scrape_exporter_metrics_to_file() {
  ensure_results_tree
  curl -sf "${EXPORTER_METRICS_URL}" >"${KVCACHE_RAW_OUT}" || {
    echo "[pipeline] failed to scrape ${EXPORTER_METRICS_URL}" >&2
    return 1
  }
  echo "[pipeline] wrote ${KVCACHE_RAW_OUT}"
}

run_benchmark_runner() {
  local base_url="$1"
  local endpoint="$2"
  local model="$3"
  ensure_results_tree
  "${TOOL_PYTHON}" -m benchmark.runner \
    --base-url "${base_url}" \
    --endpoint "${endpoint}" \
    --model "${model}" \
    --num-requests "${BENCH_NUM_REQUESTS:-32}" \
    --concurrency "${BENCH_CONCURRENCY:-4}" \
    --base-seed "${BENCH_BASE_SEED:-42}" \
    --input-len "${BENCH_INPUT_LEN:-128}" \
    --output-len "${BENCH_OUTPUT_LEN:-32}" \
    --timeout-s "${BENCH_TIMEOUT_S:-30}" \
    --output "${BENCHMARK_SUMMARY_OUT}"
}
