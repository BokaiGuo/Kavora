#!/usr/bin/env bash
set -euo pipefail

# Copyable template: low/high reuse + repeats=5 + baseline comparison.
# Supports both BACKEND=vllm and BACKEND=sglang.
# This script assumes local backend + exporter are already up.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

BACKEND="${BACKEND:-vllm}"
MODEL="${MODEL:-kvcache-local-tiny}"
if [[ "${BACKEND}" == "sglang" ]]; then
  BASE_URL="${BASE_URL:-http://localhost:30000}"
  OUT_DIR="${OUT_DIR:-results/experiments/reuse_local_sglang}"
else
  BASE_URL="${BASE_URL:-http://localhost:8000}"
  OUT_DIR="${OUT_DIR:-results/experiments/reuse_local_vllm}"
fi
EXPORTER_METRICS_URL="${EXPORTER_METRICS_URL:-http://localhost:9108/metrics}"

REPEATS="${REPEATS:-5}"
NUM_REQUESTS="${NUM_REQUESTS:-80}"
CONCURRENCY="${CONCURRENCY:-4}"
INPUT_LEN="${INPUT_LEN:-320}"
OUTPUT_LEN="${OUTPUT_LEN:-48}"
TIMEOUT_S="${TIMEOUT_S:-30}"
BASE_SEED="${BASE_SEED:-42}"

.venv/bin/python scripts/run_reuse_experiment.py \
  --base-url "${BASE_URL}" \
  --model "${MODEL}" \
  --exporter-metrics-url "${EXPORTER_METRICS_URL}" \
  --repeats "${REPEATS}" \
  --num-requests "${NUM_REQUESTS}" \
  --concurrency "${CONCURRENCY}" \
  --input-len "${INPUT_LEN}" \
  --output-len "${OUTPUT_LEN}" \
  --timeout-s "${TIMEOUT_S}" \
  --base-seed "${BASE_SEED}" \
  --out-dir "${OUT_DIR}"

# Baseline compare command (hard-only vs dual-boundary)
.venv/bin/python -m planner.compare_baseline \
  --input "${OUT_DIR}/summary.json" \
  --out "${OUT_DIR}/baseline_compare.json" \
  --e2e-p95-slo-ms 1500 \
  --min-success-rate 0.99 \
  --min-hit-ratio 0.05 \
  --safety-factor 0.9

echo "[template] done"
echo " - backend=${BACKEND}"
echo " - base_url=${BASE_URL}"
echo " - ${OUT_DIR}/summary.json"
echo " - ${OUT_DIR}/summary.md"
echo " - ${OUT_DIR}/baseline_compare.json"
