from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scripts.generate_report_bundle import _build_one_report


def _reuse_summary() -> dict:
    quality = {
        "metric_quality": "ok",
        "hit_ratio_source": "window",
        "num_runs_ok_metrics": 1,
        "total_runs": 1,
        "num_runs_prefix_metric_strict": 1,
        "num_runs_prefix_metric_token_fallback": 0,
        "prefix_metric_check": "strict",
    }
    return {
        "meta": {"base_url": "http://localhost:8000", "model": "demo", "repeats": 1},
        "aggregates": {
            "low_reuse": {"req_s_mean": 1, "e2e_p95_ms_mean": 10, "hit_ratio_mean": 0, "quality_summary": quality},
            "high_reuse": {"req_s_mean": 2, "e2e_p95_ms_mean": 9, "hit_ratio_mean": 0.2, "quality_summary": quality},
        },
    }


def test_report_bundle_discovers_and_copies_plots(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment"
    capacity = tmp_path / "capacity"
    output = tmp_path / "reports"
    experiment.mkdir()
    capacity.mkdir()
    (experiment / "summary.json").write_text(json.dumps(_reuse_summary()), encoding="utf-8")
    (experiment / "baseline_compare.json").write_text(json.dumps({"scenarios": {}}), encoding="utf-8")
    (experiment / "threshold_recommended_rps_curve.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "scenario": "low_reuse",
                        "min_hit_ratio": 0.7,
                        "baseline_rps": 1.0,
                        "dual_rps": 1.0,
                        "metric_quality": "ok",
                        "hit_ratio_source": "window",
                        "num_runs": 1,
                        "num_runs_ok_metrics": 1,
                        "num_runs_prefix_metric_strict": 1,
                        "num_runs_prefix_metric_token_fallback": 0,
                        "prefix_metric_check": "strict",
                    },
                    {
                        "scenario": "high_reuse",
                        "min_hit_ratio": 0.7,
                        "baseline_rps": 2.0,
                        "dual_rps": 2.0,
                        "metric_quality": "ok",
                        "hit_ratio_source": "window",
                        "num_runs": 1,
                        "num_runs_ok_metrics": 1,
                        "num_runs_prefix_metric_strict": 1,
                        "num_runs_prefix_metric_token_fallback": 0,
                        "prefix_metric_check": "strict",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (capacity / "summary.json").write_text(json.dumps({"ranking": {"by_scenario": {}}}), encoding="utf-8")
    for directory, name in (
        (experiment, "threshold_recommended_rps_curve.png"),
        (experiment, "threshold_recommended_rps_curve_split.png"),
        (capacity, "capacity_sweep_ranking.png"),
    ):
        Image.new("RGB", (2, 2), "white").save(directory / name)

    report = _build_one_report(
        experiment_dir=experiment,
        capacity_dir=capacity,
        output_dir=output,
        language="en",
        output_name="final_report.md",
    )

    text = report.read_text(encoding="utf-8")
    assert report.is_file()
    assert "## Threshold Scan" in text
    assert "## Capacity Sweep" in text
    assert "![threshold_recommended_rps_curve](threshold_recommended_rps_curve.png)" in text
    assert "![capacity_sweep_ranking](capacity_sweep_ranking.png)" in text
    assert (output / "threshold_recommended_rps_curve.png").is_file()
    assert (output / "threshold_recommended_rps_curve_split.png").is_file()
    assert (output / "capacity_sweep_ranking.png").is_file()
