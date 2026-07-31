from pathlib import Path

from scripts.run_capacity_sweep import (
    _build_plot_series,
    _build_stack_restart_env,
    _build_sweep_ranking,
    _parse_csv_ints,
    _parse_url_host_port,
    _to_markdown,
)


def test_parse_csv_ints_supports_simple_list() -> None:
    assert _parse_csv_ints("1, 2,4") == [1, 2, 4]


def test_parse_url_host_port_uses_explicit_port() -> None:
    assert _parse_url_host_port("http://127.0.0.1:19130/metrics", default_port=9108) == ("127.0.0.1", 19130)


def test_build_stack_restart_env_separates_model_name_from_model_path(monkeypatch) -> None:
    monkeypatch.setenv("MODEL", "kvcache-local-tiny")
    env = _build_stack_restart_env(
        backend="sglang",
        base_url="http://127.0.0.1:18030",
        exporter_metrics_url="http://127.0.0.1:19130/metrics",
        results_dir="/tmp/test-results",
        served_model_name="kvcache-local-tiny",
        stack_model_path="",
    )

    assert env["RESULTS_DIR"] == "/tmp/test-results"
    assert env["ONE_CLICK_BACKEND"] == "sglang"
    assert env["MODEL"] == ""
    assert env["SERVED_MODEL_NAME"] == "kvcache-local-tiny"
    assert env["SGLANG_HOST"] == "127.0.0.1"
    assert env["SGLANG_PORT"] == "18030"
    assert env["EXPORTER_HOST"] == "127.0.0.1"
    assert env["EXPORTER_PORT"] == "19130"


def test_build_sweep_ranking_picks_highest_feasible_and_best_safe_points() -> None:
    points = [
        {
            "scenario": "low_reuse",
            "concurrency": 1,
            "aggregates": {
                "req_s_mean": 4.0,
                "e2e_p95_ms_mean": 120.0,
                "hit_ratio_mean": 0.01,
                "quality_summary": {"metric_quality": "ok", "hit_ratio_source": "window"},
            },
            "recommendation": {
                "baseline_hard_only_recommended_rps": 3.6,
                "dual_boundary_recommended_rps": 0.0,
                "hit_ratio_comparable": True,
            },
        },
        {
            "scenario": "low_reuse",
            "concurrency": 2,
            "aggregates": {
                "req_s_mean": 7.5,
                "e2e_p95_ms_mean": 200.0,
                "hit_ratio_mean": 0.08,
                "quality_summary": {"metric_quality": "ok", "hit_ratio_source": "window"},
            },
            "recommendation": {
                "baseline_hard_only_recommended_rps": 6.75,
                "dual_boundary_recommended_rps": 6.75,
                "hit_ratio_comparable": True,
            },
        },
        {
            "scenario": "low_reuse",
            "concurrency": 4,
            "aggregates": {
                "req_s_mean": 9.0,
                "e2e_p95_ms_mean": 210.0,
                "hit_ratio_mean": 0.03,
                "quality_summary": {"metric_quality": "ok", "hit_ratio_source": "window"},
            },
            "recommendation": {
                "baseline_hard_only_recommended_rps": 8.1,
                "dual_boundary_recommended_rps": 0.0,
                "hit_ratio_comparable": True,
            },
        },
    ]

    ranking = _build_sweep_ranking(points)
    summary = ranking["by_scenario"]["low_reuse"]

    assert summary["highest_feasible_point"]["concurrency"] == 4
    assert summary["best_safe_point"]["concurrency"] == 2
    assert summary["ranked_points"][0]["concurrency"] == 4


def test_build_plot_series_exposes_rank_markers() -> None:
    doc = {
        "ranking": {
            "by_scenario": {
                "low_reuse": {
                    "ranked_points": [
                        {"concurrency": 4, "req_s_mean": 9.0},
                        {"concurrency": 2, "req_s_mean": 7.5},
                    ],
                    "highest_feasible_point": {"concurrency": 4, "req_s_mean": 9.0},
                    "best_safe_point": {"concurrency": 2, "req_s_mean": 7.5},
                }
            }
        }
    }

    series = _build_plot_series(doc)

    assert series["low_reuse"]["x"] == [4, 2]
    assert series["low_reuse"]["y"] == [9.0, 7.5]
    assert series["low_reuse"]["highest_feasible_point"]["concurrency"] == 4
    assert series["low_reuse"]["best_safe_point"]["concurrency"] == 2


def test_markdown_embeds_ranking_plot_image() -> None:
    doc = {
        "meta": {"base_url": "http://localhost:8000", "model": "demo", "repeats": 3},
        "points": [],
        "ranking": {"by_scenario": {"low_reuse": {"highest_feasible_point": None, "best_safe_point": None}}},
    }

    md = _to_markdown(doc, ranking_plot_name="capacity_sweep_ranking.png")

    assert "## Ranking Plot" in md
    assert "![capacity_sweep_ranking](capacity_sweep_ranking.png)" in md


def test_experiment_template_invokes_capacity_sweep() -> None:
    template = Path("scripts/experiment_template_local.sh").read_text(encoding="utf-8")

    assert "RUN_CAPACITY_SWEEP" in template
    assert "scripts/run_capacity_sweep.py" in template
    assert "SWEEP_CONCURRENCY_VALUES" in template
    assert "RUN_THRESHOLD_CURVE" in template
    assert "scripts/plot_threshold_curve.py" in template
    assert "RUN_FINAL_REPORT" in template
    assert "scripts/generate_final_report.py" in template
