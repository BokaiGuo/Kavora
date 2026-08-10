# Stage 4: Evidence-Aware Routing

Kavora now treats routing as an evidence-aware control-plane decision rather than a single KV heuristic. The router first enforces tenant requirements, then evaluates cache evidence, queue state, KV pressure, predicted TTFT, SLO risk, confidence, and staleness. Every decision is retained in a bounded ledger and can be inspected through the admin API and GUI.

## Cache fidelity ladder

`CacheStateProvider` is the stable routing boundary. Select one provider with `KAVORA_CACHE_FIDELITY`:

| Value | Source | Meaning |
|---|---|---|
| `none` | no cache evidence | static/load-only control |
| `affinity` | tenant-scoped prior placement | predicted reuse, default confidence `0.65` |
| `shadow` | normalized residency signal | coarse predicted cache match |
| `exact` | bounded KV event index | per-prefix/per-backend matched-token evidence |

Each candidate records `matched_tokens`, `match_ratio`, `source`, `observed_at`, `quality`, and `confidence`. Exact events are ingested with `POST /v1/admin/cache-events`. Confidence decays as `exp(-lambda * age_seconds)`; configure lambda with `KAVORA_CACHE_CONFIDENCE_LAMBDA`.

## Decision contract

Tenant YAML can define hard requirements and a TTFT target:

```yaml
ttft_slo_ms: 200
routing_requirements:
  trust_zone: private
  accelerator: cuda
```

Backend YAML exposes matching attributes. A mismatch removes the backend before performance scoring. Eligible candidates use the explainable score inputs:

- confidence-weighted cache match and matched tokens;
- normalized queue depth and KV pressure;
- predicted TTFT from uncached prompt work, recent prefill rate, queue, and pressure;
- logistic probability of violating the tenant TTFT SLO.

The model is intentionally simple and inspectable; Kavora does not use reinforcement learning for this policy.

## Decision ledger and admin API

Set `KAVORA_ADMIN_TOKEN` to require a Bearer token on admin routes.

```text
GET  /v1/admin/decisions?limit=20
GET  /v1/admin/decisions/{request_id}
GET  /v1/admin/lifecycle
POST /v1/admin/lifecycle
POST /v1/admin/cache-events
```

The ledger includes requirements, candidate eligibility, exclusions, cache evidence, queue depth, KV pressure, predicted TTFT, SLO risk, score, recommendation, actual backend, fallback state, and reasons. The embedded GUI renders the same record in the Decision Inspector.

## Policy lifecycle

Set `KAVORA_ROUTING_LIFECYCLE_CONFIG=gateway/routing.lifecycle.example.yaml`. A healthy policy progresses through:

```text
shadow -> canary 5% -> 25% -> 50% -> 100% -> enforced
```

Promotion requires `min_requests` and all configured p95 regression, error delta, fallback, and SLO gates. Missing/unhealthy state, an unhealthy policy, or any failed gate immediately returns the lifecycle to `static`. Canary assignment is deterministic by request ID.

## Fidelity and lag ablation

Run:

```bash
make benchmark-fidelity
```

The deterministic trace compares all four fidelity levels at `0`, `100`, `500`, `1000`, `2000`, `5000`, and `10000` ms lag. It reports routing accuracy, reuse, TTFT and throughput proxies, decision latency, controller CPU/state cost, state bandwidth, wrong affinity, backend imbalance, decision reversal, and fallback.

## Claim boundary

The provider interfaces, routing path, lifecycle, admin API, GUI, and deterministic ablation are implemented and testable. Native vLLM/SGLang KV-event transport and a reviewed real-GPU Stage 2 result are not yet validated. The fidelity artifact is a mechanism/lag study, not evidence of production speedup.
