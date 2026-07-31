#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pid_utils.sh
source "${ROOT}/scripts/lib/pid_utils.sh"

if [[ -z "${PYTHON_FOR_SGLANG:-}" ]]; then
  if [[ -x "${ROOT}/.venv-sglang/bin/python" ]]; then
    PYTHON_FOR_SGLANG="${ROOT}/.venv-sglang/bin/python"
  else
    PYTHON_FOR_SGLANG="python3"
  fi
fi
MODEL="${MODEL:-${ROOT}/models/hf/TinyLlama-TinyLlama-1.1B-Chat-v1.0}"
HOST="${SGLANG_HOST:-localhost}"
PORT="${SGLANG_PORT:-30000}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-kvcache-local-tiny}"
PID_FILE="${PID_FILE:-${ROOT}/results/sglang.pid}"
SGLANG_ENABLE_METRICS="${SGLANG_ENABLE_METRICS:-true}"
SGLANG_SKIP_SERVER_WARMUP="${SGLANG_SKIP_SERVER_WARMUP:-true}"
SGLANG_MEM_FRACTION="${SGLANG_MEM_FRACTION-0.55}"
SGLANG_CONTEXT_LENGTH="${SGLANG_CONTEXT_LENGTH-1024}"

mkdir -p "$(dirname "${PID_FILE}")"

is_true() {
  case "${1:-}" in
    1 | true | TRUE | yes | YES | on | ON) return 0 ;;
    *) return 1 ;;
  esac
}

SGLANG_LAUNCH_ARGS=()
if is_true "${SGLANG_ENABLE_METRICS}"; then
  SGLANG_LAUNCH_ARGS+=(--enable-metrics)
fi
if is_true "${SGLANG_SKIP_SERVER_WARMUP}"; then
  # SGLang 0.5.9 can fail its internal warmup with HTTP 502 even though
  # real /v1/completions traffic succeeds. The pipeline performs its own
  # readiness and benchmark requests after startup.
  SGLANG_LAUNCH_ARGS+=(--skip-server-warmup)
fi
if [[ -n "${SGLANG_MEM_FRACTION:-}" ]]; then
  SGLANG_LAUNCH_ARGS+=(--mem-fraction-static "${SGLANG_MEM_FRACTION}")
fi
if [[ -n "${SGLANG_CONTEXT_LENGTH:-}" ]]; then
  SGLANG_LAUNCH_ARGS+=(--context-length "${SGLANG_CONTEXT_LENGTH}")
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
  --served-model-name "${SERVED_MODEL_NAME}" \
  "${SGLANG_LAUNCH_ARGS[@]}"
