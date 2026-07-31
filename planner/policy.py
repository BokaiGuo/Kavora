from __future__ import annotations

from typing import Any

from benchmark.window_metrics import (
    get_entry_prefix_metric_check,
    get_entry_hit_ratio_quality_and_source,
    summarize_hit_ratio_source_counts,
    summarize_metric_quality_counts,
)


def recommend_runs(
    runs: list[dict[str, Any]],
    *,
    e2e_p95_slo_ms: float,
    min_success_rate: float,
    min_hit_ratio: float | None,
    safety_factor: float,
) -> dict[str, Any]:
    passed: list[float] = []
    missing_count = 0
    stale_count = 0
    ok_count = 0
    window_count = 0
    snapshot_fallback_count = 0
    prefix_metric_strict_count = 0
    prefix_metric_token_fallback_count = 0
    prefix_metric_other_count = 0
    prefix_metric_missing_count = 0

    for entry in runs:
        s = entry.get("summary", {})
        req = s.get("requests", {})
        total = float(req.get("total", 0) or 0)
        ok = float(req.get("ok", 0) or 0)
        success_rate = (ok / total) if total > 0 else 0.0
        e2e = float(s.get("latency", {}).get("e2e_latency_p95_ms", 0.0) or 0.0)
        req_s = float(s.get("throughput", {}).get("req_s", 0.0) or 0.0)
        hit_ratio, metric_quality, hit_ratio_source = get_entry_hit_ratio_quality_and_source(entry)

        if metric_quality == "missing":
            missing_count += 1
        elif metric_quality == "stale":
            stale_count += 1
        else:
            ok_count += 1
        if hit_ratio_source == "window":
            window_count += 1
        elif hit_ratio_source == "snapshot_fallback":
            snapshot_fallback_count += 1
        prefix_metric_check = get_entry_prefix_metric_check(entry)
        if prefix_metric_check == "strict":
            prefix_metric_strict_count += 1
        elif prefix_metric_check == "token_fallback":
            prefix_metric_token_fallback_count += 1
        elif prefix_metric_check == "other":
            prefix_metric_other_count += 1
        else:
            prefix_metric_missing_count += 1

        hard_ok = e2e <= e2e_p95_slo_ms and success_rate >= min_success_rate
        hot_ok = True if min_hit_ratio is None else (metric_quality == "ok" and hit_ratio is not None and hit_ratio >= min_hit_ratio)
        if hard_ok and hot_ok:
            passed.append(req_s)

    recommended_rps = (max(passed) * safety_factor) if passed else 0.0
    dominant_quality = summarize_metric_quality_counts(
        total_runs=len(runs),
        num_runs_ok_metrics=ok_count,
        num_runs_stale_metrics=stale_count,
        num_runs_missing_hit_ratio=missing_count,
    )
    dominant_source = summarize_hit_ratio_source_counts(
        num_runs_window_hit_ratio=window_count,
        num_runs_snapshot_fallback_hit_ratio=snapshot_fallback_count,
        num_runs_missing_hit_ratio_source=max(0, len(runs) - window_count - snapshot_fallback_count),
    )
    return {
        "recommended_rps": recommended_rps,
        "num_runs": len(runs),
        "num_runs_missing_hit_ratio": missing_count,
        "num_runs_stale_metrics": stale_count,
        "num_runs_ok_metrics": ok_count,
        "metric_quality": dominant_quality,
        "num_runs_window_hit_ratio": window_count,
        "num_runs_snapshot_fallback_hit_ratio": snapshot_fallback_count,
        "hit_ratio_source": dominant_source,
        "ok_metric_run_fraction": (float(ok_count) / float(len(runs))) if runs else 0.0,
        "hit_ratio_comparable": bool(dominant_quality == "ok" and dominant_source != "mixed"),
        "num_runs_prefix_metric_strict": prefix_metric_strict_count,
        "num_runs_prefix_metric_token_fallback": prefix_metric_token_fallback_count,
        "num_runs_prefix_metric_other": prefix_metric_other_count,
        "num_runs_prefix_metric_missing": prefix_metric_missing_count,
        "prefix_metric_check": (
            "missing"
            if not runs or prefix_metric_missing_count == len(runs)
            else "strict"
            if prefix_metric_strict_count == len(runs)
            else "token_fallback"
            if prefix_metric_token_fallback_count == len(runs)
            else "other"
            if prefix_metric_other_count == len(runs)
            else "mixed"
        ),
    }
