#!/usr/bin/env bash
set -euo pipefail

# Copyable template: low/high reuse + baseline comparison + capacity sweep + threshold curve + final report.
# Supports both BACKEND=vllm and BACKEND=sglang.
# By default this script restarts the serving stack before benchmarked stages
# so cache state from earlier traffic does not contaminate later measurements.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

BACKEND="${BACKEND:-vllm}"
MODEL="${MODEL:-kvcache-local-tiny}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${MODEL}}"
STACK_MODEL_PATH="${STACK_MODEL_PATH:-${BACKEND_MODEL_PATH:-}}"
if [[ "${BACKEND}" == "sglang" ]]; then
  BASE_URL="${BASE_URL:-http://localhost:30000}"
  OUT_DIR="${OUT_DIR:-results/experiments/reuse_local_sglang}"
  SWEEP_OUT_DIR="${SWEEP_OUT_DIR:-results/capacity_sweeps/local_sglang}"
  DEFAULT_BACKEND_PORT="30000"
else
  BASE_URL="${BASE_URL:-http://localhost:8000}"
  OUT_DIR="${OUT_DIR:-results/experiments/reuse_local_vllm}"
  SWEEP_OUT_DIR="${SWEEP_OUT_DIR:-results/capacity_sweeps/local_vllm}"
  DEFAULT_BACKEND_PORT="8000"
fi
BACKEND_METRICS_URL="${BACKEND_METRICS_URL:-${BASE_URL%/}/metrics}"
EXPORTER_METRICS_URL="${EXPORTER_METRICS_URL:-http://localhost:9108/metrics}"
STACK_RESULTS_DIR="${STACK_RESULTS_DIR:-${RESULTS_DIR:-${ROOT}/results}}"
ISOLATE_EXPERIMENT_STACK="${ISOLATE_EXPERIMENT_STACK:-1}"
ISOLATE_CAPACITY_SWEEP_POINTS="${ISOLATE_CAPACITY_SWEEP_POINTS:-1}"

REPEATS="${REPEATS:-5}"
NUM_REQUESTS="${NUM_REQUESTS:-80}"
CONCURRENCY="${CONCURRENCY:-4}"
INPUT_LEN="${INPUT_LEN:-320}"
OUTPUT_LEN="${OUTPUT_LEN:-48}"
WARMUP_REQUESTS="${WARMUP_REQUESTS:-0}"
TIMEOUT_S="${TIMEOUT_S:-30}"
BASE_SEED="${BASE_SEED:-42}"
RUN_CAPACITY_SWEEP="${RUN_CAPACITY_SWEEP:-1}"
SWEEP_SCENARIOS="${SWEEP_SCENARIOS:-low_reuse,high_reuse}"
SWEEP_CONCURRENCY_VALUES="${SWEEP_CONCURRENCY_VALUES:-1,2,4,8}"
SWEEP_REPEATS="${SWEEP_REPEATS:-3}"
SWEEP_NUM_REQUESTS="${SWEEP_NUM_REQUESTS:-${NUM_REQUESTS}}"
SWEEP_INPUT_LEN="${SWEEP_INPUT_LEN:-${INPUT_LEN}}"
SWEEP_OUTPUT_LEN="${SWEEP_OUTPUT_LEN:-${OUTPUT_LEN}}"
SWEEP_MIN_HIT_RATIO="${SWEEP_MIN_HIT_RATIO:-0.05}"
SWEEP_SAFETY_FACTOR="${SWEEP_SAFETY_FACTOR:-0.9}"
SWEEP_E2E_P95_SLO_MS="${SWEEP_E2E_P95_SLO_MS:-1500}"
SWEEP_MIN_SUCCESS_RATE="${SWEEP_MIN_SUCCESS_RATE:-0.99}"
RUN_THRESHOLD_CURVE="${RUN_THRESHOLD_CURVE:-1}"
THRESHOLD_START="${THRESHOLD_START:-0.65}"
THRESHOLD_END="${THRESHOLD_END:-0.82}"
THRESHOLD_STEP="${THRESHOLD_STEP:-0.01}"
THRESHOLD_E2E_P95_SLO_MS="${THRESHOLD_E2E_P95_SLO_MS:-1500}"
THRESHOLD_MIN_SUCCESS_RATE="${THRESHOLD_MIN_SUCCESS_RATE:-0.99}"
THRESHOLD_SAFETY_FACTOR="${THRESHOLD_SAFETY_FACTOR:-0.9}"
RUN_FINAL_REPORT="${RUN_FINAL_REPORT:-1}"
FINAL_REPORT_OUT_MD="${FINAL_REPORT_OUT_MD:-${OUT_DIR}/final_report.md}"
RUN_FINAL_REPORT_ZH="${RUN_FINAL_REPORT_ZH:-1}"
FINAL_REPORT_OUT_MD_ZH="${FINAL_REPORT_OUT_MD_ZH:-${OUT_DIR}/final_report_zh.md}"

