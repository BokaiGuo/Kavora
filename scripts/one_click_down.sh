#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# shellcheck source=./lib/pipeline_common.sh
source "${ROOT}/scripts/lib/pipeline_common.sh"

stop_exporter_with_pid
stop_with_pid_file "${RESULTS_DIR}/vllm.pid" "vllm"
stop_with_pid_file "${RESULTS_DIR}/sglang.pid" "sglang"
echo "[one_click] down"
