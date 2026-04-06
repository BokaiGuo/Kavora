"""Compare hard-only baseline vs dual-boundary recommendations from reuse experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _recommend(
    runs: list[dict[str, Any]],
    *,
    e2e_p95_slo_ms: float,
    min_success_rate: float,
    min_hit_ratio: float | None,
    safety_factor: float,
) -> float:
    passed: list[float] = []
    for entry in runs:
        s = entry.get("summary", {})
        req = s.get("requests", {})
        total = float(req.get("total", 0) or 0)
        ok = float(req.get("ok", 0) or 0)
        success_rate = (ok / total) if total > 0 else 0.0
        e2e = float(s.get("latency", {}).get("e2e_latency_p95_ms", 0.0) or 0.0)
        req_s = float(s.get("throughput", {}).get("req_s", 0.0) or 0.0)
        hit_ratio = float(entry.get("exporter_metrics", {}).get("kvcache_kv_cache_hit_ratio", 0.0) or 0.0)

        hard_ok = e2e <= e2e_p95_slo_ms and success_rate >= min_success_rate
        hot_ok = True if min_hit_ratio is None else (hit_ratio >= min_hit_ratio)
        if hard_ok and hot_ok:
            passed.append(req_s)
    if not passed:
        return 0.0
    return max(passed) * safety_factor


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
        hard_only = _recommend(
            runs,
            e2e_p95_slo_ms=args.e2e_p95_slo_ms,
            min_success_rate=args.min_success_rate,
            min_hit_ratio=None,
            safety_factor=args.safety_factor,
        )
        dual = _recommend(
            runs,
            e2e_p95_slo_ms=args.e2e_p95_slo_ms,
            min_success_rate=args.min_success_rate,
            min_hit_ratio=args.min_hit_ratio,
            safety_factor=args.safety_factor,
        )
        out["scenarios"][scenario] = {
            "baseline_hard_only_recommended_rps": round(hard_only, 6),
            "dual_boundary_recommended_rps": round(dual, 6),
            "delta_dual_minus_baseline": round(dual - hard_only, 6),
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[compare-baseline] wrote {out_path}")


if __name__ == "__main__":
    main()
