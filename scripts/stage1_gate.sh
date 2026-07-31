#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${KAVORA_STAGE1_GATE_OUT_DIR:-${ROOT}/results/stage1}"
OUT_JSON="${OUT_DIR}/promotion_gate.json"
OUT_MD="${OUT_DIR}/promotion_gate.md"
LOG_DIR="$(mktemp -d)"
FAKE_PID=""
trap 'if [[ -n "${FAKE_PID}" ]]; then kill "${FAKE_PID}" 2>/dev/null || true; fi; rm -rf "${LOG_DIR}"' EXIT
mkdir -p "${OUT_DIR}"

run_check() {
  local name="$1"
  shift
  "$@" >"${LOG_DIR}/${name}.log" 2>&1
  local status=$?
  printf '%s' "${status}"
}

E2E_STATUS=$(run_check e2e make test-e2e-stream)
make build-fake-backend >"${LOG_DIR}/build-fake.log" 2>&1
./build/kavora-fake-backend -listen 127.0.0.1:18580 -chunk gate >"${LOG_DIR}/fake.log" 2>&1 &
FAKE_PID=$!
sleep .2
BENCH_STATUS=0
KAVORA_BENCHMARK_PATHS='direct=http://127.0.0.1:18580' \
KAVORA_BENCHMARK_REQUESTS=3 KAVORA_BENCHMARK_CONCURRENCY=1 \
KAVORA_BENCHMARK_OUT_DIR="${OUT_DIR}" bash scripts/benchmark_stage1.sh >"${LOG_DIR}/benchmark.log" 2>&1 || BENCH_STATUS=$?

if [[ "${KAVORA_STAGE1_REAL_BACKEND:-both}" == "sglang" ]]; then
  VLLM_LOG="SKIP: vLLM not selected"
  VLLM_STATUS=0
else
  VLLM_LOG=$(make smoke-vllm 2>&1)
  VLLM_STATUS=$?
fi
if [[ "${KAVORA_STAGE1_REAL_BACKEND:-both}" == "vllm" ]]; then
  SGLANG_LOG="SKIP: SGLang not selected"
  SGLANG_STATUS=0
else
  SGLANG_LOG=$(make smoke-sglang 2>&1)
  SGLANG_STATUS=$?
fi

REAL_BACKEND="none"
if grep -q 'PASS: vLLM' <<<"${VLLM_LOG}"; then REAL_BACKEND="vllm"; fi
if grep -q 'PASS: SGLang' <<<"${SGLANG_LOG}"; then REAL_BACKEND="sglang"; fi
if [[ "${REAL_BACKEND}" == "none" && "${KAVORA_STAGE1_REQUIRE_REAL:-false}" == "true" ]]; then
  REAL_GATE_STATUS="failed"
elif [[ "${REAL_BACKEND}" == "none" ]]; then
  REAL_GATE_STATUS="environment_blocked"
else
  REAL_GATE_STATUS="passed"
fi
if [[ "${REAL_GATE_STATUS}" == "passed" ]]; then
  CLAIM_BOUNDARY="real backend smoke passed for ${REAL_BACKEND}; benchmark phase attribution remains end-to-end"
else
  CLAIM_BOUNDARY="real backend evidence is environment_blocked until vLLM or SGLang smoke prints PASS"
fi

DOC_STATUS=0
rg -q '\[DONE\]|PII|failover|首字节' docs/development.md docs/stage1_benchmark.md || DOC_STATUS=$?
PROMOTION_STATUS="passed"
if [[ "${E2E_STATUS}" != "0" || "${BENCH_STATUS}" != "0" || "${DOC_STATUS}" != "0" || "${REAL_GATE_STATUS}" != "passed" ]]; then
  PROMOTION_STATUS="blocked"
fi

export OUT_JSON OUT_MD E2E_STATUS BENCH_STATUS DOC_STATUS VLLM_STATUS SGLANG_STATUS REAL_BACKEND REAL_GATE_STATUS PROMOTION_STATUS VLLM_LOG SGLANG_LOG CLAIM_BOUNDARY
python3 - <<'PY'
import json
import os
from pathlib import Path

def integer(name):
    return int(os.environ[name])

evidence = {
    "fake_backend_e2e": {"status": "passed" if integer("E2E_STATUS") == 0 else "failed", "exit_code": integer("E2E_STATUS")},
    "benchmark_artifact": {"status": "passed" if integer("BENCH_STATUS") == 0 else "failed", "exit_code": integer("BENCH_STATUS")},
    "policy_stream_docs": {"status": "passed" if integer("DOC_STATUS") == 0 else "failed", "exit_code": integer("DOC_STATUS")},
    "real_backend": {"status": os.environ["REAL_GATE_STATUS"], "backend": os.environ["REAL_BACKEND"], "vllm_exit_code": integer("VLLM_STATUS"), "sglang_exit_code": integer("SGLANG_STATUS")},
}
report = {
    "schema_version": "kavora-stage1-promotion-v1",
    "promotion_status": os.environ["PROMOTION_STATUS"],
    "evidence": evidence,
    "claim_boundary": os.environ["CLAIM_BOUNDARY"],
}
Path(os.environ["OUT_JSON"]).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
markdown = ["# Kavora Stage 1 Promotion Gate", "", f"Status: **{report['promotion_status'].upper()}**", "", "| Evidence | Status |", "|---|---|"]
for name, item in evidence.items():
    markdown.append(f"| `{name}` | `{item['status']}` |")
markdown.extend(["", report["claim_boundary"], ""])
Path(os.environ["OUT_MD"]).write_text("\n".join(markdown), encoding="utf-8")
PY

echo "Stage 1 promotion: ${PROMOTION_STATUS}"
echo "Evidence: fake E2E=${E2E_STATUS}, benchmark=${BENCH_STATUS}, real=${REAL_GATE_STATUS} (${REAL_BACKEND})"
echo "Gate report: ${OUT_MD}"
if [[ "${PROMOTION_STATUS}" == "passed" ]]; then exit 0; fi
if [[ "${KAVORA_STAGE1_GATE_REQUIRED:-false}" == "true" ]]; then exit 1; fi
exit 0
