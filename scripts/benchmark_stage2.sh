#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${KAVORA_STAGE2_OUT_DIR:-${ROOT}/results/stage2}"
mkdir -p "$OUT_DIR"
KAVORA_STAGE2_REAL_PATHS="${KAVORA_STAGE2_REAL_PATHS:-}" python3 "$ROOT/benchmark/kvaware_experiment.py" --seed "${KAVORA_STAGE2_SEED:-7}" --out "$OUT_DIR/kvaware_matrix.json"
python3 "$ROOT/benchmark/kvaware_report.py" --input "$OUT_DIR/kvaware_matrix.json" --out "$OUT_DIR/kvaware_matrix.md"
echo "stage2 report -> $OUT_DIR/kvaware_matrix.md"
