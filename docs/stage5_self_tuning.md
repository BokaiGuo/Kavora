# Stage 5: Evidence-Driven Self-Tuning

Stage 5 connects telemetry, experiments, calibration, anonymous replay, human approval, canary rollout, and rollback. It deliberately stops short of automatically modifying production configuration.

## Semantic alignment

Kavora separates operational freshness from semantic fidelity:

| Field | Values | Question answered |
|---|---|---|
| `quality` | `fresh`, `stale`, `missing`, `invalid` | Is this observation operationally usable now? |
| `evidence_quality` | `strict`, `estimated`, `fallback`, `missing` | How closely does the metric represent the intended cache semantic? |

Examples:

- vLLM/SGLang prefix query counters: `strict`;
- cache occupancy or token-to-block conversion: `estimated`;
- SGLang cached-token/prompt-token counters: `fallback`;
- absent or partial counters: `missing`.

The exporter writes this distinction into backend-state, Prometheus self-check metrics, advisor recommendations, planner summaries, and routing decision candidates. Stale and semantically weak evidence are therefore different failure modes.

## Automatic calibration

Run a capacity sweep normally. New sweep artifacts contain a `calibration` section, or calibrate an existing artifact directly:

```bash
make auto-calibrate INPUT=results/capacity_sweeps/local/summary.json
```

The calibrator enumerates `min_hit_ratio` from `0.00` through `1.00` in `0.05` increments across every measured concurrency point. A run is feasible only when:

```text
e2e p95 <= configured SLO
success rate >= configured target
metric quality == ok
hit ratio >= candidate threshold
evidence quality != missing
```

It ranks candidates using a conservative RPS lower bound minus instability and evidence-uncertainty penalties. The artifact includes the recommended threshold, maximum concurrency, expected safe RPS, confidence, reasons, every alternative, and why each alternative was not selected.

The deployment status is always `human_approval_required`; calibration never edits the Gateway configuration.

## Anonymous workload replay

The replay schema contains only workload signatures:

```text
prompt_tokens, output_tokens, shared_prefix_hash, shared_prefix_tokens,
tenant_class, arrival_delta_ms, streaming, model
```

Unknown fields are rejected, so raw `prompt`, `messages`, or customer content cannot silently enter a replay artifact. Validate the example path with:

```bash
build/kavora replay benchmark/workload_trace.example.jsonl --policy baseline
build/kavora replay benchmark/workload_trace.example.jsonl \
  --policy candidate \
  --backends 2 \
  --min-hit-ratio 0.40 \
  --max-concurrency 16 \
  --evidence-quality strict \
  --out results/replay/candidate.json
```

Candidate output compares p95 TTFT, throughput, SLO violations, cache reuse, and backend imbalance, then returns `SAFE_FOR_CANARY`, `NEEDS_MORE_EVIDENCE`, or `NOT_SAFE_FOR_CANARY`.

## Human approval and rollout

When `require_human_approval: true`, a lifecycle cannot leave shadow mode until an operator approves it:

```http
POST /v1/admin/lifecycle/approve
Authorization: Bearer $KAVORA_ADMIN_TOKEN
Content-Type: application/json

{"approved_by":"operator-name"}
```

The GUI exposes the same approval action. Any unhealthy state, policy failure, regression, error, fallback, or SLO gate resets approval and rolls back to static. A new promotion attempt therefore requires fresh evidence and a new approval.

## Claim boundary

The semantic contract, calibrator, deterministic anonymous replay, approval gate, and rollback path are implemented and tested. Replay uses an explicit local simulation model; it does not reproduce engine scheduling exactly. A reviewed real-GPU Stage 2 artifact and native engine KV-event transport remain required before claiming production performance gains.
