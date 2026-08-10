from benchmark.window_metrics import make_run_entry
from planner.auto_calibrator import CalibrationConstraints, CalibrationWeights, calibrate


def _run(*, rps: float, hit: float, e2e: float = 200, evidence: str = "strict") -> dict:
    entry = make_run_entry(
        repeat=1,
        summary={
            "requests": {"ok": 100, "total": 100, "failed": 0},
            "latency": {"e2e_latency_p95_ms": e2e},
            "throughput": {"req_s": rps},
        },
    )
    entry["derived_window_metrics"]["cache_hit_ratio_window"] = hit
    entry["exporter_metrics"]["kvcache_exporter_prefix_metric_comparable"] = 1.0 if evidence == "strict" else 0.0
    entry["exporter_metrics"]["kvcache_exporter_prefix_metric_estimated"] = 1.0 if evidence == "estimated" else 0.0
    entry["exporter_metrics"]["kvcache_exporter_prefix_metric_token_fallback"] = 1.0 if evidence == "fallback" else 0.0
    return entry


def test_calibrator_prefers_stable_rps_lower_bound_over_peak_mean() -> None:
    points = [
        {"scenario": "high_reuse", "concurrency": 8, "runs": [_run(rps=9, hit=.45), _run(rps=10, hit=.5), _run(rps=11, hit=.55)]},
        {"scenario": "high_reuse", "concurrency": 16, "runs": [_run(rps=8, hit=.45), _run(rps=20, hit=.5), _run(rps=21, hit=.55)]},
    ]

    result = calibrate(
        points,
        constraints=CalibrationConstraints(e2e_p95_slo_ms=500, min_success_rate=.995, min_feasible_runs=3),
        weights=CalibrationWeights(instability=2.0, evidence_uncertainty=1.0),
        thresholds=[0, .05, .1, .15, .2, .25, .3, .35, .4, .45, .5, .55, .6],
    )

    assert result["recommendation"]["max_concurrency"] == 8
    assert result["recommendation"]["min_hit_ratio"] == .45
    assert result["recommendation"]["expected_rps"] > 0
    assert "lower-bound" in " ".join(result["reason"])


def test_calibrator_penalizes_fallback_evidence_and_explains_rejections() -> None:
    points = [
        {"scenario": "high_reuse", "concurrency": 8, "runs": [_run(rps=10, hit=.5, evidence="strict") for _ in range(3)]},
        {"scenario": "high_reuse", "concurrency": 16, "runs": [_run(rps=13, hit=.5, evidence="fallback") for _ in range(3)]},
        {"scenario": "high_reuse", "concurrency": 32, "runs": [_run(rps=30, hit=.8, e2e=800) for _ in range(3)]},
    ]

    result = calibrate(
        points,
        constraints=CalibrationConstraints(e2e_p95_slo_ms=500, min_success_rate=.995, min_feasible_runs=3),
        weights=CalibrationWeights(instability=.5, evidence_uncertainty=1.0),
        thresholds=[.5],
    )

    assert result["recommendation"]["max_concurrency"] == 8
    assert result["recommendation"]["evidence_quality"] == "strict"
    rejected = {item["concurrency"]: item["rejection_reasons"] for item in result["alternatives"] if not item["selected"]}
    assert "evidence_uncertainty" in rejected[16]
    assert "insufficient_feasible_runs" in rejected[32]
    assert result["deployment"]["status"] == "human_approval_required"
    assert result["deployment"]["canary_steps"] == [0.05, 0.25, 0.5, 1.0]
