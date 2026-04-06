#!/usr/bin/env bash
# Start/stop the KV cache exporter with results/exporter.pid (same layout as pipeline_common.sh).
# Logs go to results/exporter.log by default.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pid_utils.sh
source "${ROOT}/scripts/lib/pid_utils.sh"

RESULTS_DIR="${RESULTS_DIR:-${ROOT}/results}"
PID_FILE="${EXPORTER_PID_FILE:-${RESULTS_DIR}/exporter.pid}"
LOG_FILE="${EXPORTER_LOG_FILE:-${RESULTS_DIR}/exporter.log}"

if [[ -z "${TOOL_PYTHON:-}" ]]; then
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    TOOL_PYTHON="${ROOT}/.venv/bin/python"
  else
    TOOL_PYTHON="python3"
  fi
fi

KVCACHE_BACKEND_METRICS_URL="${KVCACHE_BACKEND_METRICS_URL:-http://localhost:8000/metrics}"
KVCACHE_BACKEND_TYPE="${KVCACHE_BACKEND_TYPE:-vllm}"
KVCACHE_EXPORTER_HOST="${KVCACHE_EXPORTER_HOST:-localhost}"
KVCACHE_EXPORTER_PORT="${KVCACHE_EXPORTER_PORT:-9108}"

mkdir -p "$(dirname "${PID_FILE}")"
mkdir -p "$(dirname "${LOG_FILE}")"

if [[ "${1:-}" == "stop" ]]; then
  stop_with_pid_file "${PID_FILE}" "exporter"
  exit 0
fi

stop_with_pid_file "${PID_FILE}" "exporter"

export KVCACHE_BACKEND_METRICS_URL
export KVCACHE_BACKEND_TYPE
export KVCACHE_EXPORTER_HOST
export KVCACHE_EXPORTER_PORT

nohup "${TOOL_PYTHON}" -m uvicorn exporter.app:app \
  --host "${KVCACHE_EXPORTER_HOST}" \
  --port "${KVCACHE_EXPORTER_PORT}" \
  >>"${LOG_FILE}" 2>&1 &

echo $! >"${PID_FILE}"
echo "[pid] started exporter pid=$(tr -d '[:space:]' <"${PID_FILE}"), pid_file=${PID_FILE}, log=${LOG_FILE}"
