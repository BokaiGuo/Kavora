#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Local inference endpoints must bypass workstation HTTP proxies.
export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}"
export no_proxy="$NO_PROXY"

# shellcheck source=./lib/pid_utils.sh
source "${ROOT}/scripts/lib/pid_utils.sh"

RUN_DIR="${KAVORA_STAGE2_RUN_DIR:-${ROOT}/results/stage2/local-stack}"
PID_DIR="${RUN_DIR}/pids"
LOG_DIR="${RUN_DIR}/logs"
CONFIG_DIR="${RUN_DIR}/config"
POLICY_SOCKET="${RUN_DIR}/policy.sock"
MODEL_NAME="${SERVED_MODEL_NAME:-qwen-local}"
API_KEY="${KAVORA_STAGE2_API_KEY:-local-stage2-key}"
EXPORTER_A_PORT="${EXPORTER_A_PORT:-19108}"
EXPORTER_B_PORT="${EXPORTER_B_PORT:-19109}"
STATIC_PORT="${STATIC_PORT:-18100}"
LOAD_PORT="${LOAD_PORT:-18101}"
SHADOW_PORT="${SHADOW_PORT:-18102}"
ENFORCED_PORT="${ENFORCED_PORT:-18103}"
HASH_RESOLVER_PORT="${HASH_RESOLVER_PORT:-19120}"
ADMIN_TOKEN="${KAVORA_ADMIN_TOKEN:-stage2-admin-token}"

mkdir -p "$PID_DIR" "$LOG_DIR" "$CONFIG_DIR"

wait_http() {
  local url="$1" timeout_s="${2:-180}" start
  start="$(date +%s)"
  until curl --noproxy '*' -fsS "$url" >/dev/null 2>&1; do
    if (( $(date +%s) - start >= timeout_s )); then
      echo "timed out waiting for $url" >&2
      return 1
    fi
    sleep 2
  done
}

stop_stack() {
  for name in kv-events-b kv-events-a hash-resolver gateway-enforced gateway-shadow gateway-load gateway-static exporter-b exporter-a policy; do
    stop_with_pid_file "$PID_DIR/$name.pid" "$name"
  done
  bash "${ROOT}/scripts/launch_stage2_vllm_pair.sh" stop
  rm -f "$POLICY_SOCKET"
}

