#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${KAVORA_BENCHMARK_OUT_DIR:-${ROOT}/results/stage1}"
RAW_OUT="${OUT_DIR}/gateway_benchmark.json"
REPORT_OUT="${OUT_DIR}/gateway_benchmark.md"
PATHS="${KAVORA_BENCHMARK_PATHS:-}"
REQUIRED="${KAVORA_BENCHMARK_REQUIRED:-false}"
mkdir -p "${OUT_DIR}"

if [[ -z "${PATHS}" ]]; then
  if [[ "${REQUIRED}" == "true" ]]; then
    echo "benchmark paths are required: set KAVORA_BENCHMARK_PATHS" >&2
    exit 2
  fi
  printf '%s\n' '{"schema_version":"kavora-stage1-gateway-v1","status":"skipped","reason":"KAVORA_BENCHMARK_PATHS is not configured","config_hash":"not-run"}' >"${RAW_OUT}"
  printf '%s\n' '# Kavora Stage 1 Gateway Benchmark' '' 'Status: `SKIPPED`' '' 'Set `KAVORA_BENCHMARK_PATHS` to run measured paths.' >"${REPORT_OUT}"
  echo "SKIP: benchmark paths are not configured"
  exit 0
fi

ARGS=()
for path in ${PATHS}; do ARGS+=(--path "${path}"); done
python3 "${ROOT}/benchmark/gateway_runner.py" "${ARGS[@]}" \
  --api-key "${KAVORA_API_KEY:-}" --model "${KAVORA_BENCHMARK_MODEL:-demo-model}" \
  --requests "${KAVORA_BENCHMARK_REQUESTS:-20}" --concurrency "${KAVORA_BENCHMARK_CONCURRENCY:-2}" \
  --input-length "${KAVORA_BENCHMARK_INPUT_LENGTH:-128}" --out "${RAW_OUT}"
python3 "${ROOT}/benchmark/gateway_report.py" --input "${RAW_OUT}" --out "${REPORT_OUT}"
echo "benchmark report -> ${REPORT_OUT}"
