#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${KAVORA_TENANT_CONFIG:-${ROOT}/gateway/config.example.yaml}"
GATEWAY_BIN="${KAVORA_GATEWAY_BIN:-${ROOT}/build/kavora-gateway}"

if [[ ! -f "${CONFIG}" ]]; then
  echo "gateway config not found: ${CONFIG}" >&2
  exit 2
fi
if [[ ! -x "${GATEWAY_BIN}" ]]; then
  echo "gateway binary not found; run make build first: ${GATEWAY_BIN}" >&2
  exit 2
fi

export KAVORA_TENANT_CONFIG="${CONFIG}"
exec "${GATEWAY_BIN}"
