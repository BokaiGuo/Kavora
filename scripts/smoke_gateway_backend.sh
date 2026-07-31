#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND="${BACKEND_KIND:-unknown}"
GATEWAY_URL="${KAVORA_GATEWAY_URL:-http://127.0.0.1:18000}"
API_KEY="${KAVORA_API_KEY:-}"
MODEL="${KAVORA_SMOKE_MODEL:-kvcache-local-tiny}"
MESSAGE="${KAVORA_SMOKE_MESSAGE:-Kavora backend smoke test}"
REQUIRED="${KAVORA_SMOKE_REQUIRED:-false}"

skip() {
  echo "SKIP: ${KIND} smoke — $*"
  [[ "${REQUIRED}" != "true" ]]
}

if ! command -v curl >/dev/null 2>&1; then
  skip "curl is unavailable"
  exit $?
fi
if [[ -z "${API_KEY}" ]]; then
  skip "KAVORA_API_KEY is not set"
  exit $?
fi
if ! curl --noproxy '*' --max-time 3 -fsS "${GATEWAY_URL}/readyz" >/dev/null; then
  skip "gateway is not ready at ${GATEWAY_URL}"
  exit $?
fi

BODY=$(printf '{"model":"%s","messages":[{"role":"user","content":"%s"}],"max_tokens":16}' "${MODEL}" "${MESSAGE}")
RESPONSE=$(curl --noproxy '*' --max-time 30 -fsS "${GATEWAY_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" -H 'Content-Type: application/json' -d "${BODY}")
if [[ -z "${RESPONSE}" ]]; then
  echo "FAIL: ${KIND} returned an empty response" >&2
  exit 1
fi

STREAM_BODY=$(printf '{"model":"%s","messages":[{"role":"user","content":"%s"}],"max_tokens":16,"stream":true}' "${MODEL}" "${MESSAGE}")
STREAM_RESPONSE=$(curl --noproxy '*' --max-time 30 -fsS -N "${GATEWAY_URL}/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" -H 'Content-Type: application/json' \
  -d "${STREAM_BODY}")
if ! grep -q 'data: \[DONE\]' <<<"${STREAM_RESPONSE}"; then
  echo "FAIL: ${KIND} stream did not finish with [DONE]" >&2
  exit 1
fi
echo "PASS: ${KIND} unary and SSE gateway smoke (${ROOT})"
