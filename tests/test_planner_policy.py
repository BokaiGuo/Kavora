from benchmark.window_metrics import make_run_entry
from planner.policy import recommend_runs


def _entry(*, req_s: float, e2e_ms: float, ok: int, total: int, hit_ratio: float | None, missing: bool = False, stale: bool = False):
    entry = make_run_entry(
        repeat=1,
        summary={
            "requests": {"ok": ok, "total": total, "failed": total - ok},
            "latency": {"e2e_latency_p95_ms": e2e_ms},
            "throughput": {"req_s": req_s},
        },
        exporter_metrics={},
    )
    entry["derived_window_metrics"]["cache_hit_ratio_window"] = hit_ratio
    entry["derived_window_metrics"]["metrics_missing"] = missing
    entry["derived_window_metrics"]["metrics_stale"] = stale
    return entry


def test_recommend_runs_uses_window_hit_ratio_and_counts_missing() -> None:
    runs = [
        _entry(req_s=10.0, e2e_ms=100.0, ok=10, total=10, hit_ratio=None, missing=True),
        _entry(req_s=8.0, e2e_ms=100.0, ok=10, total=10, hit_ratio=0.8),
    ]
    runs[0]["exporter_metrics"]["kvcache_exporter_prefix_metric_comparable"] = 0.0
    runs[0]["exporter_metrics"]["kvcache_exporter_prefix_metric_token_fallback"] = 1.0
    runs[1]["exporter_metrics"]["kvcache_exporter_prefix_metric_comparable"] = 1.0
    runs[1]["exporter_metrics"]["kvcache_exporter_prefix_metric_token_fallback"] = 0.0

    baseline = recommend_runs(
        runs,
        e2e_p95_slo_ms=1500.0,
        min_success_rate=0.99,
        min_hit_ratio=None,
        safety_factor=0.9,
    )
    dual = recommend_runs(
        runs,
        e2e_p95_slo_ms=1500.0,
        min_success_rate=0.99,
        min_hit_ratio=0.7,
        safety_factor=0.9,
    )

    assert baseline["recommended_rps"] == 9.0
    assert dual["recommended_rps"] == 7.2
    assert dual["num_runs_missing_hit_ratio"] == 1
    assert dual["num_runs_ok_metrics"] == 1
    assert dual["num_runs_window_hit_ratio"] == 2
    assert dual["num_runs_snapshot_fallback_hit_ratio"] == 0
    assert dual["metric_quality"] == "mixed"
    assert dual["hit_ratio_source"] == "window"
    assert dual["ok_metric_run_fraction"] == 0.5
    assert dual["hit_ratio_comparable"] is False
    assert dual["num_runs_prefix_metric_strict"] == 1
    assert dual["num_runs_prefix_metric_token_fallback"] == 1
    assert dual["prefix_metric_check"] == "mixed"
    assert dual["evidence_quality"] == "mixed"


def test_recommend_runs_rejects_stale_hit_ratio_for_dual_boundary() -> None:
    runs = [_entry(req_s=12.0, e2e_ms=100.0, ok=10, total=10, hit_ratio=0.95, stale=True)]
    runs[0]["exporter_metrics"]["kvcache_exporter_prefix_metric_comparable"] = 1.0
    runs[0]["exporter_metrics"]["kvcache_exporter_prefix_metric_token_fallback"] = 0.0

    dual = recommend_runs(
        runs,
        e2e_p95_slo_ms=1500.0,
        min_success_rate=0.99,
        min_hit_ratio=0.7,
        safety_factor=0.9,
    )

    assert dual["recommended_rps"] == 0.0
    assert dual["num_runs_stale_metrics"] == 1
    assert dual["metric_quality"] == "stale"
    assert dual["num_runs_window_hit_ratio"] == 1
    assert dual["hit_ratio_source"] == "window"


def test_recommend_runs_reports_snapshot_fallback_for_legacy_entries() -> None:
    runs = [
        {
            "repeat": 1,
            "summary": {
                "requests": {"ok": 10, "total": 10, "failed": 0},
                "latency": {"e2e_latency_p95_ms": 100.0},
                "throughput": {"req_s": 10.0},
            },
            "exporter_metrics": {
                "kvcache_kv_cache_hit_ratio": 0.8,
                "kvcache_exporter_prefix_metric_comparable": 0.0,
                "kvcache_exporter_prefix_metric_token_fallback": 1.0,
            },
        }
    ]

    dual = recommend_runs(
        runs,
        e2e_p95_slo_ms=1500.0,
        min_success_rate=0.99,
        min_hit_ratio=0.7,
        safety_factor=0.9,
    )

    assert dual["recommended_rps"] == 9.0
    assert dual["num_runs_snapshot_fallback_hit_ratio"] == 1
    assert dual["num_runs_window_hit_ratio"] == 0
    assert dual["metric_quality"] == "ok"
    assert dual["hit_ratio_source"] == "snapshot_fallback"
    assert dual["hit_ratio_comparable"] is True
    assert dual["num_runs_prefix_metric_token_fallback"] == 1
    assert dual["prefix_metric_check"] == "token_fallback"
    assert dual["evidence_quality"] == "fallback"
