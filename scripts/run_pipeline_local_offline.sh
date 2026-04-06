#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${1:-}" == "stop" ]]; then
  exec "${ROOT}/scripts/run_pipeline_local_offline_backend.sh" stop vllm
fi

export KEEP_BACKEND="${KEEP_VLLM:-false}"
export KEEP_EXPORTER="${KEEP_EXPORTER:-false}"
exec "${ROOT}/scripts/run_pipeline_local_offline_backend.sh" vllm "$@"
