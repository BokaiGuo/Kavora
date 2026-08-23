# Stage 8: SLO-Efficient Operating Envelope

Stage 8 changes the optimization target from raw throughput or policy effect to **SLO-qualified goodput**:

```text
goodput = requests satisfying the configured SLO / measured experiment duration
```

A request is qualified only when it is successful and satisfies every configured latency constraint:

```text
success
∧ TTFT <= ttft_slo_ms
∧ TPOT <= tpot_slo_ms          (when configured)
∧ stream_gap_p95 <= stream_gap_slo_ms  (when configured)
```

## Implemented

- Gateway outcome journals now preserve optional `tpot_ms`, `stream_gap_p95_ms`, and `stream_chunk_count` fields.
- The Go gateway derives streaming inter-write gaps from the actual response writes and derives TPOT when output-token evidence is available.
- `planner.policy_evaluation` reports `slo_qualified_requests`, `slo_qualified_rate`, `goodput_req_s`, qualification reasons, TPOT/stream-gap samples, and treatment-vs-control goodput effect.
- The evaluator accepts `--tpot-slo-ms` and `--stream-gap-slo-ms`; omitted constraints preserve the legacy TTFT-only qualification contract.
- Missing configured stream metrics are classified as `tpot_missing` or `stream_gap_missing`, never silently converted to zero.
- `planner.operating_envelope` selects a goodput lower-bound point under SLO and optional GPU-second budget, and emits the feasible set and Pareto points.

## Usage

```bash
make policy-evaluation \
  INPUT=results/state \
  EXPERIMENT_ID=stage7-online-01 \
  CONTROL=static \
  TREATMENT=kv-v2 \
  SLO_MS=500 \
  TPOT_SLO_MS=50 \
  STREAM_GAP_SLO_MS=60 \
  MIN_REQUESTS=500
```

After a capacity sweep has produced goodput and optional resource fields:

```bash
make operating-envelope \
  INPUT=results/capacity_sweeps/local/summary.json \
  SLO_MS=1500 \
  RESOURCE_BUDGET_GPU_SECONDS=3600
```

The report distinguishes:

- **Implemented:** metric schema, gateway derivation, evaluator and report fields.
- **Measured:** only when a real outcome journal contains the corresponding fields.
- **Claim boundary:** goodput results remain specific to the model, hardware, workload, pool, and SLO configuration represented by the artifact.

Stage 8 does not create Kubernetes deployments, add GPU nodes, or autoscale infrastructure. Its boundary is evidence-backed operating-point recommendation inside an existing resource pool.
