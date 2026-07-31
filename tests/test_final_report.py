from scripts.generate_final_report import _build_report


def test_build_report_includes_all_sections_and_images() -> None:
    reuse_doc = {
        "meta": {"base_url": "http://localhost:8000", "model": "demo", "repeats": 5},
        "aggregates": {
            "low_reuse": {"req_s_mean": 10.0, "e2e_p95_ms_mean": 100.0, "hit_ratio_mean": 0.0, "quality_summary": {"metric_quality": "ok", "hit_ratio_source": "window", "num_runs_ok_metrics": 5, "total_runs": 5, "num_runs_prefix_metric_strict": 5, "num_runs_prefix_metric_token_fallback": 0, "prefix_metric_check": "strict"}},
            "high_reuse": {"req_s_mean": 12.0, "e2e_p95_ms_mean": 90.0, "hit_ratio_mean": 0.2, "quality_summary": {"metric_quality": "ok", "hit_ratio_source": "window", "num_runs_ok_metrics": 5, "total_runs": 5, "num_runs_prefix_metric_strict": 0, "num_runs_prefix_metric_token_fallback": 5, "prefix_metric_check": "token_fallback"}},
        },
    }
    baseline_doc = {
        "scenarios": {
            "low_reuse": {"baseline_hard_only_recommended_rps": 9.0, "dual_boundary_recommended_rps": 0.0, "delta_dual_minus_baseline": -9.0, "metric_quality": "ok", "hit_ratio_source": "window", "num_runs": 5, "num_runs_ok_metrics": 5, "num_runs_prefix_metric_strict": 5, "num_runs_prefix_metric_token_fallback": 0, "prefix_metric_check": "strict"},
            "high_reuse": {"baseline_hard_only_recommended_rps": 10.8, "dual_boundary_recommended_rps": 10.8, "delta_dual_minus_baseline": 0.0, "metric_quality": "ok", "hit_ratio_source": "window", "num_runs": 5, "num_runs_ok_metrics": 5, "num_runs_prefix_metric_strict": 0, "num_runs_prefix_metric_token_fallback": 5, "prefix_metric_check": "token_fallback"},
        }
    }
    threshold_doc = {
        "rows": [
            {"scenario": "low_reuse", "min_hit_ratio": 0.7, "baseline_rps": 9.0, "dual_rps": 0.0, "metric_quality": "ok", "hit_ratio_source": "window", "num_runs": 5, "num_runs_ok_metrics": 5, "num_runs_prefix_metric_strict": 5, "num_runs_prefix_metric_token_fallback": 0, "prefix_metric_check": "strict"},
            {"scenario": "high_reuse", "min_hit_ratio": 0.7, "baseline_rps": 10.8, "dual_rps": 10.8, "metric_quality": "ok", "hit_ratio_source": "window", "num_runs": 5, "num_runs_ok_metrics": 5, "num_runs_prefix_metric_strict": 0, "num_runs_prefix_metric_token_fallback": 5, "prefix_metric_check": "token_fallback"},
            {"scenario": "high_reuse", "min_hit_ratio": 0.75, "baseline_rps": 10.8, "dual_rps": 10.8, "metric_quality": "ok", "hit_ratio_source": "window", "num_runs": 5, "num_runs_ok_metrics": 5, "num_runs_prefix_metric_strict": 0, "num_runs_prefix_metric_token_fallback": 5, "prefix_metric_check": "token_fallback"},
        ]
    }
    capacity_doc = {
        "ranking": {
            "by_scenario": {
                "low_reuse": {"highest_feasible_point": {"concurrency": 4, "req_s_mean": 8.0}, "best_safe_point": None},
                "high_reuse": {"highest_feasible_point": {"concurrency": 8, "req_s_mean": 12.0}, "best_safe_point": {"concurrency": 4, "dual_boundary_recommended_rps": 10.8}},
            }
        }
    }

    md = _build_report(
        reuse_doc=reuse_doc,
        baseline_doc=baseline_doc,
        threshold_doc=threshold_doc,
        threshold_pngs=["threshold_curve.png", "threshold_curve_split.png"],
        capacity_doc=capacity_doc,
        capacity_png="capacity_sweep_ranking.png",
    )

    assert "# Final Experiment Report" in md
    assert "## Decision Summary" in md
    assert "## Project Context" in md
    assert "## Experiment Setup" in md
    assert "## Executive Summary" in md
    assert "## Key Findings" in md
    assert "## Reuse Summary" in md
    assert "## Baseline Compare" in md
    assert "## Threshold Scan" in md
    assert "## Capacity Sweep" in md
    assert "Near-term recommendation" in md
    assert "Objective: evaluate whether KV cache reuse signals are strong enough" in md
    assert "Reuse experiment: repeats `5`, concurrency `NA`, num_requests `NA`" in md
    assert "ok runs" in md
    assert "5/5" in md
    assert "prefix_check" in md
    assert "strict runs" in md
    assert "token fallback runs" in md
    assert "token_fallback" in md
    assert "Reuse experiment headline" in md
    assert "Capacity headline" in md
    assert "![threshold_curve](threshold_curve.png)" in md
    assert "![capacity_sweep_ranking](capacity_sweep_ranking.png)" in md


