#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

VENV_DIR="${VENV_DIR:-${ROOT}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install -U pip
"${VENV_DIR}/bin/pip" install -e ".[dev,offline]"

echo "[install] project venv ready at ${VENV_DIR}"
echo "[install] Optional: create ${ROOT}/.venv-vllm and pip install 'vllm' for launch_vllm.sh"
echo "[install] Optional: use PYTHON_FOR_SGLANG in launch_sglang.sh with a venv that has 'sglang'"
