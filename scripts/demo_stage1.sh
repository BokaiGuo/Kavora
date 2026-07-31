#!/usr/bin/env bash
set -u

GATEWAY_URL="${KAVORA_GATEWAY_URL:-http://127.0.0.1:18000}"
API_KEY="${KAVORA_API_KEY:-}"
MODEL="${KAVORA_DEMO_MODEL:-demo-model}"
if [[ -z "${API_KEY}" ]]; then echo "SKIP: set KAVORA_API_KEY before running the Stage 1 demo"; exit 0; fi
if ! curl --noproxy '*' --max-time 3 -fsS "${GATEWAY_URL}/readyz" >/dev/null; then echo "SKIP: Gateway is not ready at ${GATEWAY_URL}"; exit 0; fi

request() {
  curl --noproxy '*' --max-time 20 -sS -w '\nHTTP_STATUS:%{http_code}\n' \
    -H "Authorization: Bearer ${API_KEY}" -H 'Content-Type: application/json' "$@"
}

echo '=== allow ==='
request "${GATEWAY_URL}/v1/chat/completions" -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"hello Kavora\"}]}"
echo '=== PII block ==='
request "${GATEWAY_URL}/v1/chat/completions" -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"contact alice@example.com\"}]}"
echo '=== budget / stream boundary ==='
request -N "${GATEWAY_URL}/v1/chat/completions" -d "{\"model\":\"${MODEL}\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"${KAVORA_DEMO_LONG_PROMPT:-repeat this request until the configured token budget is exceeded}\"}]}"
echo '=== backend candidates ==='
curl --noproxy '*' --max-time 5 -fsS "${GATEWAY_URL}/api/backends" || true
echo
echo 'For failover, stop the first healthy backend and repeat this command; the next candidate is selected only before streaming output begins.'
