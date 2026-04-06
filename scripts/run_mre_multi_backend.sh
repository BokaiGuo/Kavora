#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pipeline_common.sh
source "${ROOT}/scripts/lib/pipeline_common.sh"

if [[ "${1:-}" == "stop" ]]; then
  exec "${ROOT}/scripts/run_pipeline_local_offline_backend.sh" stop all
fi

GROUP="${RUN_GROUP_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export RUN_GROUP_ID="${GROUP}"
ensure_results_tree

echo "[mre] group_id=${GROUP}"

export START_EXPORTER="${START_EXPORTER:-true}"
export KEEP_EXPORTER="${KEEP_EXPORTER:-false}"

export RUN_ID="${GROUP}_vllm"
export RUN_METADATA_OUT="${RESULTS_DIR}/run_metadata_${RUN_ID}.json"
export BENCHMARK_SUMMARY_OUT="${RESULTS_DIR}/raw/benchmark_summary_${RUN_ID}.json"
export KVCACHE_RAW_OUT="${RESULTS_DIR}/kvcache_raw_${RUN_ID}.txt"
export KEEP_BACKEND="${KEEP_VLLM:-false}"
bash "${ROOT}/scripts/run_pipeline_local_offline_backend.sh" vllm

export RUN_ID="${GROUP}_sglang"
export RUN_METADATA_OUT="${RESULTS_DIR}/run_metadata_${RUN_ID}.json"
export BENCHMARK_SUMMARY_OUT="${RESULTS_DIR}/raw/benchmark_summary_${RUN_ID}.json"
export KVCACHE_RAW_OUT="${RESULTS_DIR}/kvcache_raw_${RUN_ID}.txt"
export KEEP_BACKEND="${KEEP_SGLANG:-false}"
bash "${ROOT}/scripts/run_pipeline_local_offline_backend.sh" sglang

VLLM_SUMMARY="${RESULTS_DIR}/raw/benchmark_summary_${GROUP}_vllm.json"
SGLANG_SUMMARY="${RESULTS_DIR}/raw/benchmark_summary_${GROUP}_sglang.json"
FRONTIER_OUT="${RESULTS_DIR}/reports/frontier_${GROUP}.json"

"${TOOL_PYTHON}" -m planner.build_frontier \
  --inputs "${VLLM_SUMMARY}" "${SGLANG_SUMMARY}" \
  --labels vllm sglang \
  --out "${FRONTIER_OUT}"

if [[ -n "${MRE_AGGREGATE_HARD:-}" ]] && [[ -n "${MRE_AGGREGATE_HOT:-}" ]]; then
  "${TOOL_PYTHON}" -m planner.aggregate_hard_vs_hot \
    --hard "${MRE_AGGREGATE_HARD}" \
    --hot "${MRE_AGGREGATE_HOT}" \
    --out "${RESULTS_DIR}/reports/hard_vs_hot_${GROUP}.json"
fi

echo "[mre] frontier -> ${FRONTIER_OUT}"
