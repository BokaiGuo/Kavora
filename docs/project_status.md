# Kavora Project Status

Kavora is a bilingual AI infrastructure project: Go owns gateway control-plane concerns while Rust owns policy, cache-key, streaming, and sandbox execution boundaries.

## Delivered

- Go gateway with unary/SSE proxying, tenant limits, backend health/failover, CLI and embedded GUI.
- Rust policy engine over gRPC/Unix socket with JSON/PII/content/token/cache-key checks and bounded streaming policy.
- Versioned backend-state protobuf plus JSON bridge preserving fresh, stale, missing, and invalid signals.
- Explainable KV-aware shadow scoring, tenant-scoped bounded affinity, cooldown/traffic guardrails, response-level routing evidence, and a real-endpoint Stage 2 evaluation runner.
- Evidence-aware Stage 4 routing with pluggable cache fidelity, constraint-first eligibility, confidence/staleness decay, explainable TTFT/SLO scoring, a bounded decision ledger, admin APIs, GUI inspector, and gated shadow/canary/enforced lifecycle.
- Versioned tool manifest, digest-verified Rust Wasmtime worker, bounded Go agent loop, and JSONL deterministic replay.
- One-command showcase demo, persistent exporter state/advice history, CLI tuning advice, and reproducibility manifest/report generation.

## Claim boundaries

- Stage 1 promotion is durable and passed with fake-backend regression plus real vLLM unary/SSE smoke; see `results/stage1/promotion_gate.json`.
- Stage 2 has a reproducible two-backend/five-target runner, but the repository does not yet contain a reviewed real measurement artifact. KV metrics remain explicit inputs; missing/stale state falls back to static routing, and shadow mode no longer reorders traffic.
- The Stage 4 fidelity/lag artifact is a deterministic mechanism study. Exact KV event ingestion is implemented as a bounded control-plane contract, but native vLLM/SGLang event transport and real-GPU performance impact remain unvalidated.
- The Wasmtime worker verifies the artifact digest, denies non-compute capabilities, caps linear memory, limits instances/tables, consumes fuel, and interrupts at the manifest timeout. It exposes only the narrow `run() -> i32` tool ABI and no WASI host capabilities.
