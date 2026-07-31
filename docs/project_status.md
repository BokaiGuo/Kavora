# Kavora Project Status

Kavora is a bilingual AI infrastructure project: Go owns gateway control-plane concerns while Rust owns policy, cache-key, streaming, and sandbox execution boundaries.

## Delivered

- Go gateway with unary/SSE proxying, tenant limits, backend health/failover, CLI and embedded GUI.
- Rust policy engine over gRPC/Unix socket with JSON/PII/content/token/cache-key checks and bounded streaming policy.
- Versioned backend-state protobuf plus JSON bridge preserving fresh, stale, missing, and invalid signals.
- Explainable KV-aware shadow scoring, tenant-scoped bounded affinity, cooldown/traffic guardrails, and deterministic Stage 2 report generation.
- Versioned tool manifest, digest-verified Rust Wasmtime worker, bounded Go agent loop, and JSONL deterministic replay.
- One-command showcase demo, persistent exporter state/advice history, CLI tuning advice, and reproducibility manifest/report generation.

## Claim boundaries

- Stage 1 promotion is durable and passed with fake-backend regression plus real vLLM unary/SSE smoke; see `results/stage1/promotion_gate.json`.
- Stage 2 now has a real vLLM direct-versus-Gateway matrix and an enforced controller smoke using two backend identities. KV metrics remain explicit inputs; missing/stale state falls back to static routing.
- The Wasmtime worker verifies the artifact digest, denies non-compute capabilities, caps linear memory, limits instances/tables, consumes fuel, and interrupts at the manifest timeout. It exposes only the narrow `run() -> i32` tool ABI and no WASI host capabilities.
