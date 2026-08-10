import json
from pathlib import Path

from planner.ttft_validation import validate_predictor


def test_validate_predictor_uses_independent_outcome_window(tmp_path: Path) -> None:
    artifact = {
        "schema_version": "kavora-ttft-predictor/v1",
        "predictor_version": "fitted-test",
        "model": "m",
        "gpu_type": "g",
        "backend_engine": "vllm",
        "backend_version": "v",
        "coefficients": {"intercept_ms": 10, "uncached_token_ms": .1, "cached_token_ms": .02, "queue_penalty_ms": 5, "kv_pressure_penalty_ms": 20, "slo_scale_ms": 25},
        "validation": {"samples": 10, "mae_ms": 1, "p95_absolute_error_ms": 2},
    }
    artifact_path = tmp_path / "predictor.json"
    artifact_path.write_text(json.dumps(artifact))
    decisions = []
    outcomes = []
    for index in range(8):
        request_id = f"req-{index}"
        prompt = 1000
        matched = 400
        queue = index % 2
        pressure = .25
        expected = 10 + 600 * .1 + 400 * .02 + queue * 5 + pressure * 20
        decisions.append({"request_id": request_id, "candidates": [{"backend_id": "gpu", "queue_depth": queue, "kv_pressure": pressure}]})
        outcomes.append({"request_id": request_id, "actual_backend": "gpu", "success": True, "ttft_ms": expected + 1, "prompt_tokens": prompt, "observed_matched_tokens": matched, "model": "m", "gpu_type": "g", "backend_engine": "vllm", "backend_version": "v"})
    (tmp_path / "decisions-2026-08-10.jsonl").write_text("\n".join(json.dumps(row) for row in decisions) + "\n")
    (tmp_path / "outcomes-2026-08-10.jsonl").write_text("\n".join(json.dumps(row) for row in outcomes) + "\n")

    report = validate_predictor(tmp_path, artifact_path, max_mae_ms=5, max_p95_ms=5)

    assert report["samples"] == 8
    assert abs(report["mae_ms"] - 1) < 1e-9
    assert report["status"] == "HELD_OUT_PASS"
    assert "independent journal" in report["claim_boundary"]
