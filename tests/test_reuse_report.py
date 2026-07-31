from scripts.run_reuse_experiment import _to_markdown


def test_reuse_markdown_includes_prefix_self_check_columns() -> None:
    report = {
        "meta": {
            "base_url": "http://localhost:8000",
            "exporter_metrics_url": "http://localhost:9108/metrics",
            "model": "demo",
            "repeats": 2,
        },
        "runs": {
            "low_reuse": [
                {
                    "repeat": 1,
                    "summary": {
                        "requests": {"ok": 10, "total": 10},
                        "latency": {"e2e_latency_p95_ms": 100.0},
                        "throughput": {"req_s": 9.0},
                    },
                    "quality": {"hit_ratio_source": "window"},
                    "derived_window_metrics": {"cache_hit_ratio_window": 0.8, "metrics_missing": False, "metrics_stale": False},
                    "exporter_metrics": {
                        "kvcache_kv_hidden_reuse_ready_perc": 0.1,
                        "kvcache_exporter_prefix_metric_comparable": 1.0,
                        "kvcache_exporter_prefix_metric_token_fallback": 0.0,
                    },
                }
            ],
            "high_reuse": [
                {
                    "repeat": 1,
                    "summary": {
                        "requests": {"ok": 10, "total": 10},
                        "latency": {"e2e_latency_p95_ms": 90.0},
                        "throughput": {"req_s": 10.0},
                    },
                    "quality": {"hit_ratio_source": "window"},
                    "derived_window_metrics": {"cache_hit_ratio_window": 0.7, "metrics_missing": False, "metrics_stale": False},
                    "exporter_metrics": {
                        "kvcache_kv_hidden_reuse_ready_perc": 0.2,
                        "kvcache_exporter_prefix_metric_comparable": 0.0,
                        "kvcache_exporter_prefix_metric_token_fallback": 1.0,
                    },
                }
            ],
        },
        "aggregates": {
            "low_reuse": {
                "req_s_mean": 9.0,
                "e2e_p95_ms_mean": 100.0,
                "hit_ratio_mean": 0.8,
                "hidden_reuse_mean": 0.1,
                "quality_summary": {
                    "num_runs_missing_hit_ratio": 0,
                    "num_runs_stale_metrics": 0,
                    "num_runs_prefix_metric_strict": 1,
                    "num_runs_prefix_metric_token_fallback": 0,
                    "total_runs": 1,
                    "prefix_metric_check": "strict",
                },
            },
            "high_reuse": {
                "req_s_mean": 10.0,
                "e2e_p95_ms_mean": 90.0,
                "hit_ratio_mean": 0.7,
                "hidden_reuse_mean": 0.2,
                "quality_summary": {
                    "num_runs_missing_hit_ratio": 0,
                    "num_runs_stale_metrics": 0,
                    "num_runs_prefix_metric_strict": 0,
                    "num_runs_prefix_metric_token_fallback": 1,
                    "total_runs": 1,
                    "prefix_metric_check": "token_fallback",
                },
            },
        },
    }

    md = _to_markdown(report)

    assert "prefix_check" in md
    assert "strict runs" in md
    assert "token fallback runs" in md
    assert "| low_reuse | 1 | 9.0000 | 100.00 | 0.8000 | ok/window | strict | 0.1000 | 10/10 |" in md
    assert "| high_reuse | 10.0000 | 90.00 | 0.7000 | 0 | 0 | token_fallback | 0/1 | 1/1 | 0.2000 |" in md
