import json
from pathlib import Path

from planner.policy_evaluation import evaluate_experiment, render_markdown


def test_policy_evaluation_estimates_effect_and_promotion_verdict(tmp_path: Path) -> None:
    decisions = []
    outcomes = []
    for index in range(40):
        treatment = (index // 4) % 2 == 1
        request_id = f"req-{index}"
        policy = "kv-v2" if treatment else "static"
        ttft = 80 + index % 3 if treatment else 120 + index % 3
        decisions.append(
            {
                "request_id": request_id,
                "experiment_id": "exp-1",
                "assigned_policy": policy,
                "assignment_probability": 0.5,
                "experiment_window": f"window-{index // 4:02d}",
                "warmup": index in {0, 1},
                "carryover_guard": index in {0, 1},
                "fallback": False,
                "candidates": [{"backend_id": "gpu", "queue_depth": index % 2}],
                "prediction_error": {"ttft_absolute_error_ms": 5},
            }
        )
        outcomes.append(
            {
                "request_id": request_id,
                "actual_backend": "gpu",
                "success": True,
                "status_code": 200,
                "ttft_ms": ttft,
                "e2e_ms": ttft + 20,
                "prompt_tokens": 500 if index < 20 else 2000,
                "observed_cache_hit_ratio": 0.2 if index < 20 else 0.8,
                "completed_at": f"2026-08-10T12:{index:02d}:00Z",
            }
        )
    (tmp_path / "decisions-2026-08-10.jsonl").write_text("\n".join(json.dumps(row) for row in decisions) + "\n")
    (tmp_path / "outcomes-2026-08-10.jsonl").write_text("\n".join(json.dumps(row) for row in outcomes) + "\n")

    report = evaluate_experiment(
        tmp_path,
        experiment_id="exp-1",
        control_policy="static",
        treatment_policy="kv-v2",
        slo_ms=100,
        min_requests=30,
        bootstrap_samples=500,
        seed=7,
        guardrails={"max_error_rate": 0.01, "max_fallback_rate": 0.01, "max_p95_ttft_ms": 150, "max_prediction_mae_ms": 30},
    )

    assert report["effect"]["ttft_mean_difference_ms"] < -35
    assert report["effect"]["ci95_high_ms"] < 0
    assert report["effect"]["ci_method"] == "window_cluster_bootstrap"
    assert report["integrity"]["warmup_excluded"] == 2
    assert report["verdict"] == "PROMOTION_ELIGIBLE"
    assert "# Kavora Policy Evaluation" in render_markdown(report)
    assert {item["stratum"] for item in report["strata"]} >= {"short_prompt", "long_prompt", "low_reuse", "high_reuse"}
