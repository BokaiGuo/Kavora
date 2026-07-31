# Stage 1 Benchmark and Failure Demo

Stage 1 measures four explicitly supplied OpenAI-compatible paths:

- `direct`: direct backend request;
- `go_only`: Go forwarding without a policy hop, when such a deployment is supplied;
- `go_rust_unary`: Go Gateway with Rust unary policy;
- `go_rust_stream`: Go Gateway with Rust incremental stream policy.

Run a measured matrix by passing endpoint names explicitly:

```bash
KAVORA_BENCHMARK_PATHS='direct=http://127.0.0.1:18080 go_only=http://127.0.0.1:18001 go_rust_unary=http://127.0.0.1:18000 go_rust_stream=http://127.0.0.1:18000' \
KAVORA_API_KEY=local-key \
make benchmark-stage1
```

Artifacts are written under `results/stage1/`. The JSON includes a deterministic `config_hash`, environment, p50/p95/p99 latency, TTFT, throughput, error rate, response bytes and the benchmark process RSS high-water mark.

Phase attribution is conservative: when direct, Go-only and Go+Rust unary paths all exist, the report gives end-to-end deltas as **proxy measurements**. It does not claim isolated Go, gRPC or Rust CPU cost.

Run the failure walkthrough with a ready Gateway:

```bash
KAVORA_API_KEY=local-key make demo-stage1
```

The demo sends allow, PII, long-stream budget-boundary and backend-candidate requests. It prints observed results rather than fabricating success. For failover, stop the first healthy backend in a configured fleet and rerun the command; selection changes only before streaming output begins.

## Promotion Gate

Run the gate after building the project:

```bash
make stage1-gate
```

The gate writes `results/stage1/promotion_gate.json` and `.md`. Fake-backend E2E, benchmark artifact generation and documentation checks are deterministic. Real vLLM/SGLang smoke is recorded as `environment_blocked` when the service, key or GPU environment is unavailable; set `KAVORA_STAGE1_GATE_REQUIRED=true` to fail CI on a blocked gate.