THRESHOLD_OUT_PNG="${OUT_DIR}/threshold_recommended_rps_curve.png"
THRESHOLD_OUT_SPLIT_PNG="${OUT_DIR}/threshold_recommended_rps_curve_split.png"
THRESHOLD_OUT_ZH_PNG="${OUT_DIR}/threshold_recommended_rps_curve_zh.png"
THRESHOLD_OUT_ZH_SPLIT_PNG="${OUT_DIR}/threshold_recommended_rps_curve_split_zh.png"
THRESHOLD_OUT_CSV="${OUT_DIR}/threshold_recommended_rps_curve.csv"
THRESHOLD_OUT_JSON="${OUT_DIR}/threshold_recommended_rps_curve.json"

parse_host_port() {
  local url="$1"
  local default_port="$2"
  .venv/bin/python - "$url" "$default_port" <<'PY'
from urllib.parse import urlparse
import sys

url = sys.argv[1]
default_port = int(sys.argv[2])
parsed = urlparse(url)
print(parsed.hostname or "localhost")
print(parsed.port or default_port)
PY
}

readarray -t _backend_addr < <(parse_host_port "${BASE_URL}" "${DEFAULT_BACKEND_PORT}")
STACK_BACKEND_HOST="${_backend_addr[0]}"
STACK_BACKEND_PORT="${_backend_addr[1]}"
readarray -t _exporter_addr < <(parse_host_port "${EXPORTER_METRICS_URL}" "9108")
STACK_EXPORTER_HOST="${_exporter_addr[0]}"
STACK_EXPORTER_PORT="${_exporter_addr[1]}"

restart_serving_stack() {
  local label="$1"
  echo "[template] restarting serving stack (${label}) backend=${BACKEND}"
  RESULTS_DIR="${STACK_RESULTS_DIR}" \
    ONE_CLICK_BACKEND="${BACKEND}" \
    START_EXPORTER="true" \
    EXPORTER_HOST="${STACK_EXPORTER_HOST}" \
    EXPORTER_PORT="${STACK_EXPORTER_PORT}" \
    WAIT_HTTP_MAX_S="${WAIT_HTTP_MAX_S:-120}" \
    SERVED_MODEL_NAME="${SERVED_MODEL_NAME}" \
    MODEL="${STACK_MODEL_PATH}" \
    HOST="${STACK_BACKEND_HOST}" \
    PORT="${STACK_BACKEND_PORT}" \
    SGLANG_HOST="${STACK_BACKEND_HOST}" \
    SGLANG_PORT="${STACK_BACKEND_PORT}" \
    bash scripts/one_click_down.sh
  RESULTS_DIR="${STACK_RESULTS_DIR}" \
    ONE_CLICK_BACKEND="${BACKEND}" \
    START_EXPORTER="true" \
    EXPORTER_HOST="${STACK_EXPORTER_HOST}" \
    EXPORTER_PORT="${STACK_EXPORTER_PORT}" \
    WAIT_HTTP_MAX_S="${WAIT_HTTP_MAX_S:-120}" \
    SERVED_MODEL_NAME="${SERVED_MODEL_NAME}" \
    MODEL="${STACK_MODEL_PATH}" \
    HOST="${STACK_BACKEND_HOST}" \
    PORT="${STACK_BACKEND_PORT}" \
    SGLANG_HOST="${STACK_BACKEND_HOST}" \
    SGLANG_PORT="${STACK_BACKEND_PORT}" \
    bash scripts/one_click_up.sh
}

