#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pid_utils.sh
source "${ROOT}/scripts/lib/pid_utils.sh"

VLLM_BIN="${VLLM_BIN:-${ROOT}/.venv-vllm/bin/vllm}"
MODEL="${MODEL:-${ROOT}/models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0}"
HOST="${HOST:-localhost}"
PORT="${PORT:-8000}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-kvcache-local-tiny}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-2048}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.8}"
ENFORCE_EAGER="${ENFORCE_EAGER:-true}"
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
PID_FILE="${PID_FILE:-results/vllm.pid}"

mkdir -p "$(dirname "${PID_FILE}")"

VLLM_EXTRA_ARGS=()
if [[ "${ENFORCE_EAGER}" == "true" ]]; then
  VLLM_EXTRA_ARGS+=(--enforce-eager)
fi
if [[ "${ENABLE_PREFIX_CACHING}" == "true" ]]; then
  VLLM_EXTRA_ARGS+=(--enable-prefix-caching)
fi

if [[ "${1:-}" == "stop" ]]; then
  stop_with_pid_file "${PID_FILE}" "vllm"
  exit 0
fi

# Stop only the process we started previously; avoid pkill -f side effects.
stop_with_pid_file "${PID_FILE}" "vllm"

start_with_pid_file "${PID_FILE}" \
  "${VLLM_BIN}" serve "${MODEL}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --served-model-name "${SERVED_MODEL_NAME}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL}" \
  "${VLLM_EXTRA_ARGS[@]}"
