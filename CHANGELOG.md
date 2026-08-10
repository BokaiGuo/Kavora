# Changelog

## Unreleased

### Added

- Added Stage 6 decision/outcome records with realized TTFT, E2E, status, token/cache observations, prediction errors, JSONL persistence, and restart recovery.
- Added explainable TTFT predictor fitting/loading, prediction-quality API/GUI, calibration drift lifecycle gates, native vLLM KV-event replay recovery, and a multi-policy replay laboratory.
- Added backend semantic alignment with `strict`, `estimated`, `fallback`, and `missing` evidence quality across exporter snapshots, Prometheus self-checks, planner artifacts, advice, and routing decisions.
- Added an SLO-aware automatic calibrator that scans cache thresholds and concurrency points, ranks RPS lower bounds with instability/evidence penalties, and emits human-approval deployment plans.
- Added `kavora replay` for deterministic comparison of baseline and candidate policies over prompt-free workload signatures.
- Added a mandatory human approval gate before shadow policies can enter canary rollout; rollback clears prior approval.
- Added pluggable cache-state providers for no evidence, affinity, shadow residency, and exact KV events with explicit quality, provenance, matched-token, and confidence fields.
- Added constraint-first and SLO-aware routing, a bounded per-request decision ledger, authenticated admin APIs, and a GUI Decision Inspector.
- Added shadow-to-canary-to-enforced lifecycle gates with deterministic sampling and automatic static rollback on unhealthy state, policy failure, regression, error, fallback, or SLO gates.
- Added a deterministic cache-fidelity and state-lag ablation with JSON/Markdown output covering routing accuracy, reuse, TTFT/throughput proxies, controller cost, bandwidth, imbalance, reversals, fallback, and wrong affinity.
- Added a real-backend Stage 2 evaluation runner with four controlled workloads, at least ten repetitions, confidence intervals, per-request routing evidence, vLLM metric windows, and raw JSON/Markdown artifacts.
- Added a two-replica vLLM launcher and a versioned Stage 2 configuration template.

### Changed

- Capacity sweep artifacts now embed an automatic operating-envelope recommendation and rejected-alternative explanations.
- Reframed the primary project narrative around an evidence-aware inference control plane rather than feature breadth or agent expansion.
- Gateway routing now hard-filters backend attributes before confidence-weighted cache, queue, KV-pressure, predicted-TTFT, and SLO scoring.
- `make benchmark-stage2` now requires explicit real endpoints and no longer emits synthetic proxy performance rows.
- Gateway responses now expose the selected backend and routing fallback metadata; shadow mode reports recommendations without reordering traffic.

### Fixed

- Fixed isolated serving-stack restarts so the old backend must really exit before the next backend/exporter startup continues.
  - `scripts/lib/pid_utils.sh` now waits for PID exit and force-kills after timeout.
- Reduced vLLM restart noise during isolated capacity-sweep points.
  - Isolated restart helpers no longer inject `VLLM_HOST` / `VLLM_PORT` into the vLLM process environment.
- Added regression coverage for the restart path.
  - Added `tests/test_pid_utils.py`.
  - Expanded script/capacity-sweep tests around isolated execution.

### Verified

- Follow-up real vLLM isolated validation under `/tmp/vllm_restart_fix_check` completed without the earlier exporter `ConnectError` / `/readyz 503` restart-window issue.
- Follow-up vLLM isolated validation produced a feasible `high_reuse` capacity point:
  - `capacity_high_hit_ratio = 0.6154`
  - `best_safe_dual_rps = 10.566682`
- Full automated test suite after the fix:
  - `57 passed in 15.54s`
