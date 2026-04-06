#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pipeline_common.sh
source "${ROOT}/scripts/lib/pipeline_common.sh"

ensure_results_tree

if [[ "${1:-}" == "stop" ]]; then
  echo "[bench] no long-running processes; use scripts/run_pipeline_local_offline_backend.sh stop all" >&2
  exit 0
fi

wait_http_ok "${VLLM_HEALTH_URL}" "${WAIT_HTTP_MAX_S}"
echo "[bench] backend looks up; sweeping input lengths"

IFS=' ' read -r -a _lens <<< "${BENCH_INPUT_LENS:-64 128 256}"
for il in "${_lens[@]}"; do
  [[ -z "${il}" ]] && continue
  export BENCH_INPUT_LEN="${il}"
  export BENCHMARK_SUMMARY_OUT="${RESULTS_DIR}/raw/bench_inputlen_${il}.json"
  echo "[bench] input_len=${il} -> ${BENCHMARK_SUMMARY_OUT}"
  run_benchmark_runner "${VLLM_BASE_URL}" "${BENCH_COMPLETIONS_ENDPOINT}" "${SERVED_MODEL_NAME}"
done

echo "[bench] done"
