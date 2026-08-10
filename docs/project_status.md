# Kavora Project Status

Kavora is a bilingual AI infrastructure project: Go owns gateway control-plane concerns while Rust owns policy, cache-key, streaming, and sandbox execution boundaries.

## Delivered

- Go gateway with unary/SSE proxying, tenant limits, backend health/failover, CLI and embedded GUI.
- Rust policy engine over gRPC/Unix socket with JSON/PII/content/token/cache-key checks and bounded streaming policy.
- Versioned backend-state protobuf plus JSON bridge preserving fresh, stale, missing, and invalid signals.
- Explainable KV-aware shadow scoring, tenant-scoped bounded affinity, cooldown/traffic guardrails, response-level routing evidence, and a real-endpoint Stage 2 evaluation runner.
- Evidence-aware Stage 4 routing with pluggable cache fidelity, constraint-first eligibility, confidence/staleness decay, explainable TTFT/SLO scoring, a bounded decision ledger, admin APIs, GUI inspector, and gated shadow/canary/enforced lifecycle.
- Stage 5 self-tuning workflow with semantic evidence quality, automatic SLO threshold/concurrency calibration, anonymous workload replay, explicit human approval, and approval-resetting rollback.
- Stage 6 outcome-grounded workflow with realized request outcomes, prediction error/calibration, append-only decision journals, explainable fitted TTFT artifacts, native vLLM event sequence recovery, drift-aware lifecycle gates, and multi-policy replay.
- Stage 7 causal-evaluation workflow with vLLM request/block-hash alignment, switchback and isolated-pool assignment, experiment-linked ledgers, cluster-bootstrap policy reports, held-out predictor validation, and experiment-aware promotion gates.
- Versioned tool manifest, digest-verified Rust Wasmtime worker, bounded Go agent loop, and JSONL deterministic replay.
- One-command showcase demo, persistent exporter state/advice history, CLI tuning advice, and reproducibility manifest/report generation.

## Claim boundaries

- Stage 1 promotion is durable and passed with fake-backend regression plus real vLLM unary/SSE smoke; see `results/stage1/promotion_gate.json`.
- Stage 2 has a reproducible two-backend/five-target runner, but the repository does not yet contain a reviewed real measurement artifact. KV metrics remain explicit inputs; missing/stale state falls back to static routing, and shadow mode no longer reorders traffic.
- The Stage 4 fidelity/lag artifact is a deterministic mechanism study. Native vLLM transport and request/block-hash alignment are implemented, while native SGLang transport and real-GPU performance impact remain unvalidated.
- Auto-calibration and workload replay rank supplied measurements or simulate anonymous signatures; neither mutates production configuration. The v0.1.0 release candidate remains gated on a reviewed real-GPU Stage 2 artifact.
- Native vLLM block events now have transport, replay, dedupe, generation, removal, clear, and request-hash alignment semantics. Exact mode requires a compatible vLLM version, fixed hash seed, matching block/hash configuration, and a healthy resolver.
- Held-out predictor validation and causal policy evaluation tooling are implemented, but the repository still lacks the real-GPU held-out and randomized online artifacts required to claim generalization or causal production improvement.
- The Wasmtime worker verifies the artifact digest, denies non-compute capabilities, caps linear memory, limits instances/tables, consumes fuel, and interrupts at the manifest timeout. It exposes only the narrow `run() -> i32` tool ABI and no WASI host capabilities.
