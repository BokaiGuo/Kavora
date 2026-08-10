import json
from pathlib import Path

from planner.ttft_predictor import fit_from_journal


def test_fit_predictor_from_realized_outcome_journal(tmp_path: Path) -> None:
    decisions = tmp_path / "decisions-2026-08-10.jsonl"
    outcomes = tmp_path / "outcomes-2026-08-10.jsonl"
    decision_rows = []
    outcome_rows = []
    for index in range(20):
        prompt = 800 + index * 25
        matched = (index % 5) * 80
        queue = index % 4
        pressure = (index % 3) / 4
        uncached = prompt - matched
        ttft = 10 + 0.1 * uncached + 0.02 * matched + 5 * queue + 20 * pressure
        request_id = f"req-{index}"
        decision_rows.append(
            {
                "request_id": request_id,
                "candidates": [
                    {
                        "backend_id": "gpu-0",
                        "matched_tokens": matched,
                        "queue_depth": queue,
                        "kv_pressure": pressure,
                    }
                ],
            }
        )
        outcome_rows.append(
            {
                "request_id": request_id,
                "actual_backend": "gpu-0",
                "ttft_ms": ttft,
                "prompt_tokens": prompt,
                "observed_matched_tokens": matched,
                "success": True,
                "model": "qwen3-8b",
                "gpu_type": "RTX-4090",
                "backend_engine": "vllm",
                "backend_version": "0.10",
            }
        )
    decisions.write_text("\n".join(json.dumps(row) for row in decision_rows) + "\n", encoding="utf-8")
    outcomes.write_text("\n".join(json.dumps(row) for row in outcome_rows) + "\n", encoding="utf-8")

    artifact = fit_from_journal(
        tmp_path,
        model="qwen3-8b",
        gpu_type="RTX-4090",
        backend_engine="vllm",
        backend_version="0.10",
    )

    assert artifact["schema_version"] == "kavora-ttft-predictor/v1"
    assert artifact["validation"]["samples"] == 20
    assert artifact["validation"]["mae_ms"] < 0.1
    assert abs(artifact["coefficients"]["intercept_ms"] - 10) < 0.5
    assert abs(artifact["coefficients"]["uncached_token_ms"] - 0.1) < 0.001
    assert artifact["data_quality"]["skipped_missing_observed_cache"] == 0
    assert "realized outcomes" in artifact["claim_boundary"]


def test_fit_predictor_rejects_non_outcome_grounded_samples(tmp_path: Path) -> None:
    (tmp_path / "decisions-2026-08-10.jsonl").write_text(
        json.dumps({"request_id": "req", "candidates": [{"backend_id": "gpu"}]}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "outcomes-2026-08-10.jsonl").write_text(
        json.dumps({"request_id": "req", "actual_backend": "gpu", "ttft_ms": 100, "prompt_tokens": 1000, "success": True}) + "\n",
        encoding="utf-8",
    )
    try:
        fit_from_journal(tmp_path, model="m", gpu_type="g", backend_engine="vllm", backend_version="v")
    except ValueError as error:
        assert "at least 5 outcome-grounded samples" in str(error)
    else:
        raise AssertionError("expected fit to reject missing observed cache evidence")


def test_fit_predictor_rejects_missing_runtime_dimensions(tmp_path: Path) -> None:
    decisions = []
    outcomes = []
    for index in range(5):
        request_id = f"req-{index}"
        decisions.append({"request_id": request_id, "candidates": [{"backend_id": "gpu"}]})
        outcomes.append(
            {
                "request_id": request_id,
                "actual_backend": "gpu",
                "ttft_ms": 100,
                "prompt_tokens": 1000,
                "observed_matched_tokens": 100,
                "success": True,
                "model": "m",
            }
        )
    (tmp_path / "decisions-2026-08-10.jsonl").write_text(
        "\n".join(json.dumps(row) for row in decisions) + "\n", encoding="utf-8"
    )
    (tmp_path / "outcomes-2026-08-10.jsonl").write_text(
        "\n".join(json.dumps(row) for row in outcomes) + "\n", encoding="utf-8"
    )

    try:
        fit_from_journal(tmp_path, model="m", gpu_type="g", backend_engine="vllm", backend_version="v")
    except ValueError as error:
        assert "at least 5 outcome-grounded samples" in str(error)
    else:
        raise AssertionError("expected fit to reject samples without exact runtime dimensions")