if [[ "${ISOLATE_EXPERIMENT_STACK}" == "1" ]]; then
  restart_serving_stack "before_reuse_experiment"
fi

.venv/bin/python scripts/run_reuse_experiment.py \
  --base-url "${BASE_URL}" \
  --model "${MODEL}" \
  --backend-metrics-url "${BACKEND_METRICS_URL}" \
  --exporter-metrics-url "${EXPORTER_METRICS_URL}" \
  --repeats "${REPEATS}" \
  --num-requests "${NUM_REQUESTS}" \
  --concurrency "${CONCURRENCY}" \
  --input-len "${INPUT_LEN}" \
  --output-len "${OUTPUT_LEN}" \
  --warmup-requests "${WARMUP_REQUESTS}" \
  --timeout-s "${TIMEOUT_S}" \
  --base-seed "${BASE_SEED}" \
  --out-dir "${OUT_DIR}"

.venv/bin/python -m planner.compare_baseline \
  --input "${OUT_DIR}/summary.json" \
  --out "${OUT_DIR}/baseline_compare.json" \
  --e2e-p95-slo-ms 1500 \
  --min-success-rate 0.99 \
  --min-hit-ratio 0.05 \
  --safety-factor 0.9

if [[ "${RUN_CAPACITY_SWEEP}" == "1" ]]; then
  SWEEP_ISOLATION_ARGS=()
  if [[ "${ISOLATE_EXPERIMENT_STACK}" == "1" ]] && [[ "${ISOLATE_CAPACITY_SWEEP_POINTS}" != "1" ]]; then
    restart_serving_stack "before_capacity_sweep"
  fi
  if [[ "${ISOLATE_EXPERIMENT_STACK}" == "1" ]] && [[ "${ISOLATE_CAPACITY_SWEEP_POINTS}" == "1" ]]; then
    SWEEP_ISOLATION_ARGS+=(
      --restart-stack-before-each-point
      --stack-backend "${BACKEND}"
      --stack-results-dir "${STACK_RESULTS_DIR}"
      --stack-served-model-name "${SERVED_MODEL_NAME}"
    )
    if [[ -n "${STACK_MODEL_PATH}" ]]; then
      SWEEP_ISOLATION_ARGS+=(--stack-model-path "${STACK_MODEL_PATH}")
    fi
  fi
  .venv/bin/python scripts/run_capacity_sweep.py \
    --base-url "${BASE_URL}" \
    --model "${MODEL}" \
    --backend-metrics-url "${BACKEND_METRICS_URL}" \
    --exporter-metrics-url "${EXPORTER_METRICS_URL}" \
    --scenarios "${SWEEP_SCENARIOS}" \
    --concurrency-values "${SWEEP_CONCURRENCY_VALUES}" \
    --repeats "${SWEEP_REPEATS}" \
    --num-requests "${SWEEP_NUM_REQUESTS}" \
    --input-len "${SWEEP_INPUT_LEN}" \
    --output-len "${SWEEP_OUTPUT_LEN}" \
    --warmup-requests "${WARMUP_REQUESTS}" \
    --timeout-s "${TIMEOUT_S}" \
    --base-seed "${BASE_SEED}" \
    --e2e-p95-slo-ms "${SWEEP_E2E_P95_SLO_MS}" \
    --min-success-rate "${SWEEP_MIN_SUCCESS_RATE}" \
    --min-hit-ratio "${SWEEP_MIN_HIT_RATIO}" \
    --safety-factor "${SWEEP_SAFETY_FACTOR}" \
    --out-dir "${SWEEP_OUT_DIR}" \
    "${SWEEP_ISOLATION_ARGS[@]}"
