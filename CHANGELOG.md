# Changelog

## Unreleased

### Added

- Added a real-backend Stage 2 evaluation runner with four controlled workloads, at least ten repetitions, confidence intervals, per-request routing evidence, vLLM metric windows, and raw JSON/Markdown artifacts.
- Added a two-replica vLLM launcher and a versioned Stage 2 configuration template.

### Changed

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
