#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pid_utils.sh
source "${ROOT}/scripts/lib/pid_utils.sh"

PYTHON_FOR_SGLANG="${PYTHON_FOR_SGLANG:-python3}"
MODEL="${MODEL:-${ROOT}/models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0}"
HOST="${SGLANG_HOST:-localhost}"
PORT="${SGLANG_PORT:-30000}"
PID_FILE="${PID_FILE:-${ROOT}/results/sglang.pid}"

mkdir -p "$(dirname "${PID_FILE}")"

SGLANG_LAUNCH_ARGS=()
if [[ -n "${SGLANG_MEM_FRACTION:-}" ]]; then
  SGLANG_LAUNCH_ARGS+=(--mem-fraction-static "${SGLANG_MEM_FRACTION}")
fi
# Extra CLI tokens, e.g. SGLANG_EXTRA_ARGS="--dtype half --trust-remote-code"
if [[ -n "${SGLANG_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  SGLANG_LAUNCH_ARGS+=(${SGLANG_EXTRA_ARGS})
fi

if [[ "${1:-}" == "stop" ]]; then
  stop_with_pid_file "${PID_FILE}" "sglang"
  exit 0
fi

stop_with_pid_file "${PID_FILE}" "sglang"

start_with_pid_file "${PID_FILE}" \
  "${PYTHON_FOR_SGLANG}" -m sglang.launch_server \
  --model-path "${MODEL}" \
  --host "${HOST}" \
  --port "${PORT}" \
  "${SGLANG_LAUNCH_ARGS[@]}"
