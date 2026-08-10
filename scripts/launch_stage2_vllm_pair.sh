#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck source=./lib/pid_utils.sh
source "${ROOT}/scripts/lib/pid_utils.sh"

VLLM_BIN="${VLLM_BIN:-$(command -v vllm || true)}"
MODEL="${MODEL:-${ROOT}/models/hf/Qwen-Qwen3-0.6B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen-local}"
HOST="${HOST:-127.0.0.1}"
PORT_A="${PORT_A:-18080}"
PORT_B="${PORT_B:-18081}"
GPU_MEM_UTIL_PER_REPLICA="${GPU_MEM_UTIL_PER_REPLICA:-0.42}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
PID_DIR="${PID_DIR:-${ROOT}/results/stage2/pids}"
LOG_DIR="${LOG_DIR:-${ROOT}/results/stage2/logs}"

mkdir -p "$PID_DIR" "$LOG_DIR"

stop_pair() {
  stop_with_pid_file "$PID_DIR/vllm-b.pid" "Stage 2 vLLM gpu-1"
  stop_with_pid_file "$PID_DIR/vllm-a.pid" "Stage 2 vLLM gpu-0"
}

if [[ "${1:-start}" == "stop" ]]; then
  stop_pair
  exit 0
fi

if [[ -z "$VLLM_BIN" || ! -x "$VLLM_BIN" ]]; then
  echo "vllm executable not found; set VLLM_BIN" >&2
  exit 2
fi
if [[ ! -e "$MODEL" ]]; then
  echo "model not found: $MODEL" >&2
  echo "set MODEL to a local Hugging Face model directory" >&2
  exit 2
fi

stop_pair

start_replica() {
  local label="$1" port="$2" pid_file="$3" log_file="$4"
  start_with_pid_file "$pid_file" bash -lc \
    "exec \"$VLLM_BIN\" serve \"$MODEL\" --host \"$HOST\" --port \"$port\" --served-model-name \"$SERVED_MODEL_NAME\" --max-model-len \"$MAX_MODEL_LEN\" --gpu-memory-utilization \"$GPU_MEM_UTIL_PER_REPLICA\" --enable-prefix-caching --enforce-eager --disable-log-requests >\"$log_file\" 2>&1"
  echo "started $label at http://$HOST:$port (log: $log_file)"
}

start_replica "gpu-0" "$PORT_A" "$PID_DIR/vllm-a.pid" "$LOG_DIR/vllm-a.log"
start_replica "gpu-1" "$PORT_B" "$PID_DIR/vllm-b.pid" "$LOG_DIR/vllm-b.log"

echo "wait for both /health endpoints before starting the strategy gateways"
