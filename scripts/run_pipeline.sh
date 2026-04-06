#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${1:-}" == "stop" ]]; then
  exec "${ROOT}/scripts/run_pipeline_local_offline_backend.sh" stop "${2:-all}"
fi

# Default single-backend offline path (vLLM). Set PIPELINE_BACKEND=sglang to override.
BACKEND="${PIPELINE_BACKEND:-vllm}"
exec "${ROOT}/scripts/run_pipeline_local_offline_backend.sh" "${BACKEND}" "$@"