def test_build_report_supports_chinese_presentation_style() -> None:
    reuse_doc = {
        "meta": {"base_url": "http://localhost:8000", "model": "demo", "repeats": 5},
        "aggregates": {
            "low_reuse": {"req_s_mean": 10.0, "e2e_p95_ms_mean": 100.0, "hit_ratio_mean": 0.0, "quality_summary": {"metric_quality": "ok", "hit_ratio_source": "window", "num_runs_ok_metrics": 5, "total_runs": 5, "num_runs_prefix_metric_strict": 5, "num_runs_prefix_metric_token_fallback": 0, "prefix_metric_check": "strict"}},
            "high_reuse": {"req_s_mean": 12.0, "e2e_p95_ms_mean": 90.0, "hit_ratio_mean": 0.2, "quality_summary": {"metric_quality": "ok", "hit_ratio_source": "window", "num_runs_ok_metrics": 5, "total_runs": 5, "num_runs_prefix_metric_strict": 0, "num_runs_prefix_metric_token_fallback": 5, "prefix_metric_check": "token_fallback"}},
        },
    }
    baseline_doc = {
        "scenarios": {
            "low_reuse": {"baseline_hard_only_recommended_rps": 9.0, "dual_boundary_recommended_rps": 0.0, "delta_dual_minus_baseline": -9.0, "metric_quality": "ok", "hit_ratio_source": "window", "num_runs": 5, "num_runs_ok_metrics": 5, "num_runs_prefix_metric_strict": 5, "num_runs_prefix_metric_token_fallback": 0, "prefix_metric_check": "strict"},
            "high_reuse": {"baseline_hard_only_recommended_rps": 10.8, "dual_boundary_recommended_rps": 10.8, "delta_dual_minus_baseline": 0.0, "metric_quality": "ok", "hit_ratio_source": "window", "num_runs": 5, "num_runs_ok_metrics": 5, "num_runs_prefix_metric_strict": 0, "num_runs_prefix_metric_token_fallback": 5, "prefix_metric_check": "token_fallback"},
        }
    }

    md = _build_report(
        reuse_doc=reuse_doc,
        baseline_doc=baseline_doc,
        threshold_doc=None,
        threshold_pngs=[],
        capacity_doc=None,
        capacity_png=None,
        lang="zh",
    )

    assert "# 最终实验汇报" in md
    assert "## 决策摘要" in md
    assert "## 项目背景" in md
    assert "## 实验设置" in md
    assert "## 高层概览" in md
    assert "## 关键发现" in md
    assert "## 复用实验摘要" in md
    assert "近期建议" in md
    assert "目标：评估 KV cache 复用信号是否足够稳定" in md
