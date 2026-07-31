#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${KAVORA_RESEARCH_OUT_DIR:-${ROOT}/results/research}"
python3 "$ROOT/benchmark/paper_report.py" \
  --stage1 "${KAVORA_STAGE1_REPORT:-${ROOT}/results/stage1/promotion_gate.json}" \
  --stage2 "${KAVORA_STAGE2_REPORT:-${ROOT}/results/stage2/kvaware_matrix.json}" \
  --out-dir "$OUT_DIR"
