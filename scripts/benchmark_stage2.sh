#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${KAVORA_STAGE2_OUT_DIR:-${ROOT}/results/stage2}"
CONFIG="${KAVORA_STAGE2_CONFIG:-${ROOT}/benchmark/config.stage2.yaml}"
mkdir -p "$OUT_DIR"
if [[ ! -f "$CONFIG" ]]; then
  echo "missing Stage 2 config: $CONFIG" >&2
  echo "copy benchmark/config.stage2.template.yaml to benchmark/config.stage2.yaml and edit the real endpoints" >&2
  exit 2
fi
if [[ "${KAVORA_STAGE2_VALIDATE_ONLY:-false}" == "true" ]]; then
  python3 -m benchmark.stage2_evaluation --config "$CONFIG" --out "$OUT_DIR/stage2_evaluation.json" --validate-only
  exit 0
fi
python3 -m benchmark.stage2_evaluation --config "$CONFIG" --out "$OUT_DIR/stage2_evaluation.json"
python3 -m benchmark.stage2_report --input "$OUT_DIR/stage2_evaluation.json" --out "$OUT_DIR/stage2_evaluation.md"
echo "stage2 raw results -> $OUT_DIR/stage2_evaluation.json"
echo "stage2 report -> $OUT_DIR/stage2_evaluation.md"
