from __future__ import annotations

from planner.operating_envelope import recommend_operating_point, render_markdown


def _point(scenario: str, concurrency: int, goodput: float, gpu_seconds: float | None) -> dict:
    return {
        "scenario": scenario,
        "concurrency": concurrency,
        "aggregates": {
            "goodput_req_s_mean": goodput,
            "goodput_req_s_lcb": goodput - 0.5,
            "e2e_p95_ms_mean": 100,
            "success_rate_mean": 1.0,
            "gpu_seconds_mean": gpu_seconds,
            "quality_summary": {"metric_quality": "ok"},
        },
    }


def test_recommender_selects_best_goodput_under_resource_budget() -> None:
    result = recommend_operating_point(
        {"points": [_point("high_reuse", 2, 10, 20), _point("high_reuse", 4, 15, 40)]},
        e2e_p95_slo_ms=200,
        resource_budget_gpu_seconds=25,
    )

    assert result["status"] == "recommended"
    assert result["recommendation"]["concurrency"] == 2
    assert result["points"][1]["rejection_reasons"] == ["resource_budget"]
    assert "Operating Envelope" in render_markdown(result)


def test_recommender_does_not_impute_missing_resource_evidence() -> None:
    result = recommend_operating_point(
        {"points": [_point("low_reuse", 1, 5, None)]},
        e2e_p95_slo_ms=200,
        resource_budget_gpu_seconds=25,
    )

    assert result["status"] == "blocked"
    assert result["points"][0]["rejection_reasons"] == ["resource_missing"]
