"""Compare hard-only baseline vs dual-boundary recommendations from reuse experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from planner.policy import recommend_runs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="results/experiments/.../summary.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--e2e-p95-slo-ms", type=float, default=1500.0)
    ap.add_argument("--min-success-rate", type=float, default=0.99)
    ap.add_argument("--min-hit-ratio", type=float, default=0.05)
    ap.add_argument("--safety-factor", type=float, default=0.9)
    args = ap.parse_args()

    doc = json.loads(Path(args.input).read_text(encoding="utf-8"))
    out: dict[str, Any] = {
        "schema_version": 3,
        "policy": {
            "e2e_p95_slo_ms": args.e2e_p95_slo_ms,
            "min_success_rate": args.min_success_rate,
            "min_hit_ratio": args.min_hit_ratio,
            "safety_factor": args.safety_factor,
        },
        "scenarios": {},
    }

    for scenario in ("low_reuse", "high_reuse"):
        runs = doc.get("runs", {}).get(scenario, [])
        hard_only = recommend_runs(
            runs,
            e2e_p95_slo_ms=args.e2e_p95_slo_ms,
            min_success_rate=args.min_success_rate,
            min_hit_ratio=None,
            safety_factor=args.safety_factor,
        )
        dual = recommend_runs(
            runs,
            e2e_p95_slo_ms=args.e2e_p95_slo_ms,
            min_success_rate=args.min_success_rate,
            min_hit_ratio=args.min_hit_ratio,
            safety_factor=args.safety_factor,
        )
        out["scenarios"][scenario] = {
            "baseline_hard_only_recommended_rps": round(float(hard_only["recommended_rps"]), 6),
            "dual_boundary_recommended_rps": round(float(dual["recommended_rps"]), 6),
            "delta_dual_minus_baseline": round(float(dual["recommended_rps"]) - float(hard_only["recommended_rps"]), 6),
            "num_runs": int(dual["num_runs"]),
            "num_runs_missing_hit_ratio": int(dual["num_runs_missing_hit_ratio"]),
            "num_runs_stale_metrics": int(dual["num_runs_stale_metrics"]),
            "num_runs_ok_metrics": int(dual["num_runs_ok_metrics"]),
            "metric_quality": str(dual["metric_quality"]),
            "num_runs_window_hit_ratio": int(dual["num_runs_window_hit_ratio"]),
            "num_runs_snapshot_fallback_hit_ratio": int(dual["num_runs_snapshot_fallback_hit_ratio"]),
            "hit_ratio_source": str(dual["hit_ratio_source"]),
            "ok_metric_run_fraction": float(dual["ok_metric_run_fraction"]),
            "hit_ratio_comparable": bool(dual["hit_ratio_comparable"]),
            "num_runs_prefix_metric_strict": int(dual["num_runs_prefix_metric_strict"]),
            "num_runs_prefix_metric_token_fallback": int(dual["num_runs_prefix_metric_token_fallback"]),
            "num_runs_prefix_metric_other": int(dual["num_runs_prefix_metric_other"]),
            "num_runs_prefix_metric_missing": int(dual["num_runs_prefix_metric_missing"]),
            "prefix_metric_check": str(dual["prefix_metric_check"]),
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[compare-baseline] wrote {out_path}")


if __name__ == "__main__":
    main()
