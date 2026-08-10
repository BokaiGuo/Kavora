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
BLOCK_SIZE="${BLOCK_SIZE:-16}"
PYTHON_HASH_SEED="${PYTHON_HASH_SEED:-7}"
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
  local label="$1" port="$2" event_port="$3" replay_port="$4" pid_file="$5" log_file="$6"
  local event_config
  event_config="{\"enable_kv_cache_events\":true,\"publisher\":\"zmq\",\"endpoint\":\"tcp://*:${event_port}\",\"replay_endpoint\":\"tcp://*:${replay_port}\",\"buffer_steps\":10000}"
  start_with_pid_file "$pid_file" bash -lc \
    "exec env PYTHONHASHSEED=\"$PYTHON_HASH_SEED\" \"$VLLM_BIN\" serve \"$MODEL\" --host \"$HOST\" --port \"$port\" --served-model-name \"$SERVED_MODEL_NAME\" --max-model-len \"$MAX_MODEL_LEN\" --gpu-memory-utilization \"$GPU_MEM_UTIL_PER_REPLICA\" --enable-prefix-caching --prefix-caching-hash-algo sha256_cbor --block-size \"$BLOCK_SIZE\" --kv-events-config '$event_config' --enforce-eager --disable-log-requests >\"$log_file\" 2>&1"
  echo "started $label at http://$HOST:$port (log: $log_file)"
}

start_replica "gpu-0" "$PORT_A" "${EVENT_PORT_A:-15557}" "${REPLAY_PORT_A:-15558}" "$PID_DIR/vllm-a.pid" "$LOG_DIR/vllm-a.log"
start_replica "gpu-1" "$PORT_B" "${EVENT_PORT_B:-15567}" "${REPLAY_PORT_B:-15568}" "$PID_DIR/vllm-b.pid" "$LOG_DIR/vllm-b.log"

echo "wait for both /health endpoints before starting the strategy gateways"