fi

if [[ "${RUN_THRESHOLD_CURVE}" == "1" ]]; then
  .venv/bin/python scripts/plot_threshold_curve.py \
    --input "${OUT_DIR}/summary.json" \
    --out-png "${THRESHOLD_OUT_PNG}" \
    --out-split-png "${THRESHOLD_OUT_SPLIT_PNG}" \
    --out-zh-png "${THRESHOLD_OUT_ZH_PNG}" \
    --out-zh-split-png "${THRESHOLD_OUT_ZH_SPLIT_PNG}" \
    --out-csv "${THRESHOLD_OUT_CSV}" \
    --out-json "${THRESHOLD_OUT_JSON}" \
    --start "${THRESHOLD_START}" \
    --end "${THRESHOLD_END}" \
    --step "${THRESHOLD_STEP}" \
    --e2e-p95-slo-ms "${THRESHOLD_E2E_P95_SLO_MS}" \
    --min-success-rate "${THRESHOLD_MIN_SUCCESS_RATE}" \
    --safety-factor "${THRESHOLD_SAFETY_FACTOR}"
fi

if [[ "${RUN_FINAL_REPORT}" == "1" ]]; then
  FINAL_REPORT_ARGS=(
    --reuse-summary "${OUT_DIR}/summary.json"
    --baseline-compare "${OUT_DIR}/baseline_compare.json"
    --out-md "${FINAL_REPORT_OUT_MD}"
  )
  if [[ "${RUN_THRESHOLD_CURVE}" == "1" ]]; then
    FINAL_REPORT_ARGS+=(
      --threshold-json "${THRESHOLD_OUT_JSON}"
      --threshold-png "${THRESHOLD_OUT_PNG}"
      --threshold-png "${THRESHOLD_OUT_SPLIT_PNG}"
    )
  fi
  if [[ "${RUN_CAPACITY_SWEEP}" == "1" ]]; then
    FINAL_REPORT_ARGS+=(
      --capacity-summary "${SWEEP_OUT_DIR}/summary.json"
      --capacity-png "${SWEEP_OUT_DIR}/capacity_sweep_ranking.png"
    )
  fi
  .venv/bin/python scripts/generate_final_report.py "${FINAL_REPORT_ARGS[@]}"
  if [[ "${RUN_FINAL_REPORT_ZH}" == "1" ]]; then
    .venv/bin/python scripts/generate_final_report.py "${FINAL_REPORT_ARGS[@]}" --out-md "${FINAL_REPORT_OUT_MD_ZH}" --lang zh
  fi
fi

echo "[template] done"
echo " - backend=${BACKEND}"
echo " - base_url=${BASE_URL}"
echo " - ${OUT_DIR}/summary.json"
echo " - ${OUT_DIR}/summary.md"
echo " - ${OUT_DIR}/baseline_compare.json"
if [[ "${RUN_THRESHOLD_CURVE}" == "1" ]]; then
  echo " - ${THRESHOLD_OUT_JSON}"
  echo " - ${THRESHOLD_OUT_PNG}"
  echo " - ${THRESHOLD_OUT_SPLIT_PNG}"
fi
if [[ "${RUN_CAPACITY_SWEEP}" == "1" ]]; then
  echo " - ${SWEEP_OUT_DIR}/summary.json"
  echo " - ${SWEEP_OUT_DIR}/summary.md"
fi
if [[ "${RUN_FINAL_REPORT}" == "1" ]]; then
  echo " - ${FINAL_REPORT_OUT_MD}"
  if [[ "${RUN_FINAL_REPORT_ZH}" == "1" ]]; then
    echo " - ${FINAL_REPORT_OUT_MD_ZH}"
  fi
fi
