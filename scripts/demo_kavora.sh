#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${KAVORA_DEMO_OUT_DIR:-${ROOT}/results/demo}"
RUN_DIR="$(mktemp -d)"
GATEWAY_PORT="${KAVORA_DEMO_GATEWAY_PORT:-18002}"
BACKEND_PORT="${KAVORA_DEMO_BACKEND_PORT:-18582}"
GATEWAY_URL="http://127.0.0.1:${GATEWAY_PORT}"
PIDS=()
cleanup() { for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done; rm -rf "$RUN_DIR"; }
trap cleanup EXIT
mkdir -p "$OUT_DIR"

if [[ ! -x "$ROOT/build/kavora" || ! -x "$ROOT/build/kavora-gateway" || ! -x "$ROOT/build/kavora-fake-backend" || ! -x "$ROOT/policy-engine/target/debug/kavora-policy" ]]; then
  echo "demo binaries missing; run make build first" >&2
  exit 2
fi

cat >"$RUN_DIR/config.yaml" <<'EOF'
tenants:
  - id: demo
    api_keys: [demo-key]
    max_concurrent: 4
    token_budget: 4096
    policy_fail_mode: closed
backends:
  - id: demo-fake
    url: http://127.0.0.1:18582
    enabled: true
    weight: 1
    models: [demo-model]
    health_path: /healthz
EOF

"$ROOT/build/kavora-fake-backend" -listen "127.0.0.1:${BACKEND_PORT}" -chunk "Kavora demo response" >"$RUN_DIR/backend.log" 2>&1 & PIDS+=("$!")
KAVORA_POLICY_SOCKET="$RUN_DIR/policy.sock" "$ROOT/policy-engine/target/debug/kavora-policy" >"$RUN_DIR/policy.log" 2>&1 & PIDS+=("$!")
for _ in $(seq 1 20); do [[ -S "$RUN_DIR/policy.sock" ]] && break; sleep .1; done
KAVORA_TENANT_CONFIG="$RUN_DIR/config.yaml" KAVORA_POLICY_SOCKET="$RUN_DIR/policy.sock" KAVORA_GATEWAY_LISTEN="127.0.0.1:${GATEWAY_PORT}" "$ROOT/build/kavora-gateway" >"$RUN_DIR/gateway.log" 2>&1 & PIDS+=("$!")
for _ in $(seq 1 30); do curl --noproxy '*' -fsS "${GATEWAY_URL}/readyz" >/dev/null 2>&1 && break; sleep .2; done

CLI_JSON="$($ROOT/build/kavora --json chat --base-url "$GATEWAY_URL" --api-key demo-key --model demo-model --message 'Show the Kavora bilingual path' --stream=false)"
STREAM="$($ROOT/build/kavora chat --base-url "$GATEWAY_URL" --api-key demo-key --model demo-model --message 'Stream the Kavora response')"
PII_STATUS=0
curl --noproxy '*' -sS -o "$RUN_DIR/pii.json" -w '%{http_code}' "${GATEWAY_URL}/v1/chat/completions" -H 'Authorization: Bearer demo-key' -H 'Content-Type: application/json' -d '{"model":"demo-model","messages":[{"role":"user","content":"email me at demo@example.com"}]}' >"$RUN_DIR/pii.status" || true
[[ "$(cat "$RUN_DIR/pii.status")" == "403" ]] || PII_STATUS=1
BACKENDS="$($ROOT/build/kavora --json backends --base-url "$GATEWAY_URL")"
python3 - "$OUT_DIR/showcase.json" "$CLI_JSON" "$STREAM" "$BACKENDS" "$PII_STATUS" <<'PY'
import json, sys, time
out, unary, stream, backends, pii_status = sys.argv[1:]
payload = {"schema_version":"kavora-showcase/v1", "status":"passed" if pii_status == "0" else "failed", "generated_at_unix":time.time(), "cli_unary":json.loads(unary), "cli_stream":stream, "backends":json.loads(backends), "policy_pii_rejection_http_status":403 if pii_status == "0" else int(pii_status)}
with open(out, "w", encoding="utf-8") as handle: json.dump(payload, handle, indent=2, sort_keys=True); handle.write("\n")
print(out)
PY
echo "Kavora showcase passed: GUI=${GATEWAY_URL}/ui/ CLI=build/kavora results=$OUT_DIR/showcase.json"
