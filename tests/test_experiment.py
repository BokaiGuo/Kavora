from benchmark.experiment import aggregate_run_entries, run_windowed_experiment
from benchmark.window_metrics import make_metric_snapshot, summarize_entry_quality, summarize_runs_quality


def _entry(*, req_s: float, e2e_ms: float, hit_ratio: float | None, metric_quality: str, hit_ratio_source: str):
    entry = {
        "repeat": 1,
        "summary": {
            "requests": {"ok": 10, "total": 10, "failed": 0},
            "latency": {"e2e_latency_p95_ms": e2e_ms},
            "throughput": {"req_s": req_s},
        },
        "exporter_metrics": {},
    }
    if hit_ratio_source == "snapshot_fallback":
        entry["exporter_metrics"]["kvcache_kv_cache_hit_ratio"] = hit_ratio
        return entry

    entry["derived_window_metrics"] = {
        "cache_hit_ratio_window": hit_ratio if hit_ratio_source == "window" else None,
        "metrics_missing": metric_quality == "missing",
        "metrics_stale": metric_quality == "stale",
    }
    return entry


def _with_prefix_check(entry: dict, *, comparable: bool | None = None, token_fallback: bool | None = None) -> dict:
    if comparable is not None:
        entry["exporter_metrics"]["kvcache_exporter_prefix_metric_comparable"] = 1.0 if comparable else 0.0
    if token_fallback is not None:
        entry["exporter_metrics"]["kvcache_exporter_prefix_metric_token_fallback"] = 1.0 if token_fallback else 0.0
    return entry


def test_summarize_entry_quality_prefers_window_value() -> None:
    entry = _entry(req_s=10.0, e2e_ms=100.0, hit_ratio=0.7, metric_quality="ok", hit_ratio_source="window")

    assert summarize_entry_quality(entry) == {
        "cache_hit_ratio": 0.7,
        "metric_quality": "ok",
        "hit_ratio_source": "window",
        "evidence_quality": "missing",
    }


def test_summarize_runs_quality_reports_mixed_sources() -> None:
    entries = [
        _with_prefix_check(
            _entry(req_s=10.0, e2e_ms=100.0, hit_ratio=0.7, metric_quality="ok", hit_ratio_source="window"),
            comparable=True,
            token_fallback=False,
        ),
        _with_prefix_check(
            _entry(req_s=8.0, e2e_ms=120.0, hit_ratio=0.4, metric_quality="ok", hit_ratio_source="snapshot_fallback"),
            comparable=False,
            token_fallback=True,
        ),
        _entry(req_s=6.0, e2e_ms=140.0, hit_ratio=None, metric_quality="missing", hit_ratio_source="missing"),
    ]

    summary = summarize_runs_quality(entries)

    assert summary["num_runs_ok_metrics"] == 2
    assert summary["num_runs_missing_hit_ratio"] == 1
    assert summary["num_runs_window_hit_ratio"] == 2
    assert summary["num_runs_snapshot_fallback_hit_ratio"] == 1
    assert summary["num_runs_missing_hit_ratio_source"] == 0
    assert summary["metric_quality"] == "mixed"
    assert summary["hit_ratio_source"] == "mixed"
    assert summary["ok_metric_run_fraction"] == 2 / 3
    assert summary["hit_ratio_comparable"] is False
    assert summary["num_runs_prefix_metric_strict"] == 1
    assert summary["num_runs_prefix_metric_token_fallback"] == 1
    assert summary["num_runs_prefix_metric_missing"] == 1
    assert summary["prefix_metric_check"] == "mixed"


def test_aggregate_run_entries_embeds_quality_summary() -> None:
    entries = [
        _with_prefix_check(
            _entry(req_s=10.0, e2e_ms=100.0, hit_ratio=0.7, metric_quality="ok", hit_ratio_source="window"),
            comparable=True,
            token_fallback=False,
        ),
        _with_prefix_check(
            _entry(req_s=8.0, e2e_ms=120.0, hit_ratio=None, metric_quality="stale", hit_ratio_source="window"),
            comparable=True,
            token_fallback=False,
        ),
    ]

    out = aggregate_run_entries(entries)

    assert out["repeats"] == 2
    assert out["req_s_mean"] == 9.0
    assert out["e2e_p95_ms_mean"] == 110.0
    assert out["hit_ratio_mean"] == 0.7
    assert out["quality_summary"]["num_runs_stale_metrics"] == 1
    assert out["quality_summary"]["metric_quality"] == "mixed"
    assert out["quality_summary"]["hit_ratio_source"] == "window"
    assert out["quality_summary"]["num_runs_prefix_metric_strict"] == 2
    assert out["quality_summary"]["prefix_metric_check"] == "strict"


async def _fake_run_custom_http(**_: object) -> dict[str, object]:
    return {
        "requests": {"ok": 10, "total": 10, "failed": 0},
        "latency": {"e2e_latency_p95_ms": 100.0},
        "throughput": {"req_s": 10.0},
        "timing": {
            "run_started_ts": 100.0,
            "warmup_end_ts": 100.1,
            "run_finished_ts": 100.2,
        },
    }


def test_run_windowed_experiment_does_not_mark_backend_window_stale_from_exporter_freshness(monkeypatch) -> None:
    backend_before = make_metric_snapshot(
        {
            "vllm:prefix_cache_hits_total": 10.0,
            "vllm:prefix_cache_queries_total": 20.0,
        },
        ts=99.0,
    )
    backend_after = make_metric_snapshot(
        {
            "vllm:prefix_cache_hits_total": 22.0,
            "vllm:prefix_cache_queries_total": 35.0,
        },
        ts=100.2,
    )
    exporter_before = make_metric_snapshot(
        {"kvcache_exporter_scrape_last_success_timestamp_seconds": 50.0},
        ts=99.0,
    )
    exporter_after = make_metric_snapshot(
        {"kvcache_exporter_scrape_last_success_timestamp_seconds": 50.0},
        ts=100.2,
    )
    backend_calls = iter([backend_before, backend_after])
    exporter_calls = iter([exporter_before, exporter_after])

    monkeypatch.setattr("benchmark.experiment.fetch_backend_snapshot", lambda url: next(backend_calls))
    monkeypatch.setattr("benchmark.experiment.fetch_exporter_snapshot", lambda url: next(exporter_calls))
    monkeypatch.setattr("benchmark.experiment.run_custom_http", _fake_run_custom_http)
    monkeypatch.setattr("benchmark.experiment.time.time", lambda: 100.0)

    entry = run_windowed_experiment(
        repeat=1,
        base_url="http://localhost:8000",
        endpoint="/v1/completions",
        model="demo",
        backend_metrics_url="http://localhost:8000/metrics",
        exporter_metrics_url="http://localhost:9108/metrics",
        num_requests=10,
        concurrency=1,
        base_seed=42,
        input_len=16,
        output_len=8,
        warmup_requests=0,
        timeout_s=1.0,
        shared_prefix_ratio=0.0,
        shared_prefix_len=0,
        unique_suffix_len=16,
    )

    assert entry["derived_window_metrics"]["cache_hit_ratio_window"] == 0.8
    assert entry["derived_window_metrics"]["metrics_stale"] is False
    assert entry["quality"]["metric_quality"] == "ok"