write_configs() {
  local resolved_model model_revision backend_version
  resolved_model="$(readlink -f "$MODEL")"
  if [[ -n "${MODEL_REVISION:-}" ]]; then
    model_revision="$MODEL_REVISION"
  else
    model_revision="local-path:$(basename "$resolved_model")"
  fi
  backend_version="${BACKEND_VERSION:-$(${VLLM_BIN:-vllm} --version 2>/dev/null || echo unknown)}"
  cat >"$CONFIG_DIR/gateway.yaml" <<EOF
tenants:
  - id: stage2-local
    api_keys: ["$API_KEY"]
    max_concurrent: 32
    token_budget: 8192
    policy_fail_mode: closed
backends:
  - id: gpu-0
    url: http://127.0.0.1:${PORT_A:-18080}
    enabled: true
    weight: 1
    models: ["$MODEL_NAME"]
    health_path: /health
    attributes:
      gpu_type: "$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
      engine: vllm
      engine_version: "$backend_version"
  - id: gpu-1
    url: http://127.0.0.1:${PORT_B:-18081}
    enabled: true
    weight: 1
    models: ["$MODEL_NAME"]
    health_path: /health
    attributes:
      gpu_type: "$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
      engine: vllm
      engine_version: "$backend_version"
EOF
  cat >"$CONFIG_DIR/evaluation.yaml" <<EOF
model: $MODEL_NAME
model_revision: "$model_revision"
backend_version: "$backend_version"
repetitions: ${KAVORA_STAGE2_REPETITIONS:-10}
requests_per_repetition: ${KAVORA_STAGE2_REQUESTS:-24}
concurrency: ${KAVORA_STAGE2_CONCURRENCY:-2}
timeout_s: ${KAVORA_STAGE2_TIMEOUT_S:-120}
max_tokens: ${KAVORA_STAGE2_MAX_TOKENS:-32}
seed: ${KAVORA_STAGE2_SEED:-7}
workloads: [random, repeated-system, long-shared-prefix, tenant-affinity]
targets:
  - {strategy: direct, url: http://127.0.0.1:${PORT_A:-18080}}
  - {strategy: static, url: http://127.0.0.1:$STATIC_PORT, api_key_env: KAVORA_API_KEY}
  - {strategy: load-aware, url: http://127.0.0.1:$LOAD_PORT, api_key_env: KAVORA_API_KEY}
  - {strategy: kv-aware-shadow, url: http://127.0.0.1:$SHADOW_PORT, api_key_env: KAVORA_API_KEY}
  - {strategy: kv-aware-enforced, url: http://127.0.0.1:$ENFORCED_PORT, api_key_env: KAVORA_API_KEY}
backends:
  - {id: gpu-0, metrics_url: http://127.0.0.1:${PORT_A:-18080}/metrics}
  - {id: gpu-1, metrics_url: http://127.0.0.1:${PORT_B:-18081}/metrics}
EOF
}

start_exporter() {
  local name="$1" backend_port="$2" exporter_port="$3" instance="$4"
  start_with_pid_file "$PID_DIR/$name.pid" bash -lc \
    "cd \"$ROOT\" && exec env KVCACHE_BACKEND_METRICS_URL=http://127.0.0.1:$backend_port/metrics KVCACHE_BACKEND_TYPE=vllm KVCACHE_EXPORTER_HOST=127.0.0.1 KVCACHE_EXPORTER_PORT=$exporter_port KVCACHE_MODEL_NAME=\"$MODEL_NAME\" KVCACHE_INSTANCE_NAME=\"$instance\" KVCACHE_BACKEND_ID=\"$instance\" KVCACHE_STATE_DIR=\"$RUN_DIR/state-$instance\" python3 -m exporter.app >\"$LOG_DIR/$name.log\" 2>&1"
}

start_gateway() {
  local name="$1" mode="$2" port="$3"
  start_with_pid_file "$PID_DIR/$name.pid" bash -lc \
    "cd \"$ROOT\" && exec env KAVORA_TENANT_CONFIG=\"$CONFIG_DIR/gateway.yaml\" KAVORA_POLICY_SOCKET=\"$POLICY_SOCKET\" KAVORA_GATEWAY_LISTEN=127.0.0.1:$port KAVORA_ROUTING_MODE=\"$mode\" KAVORA_BACKEND_STATE_URLS=\"http://127.0.0.1:$EXPORTER_A_PORT/backend-state http://127.0.0.1:$EXPORTER_B_PORT/backend-state\" \"$ROOT/build/kavora-gateway\" >\"$LOG_DIR/$name.log\" 2>&1"
}

start_hash_resolver() {
  start_with_pid_file "$PID_DIR/hash-resolver.pid" bash -lc \
    "cd \"$ROOT\" && exec env PYTHONHASHSEED=${PYTHON_HASH_SEED:-7} python3 -m engine_events.vllm_hash --tokenize-url http://127.0.0.1:${PORT_A:-18080} --block-size ${BLOCK_SIZE:-16} --hash-algo sha256_cbor --python-hash-seed ${PYTHON_HASH_SEED:-7} --port $HASH_RESOLVER_PORT >\"$LOG_DIR/hash-resolver.log\" 2>&1"
}

start_kv_subscriber() {
  local name="$1" backend_id="$2" event_port="$3" replay_port="$4" generation
  generation="$(cat /proc/sys/kernel/random/uuid)"
  start_with_pid_file "$PID_DIR/$name.pid" bash -lc \
    "cd \"$ROOT\" && exec python3 -m engine_events.vllm --backend-id \"$backend_id\" --generation \"$generation\" --endpoint tcp://127.0.0.1:$event_port --replay-endpoint tcp://127.0.0.1:$replay_port --gateway-url http://127.0.0.1:$ENFORCED_PORT --admin-token \"$ADMIN_TOKEN\" --checkpoint \"$RUN_DIR/$name.checkpoint.json\" >\"$LOG_DIR/$name.log\" 2>&1"
}

require_running() {
  local pid_file="$1" label="$2" pid
  pid="$(read_pid_file "$pid_file")"
  if [[ -z "$pid" ]] || ! is_pid_running "$pid"; then
    echo "$label exited during startup" >&2
    return 1
  fi
}

start_stack() {
  if [[ -z "${MODEL:-}" ]]; then
    echo "set MODEL to a local Hugging Face model directory" >&2
    exit 2
  fi
  make build
  stop_stack
  write_configs
  bash "${ROOT}/scripts/launch_stage2_vllm_pair.sh"
  wait_http "http://127.0.0.1:${PORT_A:-18080}/health"
  wait_http "http://127.0.0.1:${PORT_B:-18081}/health"
  start_hash_resolver
  wait_http "http://127.0.0.1:$HASH_RESOLVER_PORT/docs" 60
  start_exporter exporter-a "${PORT_A:-18080}" "$EXPORTER_A_PORT" gpu-0
  start_exporter exporter-b "${PORT_B:-18081}" "$EXPORTER_B_PORT" gpu-1
  wait_http "http://127.0.0.1:$EXPORTER_A_PORT/readyz"
  wait_http "http://127.0.0.1:$EXPORTER_B_PORT/readyz"
  start_with_pid_file "$PID_DIR/policy.pid" bash -lc \
    "exec env KAVORA_POLICY_SOCKET=\"$POLICY_SOCKET\" \"$ROOT/policy-engine/target/debug/kavora-policy\" >\"$LOG_DIR/policy.log\" 2>&1"
  for _ in $(seq 1 100); do [[ -S "$POLICY_SOCKET" ]] && break; sleep 0.1; done
  [[ -S "$POLICY_SOCKET" ]] || { echo "policy socket did not appear" >&2; exit 1; }
  start_gateway gateway-static static "$STATIC_PORT"
  start_gateway gateway-load load-aware "$LOAD_PORT"
  start_gateway gateway-shadow shadow "$SHADOW_PORT"
  start_with_pid_file "$PID_DIR/gateway-enforced.pid" bash -lc \
    "cd \"$ROOT\" && exec env KAVORA_TENANT_CONFIG=\"$CONFIG_DIR/gateway.yaml\" KAVORA_POLICY_SOCKET=\"$POLICY_SOCKET\" KAVORA_GATEWAY_LISTEN=127.0.0.1:$ENFORCED_PORT KAVORA_ROUTING_MODE=enforced KAVORA_CACHE_FIDELITY=exact KAVORA_VLLM_HASH_RESOLVER_URL=http://127.0.0.1:$HASH_RESOLVER_PORT KAVORA_BACKEND_STATE_URLS=\"http://127.0.0.1:$EXPORTER_A_PORT/backend-state http://127.0.0.1:$EXPORTER_B_PORT/backend-state\" KAVORA_ADMIN_TOKEN=\"$ADMIN_TOKEN\" \"$ROOT/build/kavora-gateway\" >\"$LOG_DIR/gateway-enforced.log\" 2>&1"
  wait_http "http://127.0.0.1:$STATIC_PORT/readyz" 60
  wait_http "http://127.0.0.1:$LOAD_PORT/readyz" 60
  wait_http "http://127.0.0.1:$SHADOW_PORT/readyz" 60
  wait_http "http://127.0.0.1:$ENFORCED_PORT/readyz" 60
  start_kv_subscriber kv-events-a gpu-0 "${EVENT_PORT_A:-15557}" "${REPLAY_PORT_A:-15558}"
  start_kv_subscriber kv-events-b gpu-1 "${EVENT_PORT_B:-15567}" "${REPLAY_PORT_B:-15568}"
  sleep 1
  require_running "$PID_DIR/kv-events-a.pid" "gpu-0 KV-event subscriber"
  require_running "$PID_DIR/kv-events-b.pid" "gpu-1 KV-event subscriber"
  echo "Stage 2 local stack ready; config: $CONFIG_DIR/evaluation.yaml"
}

case "${1:-start}" in
  start)
    start_stack
    ;;
  stop)
    stop_stack
    ;;
  run)
    trap stop_stack EXIT INT TERM
    start_stack
    KAVORA_API_KEY="$API_KEY" KAVORA_STAGE2_CONFIG="$CONFIG_DIR/evaluation.yaml" make benchmark-stage2
    ;;
  *)
    echo "usage: $0 [start|stop|run]" >&2
    exit 2
    ;;
esac
