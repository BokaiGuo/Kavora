#!/usr/bin/env bash
set -euo pipefail

# Thin wrapper for parity with vLLM template usage.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

BACKEND="sglang" bash scripts/experiment_template_local.sh "$@"
