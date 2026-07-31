from benchmark.window_metrics import (
    compute_counter_delta,
    compute_hit_ratio_from_deltas,
    derive_window_metrics,
    get_entry_hit_ratio_quality_and_source,
    make_metric_snapshot,
    make_run_entry,
    snapshot_is_stale,
)


def test_make_run_entry_includes_window_metric_and_snapshot_scaffolding() -> None:
    entry = make_run_entry(
        repeat=3,
        summary={"requests": {"total": 1}},
        exporter_metrics={"kvcache_kv_cache_hit_ratio": 0.5},
        start_ts=10.0,
        end_ts=12.5,
    )

    assert entry["repeat"] == 3
    assert entry["window"]["start_ts"] == 10.0
    assert entry["window"]["warmup_end_ts"] is None
    assert entry["window"]["end_ts"] == 12.5
    assert entry["metric_snapshots"]["backend_before"] is None
    assert entry["metric_snapshots"]["exporter_after"] is None
    assert entry["derived_window_metrics"]["cache_hit_ratio_window"] is None
    assert entry["exporter_metrics"]["kvcache_kv_cache_hit_ratio"] == 0.5


def test_compute_counter_delta_handles_basic_and_reset_cases() -> None:
    assert compute_counter_delta(10.0, 25.0) == 15.0
    assert compute_counter_delta(25.0, 10.0) is None
    assert compute_counter_delta(None, 10.0) is None


def test_derive_window_metrics_uses_snapshot_counter_deltas() -> None:
    before = make_metric_snapshot(
        {
            "vllm:prefix_cache_hits_total": 10.0,
            "vllm:prefix_cache_queries_total": 20.0,
        },
        ts=100.0,
    )
    after = make_metric_snapshot(
        {
            "vllm:prefix_cache_hits_total": 22.0,
            "vllm:prefix_cache_queries_total": 35.0,
        },
        ts=120.0,
    )

    out = derive_window_metrics(before, after)

    assert out["prefix_hits_delta"] == 12.0
    assert out["prefix_queries_delta"] == 15.0
    assert out["cache_hit_ratio_window"] == 0.8
    assert out["metrics_missing"] is False


def test_derive_window_metrics_supports_sglang_cached_token_counters() -> None:
    before = make_metric_snapshot(
        {
            "sglang:cached_tokens_total": 40.0,
            "sglang:prompt_tokens_total": 100.0,
        },
        ts=100.0,
    )
    after = make_metric_snapshot(
        {
            "sglang:cached_tokens_total": 88.0,
            "sglang:prompt_tokens_total": 160.0,
        },
        ts=120.0,
    )

    out = derive_window_metrics(before, after)

    assert out["prefix_hits_delta"] == 48.0
    assert out["prefix_queries_delta"] == 60.0
    assert out["cache_hit_ratio_window"] == 0.8
    assert out["metrics_missing"] is False


def test_derive_window_metrics_treats_missing_before_counter_as_zero_when_scrape_succeeds() -> None:
    before = make_metric_snapshot({}, ts=100.0)
    after = make_metric_snapshot(
        {
            "sglang:cached_tokens_total": 48.0,
            "sglang:prompt_tokens_total": 60.0,
        },
        ts=120.0,
    )

    out = derive_window_metrics(before, after)

    assert out["prefix_hits_delta"] == 48.0
    assert out["prefix_queries_delta"] == 60.0
    assert out["cache_hit_ratio_window"] == 0.8
    assert out["metrics_missing"] is False


def test_derive_window_metrics_marks_zero_query_window_as_missing() -> None:
    before = make_metric_snapshot(
        {
            "vllm:prefix_cache_hits_total": 10.0,
            "vllm:prefix_cache_queries_total": 20.0,
        },
        ts=100.0,
    )
    after = make_metric_snapshot(
        {
            "vllm:prefix_cache_hits_total": 10.0,
            "vllm:prefix_cache_queries_total": 20.0,
        },
        ts=120.0,
    )

    out = derive_window_metrics(before, after)

    assert out["prefix_hits_delta"] == 0.0
    assert out["prefix_queries_delta"] == 0.0
    assert out["cache_hit_ratio_window"] is None
    assert out["metrics_missing"] is True


def test_compute_hit_ratio_from_deltas_returns_none_without_queries() -> None:
    assert compute_hit_ratio_from_deltas(3.0, 0.0) is None
    assert compute_hit_ratio_from_deltas(3.0, None) is None


def test_snapshot_is_stale_uses_exporter_last_success_timestamp() -> None:
    fresh = make_metric_snapshot({"kvcache_exporter_scrape_last_success_timestamp_seconds": 120.0}, ts=121.0)
    stale = make_metric_snapshot({"kvcache_exporter_scrape_last_success_timestamp_seconds": 80.0}, ts=121.0)

    assert snapshot_is_stale(fresh, reference_ts=100.0) is False
    assert snapshot_is_stale(stale, reference_ts=100.0) is True


def test_get_entry_hit_ratio_quality_and_source_prefers_window_semantics() -> None:
    entry = make_run_entry(
        repeat=1,
        summary={"requests": {"total": 1}},
        exporter_metrics={"kvcache_kv_cache_hit_ratio": 0.2},
    )
    entry["derived_window_metrics"]["cache_hit_ratio_window"] = 0.8

    assert get_entry_hit_ratio_quality_and_source(entry) == (0.8, "ok", "window")


def test_get_entry_hit_ratio_quality_and_source_uses_snapshot_fallback_for_legacy_entries() -> None:
    legacy_entry = {
        "repeat": 1,
        "summary": {"requests": {"total": 1}, "latency": {}, "throughput": {}},
        "exporter_metrics": {"kvcache_kv_cache_hit_ratio": 0.5},
    }

    assert get_entry_hit_ratio_quality_and_source(legacy_entry) == (0.5, "ok", "snapshot_fallback")


def test_get_entry_hit_ratio_quality_and_source_does_not_fallback_when_window_scaffolding_exists() -> None:
    entry = make_run_entry(
        repeat=1,
        summary={"requests": {"total": 1}},
        exporter_metrics={"kvcache_kv_cache_hit_ratio": 0.9},
    )
    entry["derived_window_metrics"]["cache_hit_ratio_window"] = None
    entry["derived_window_metrics"]["metrics_missing"] = False
    entry["derived_window_metrics"]["metrics_stale"] = False

    assert get_entry_hit_ratio_quality_and_source(entry) == (None, "missing", "window")


def test_get_entry_hit_ratio_quality_and_source_ignores_non_finite_snapshot_fallback() -> None:
    legacy_entry = {
        "repeat": 1,
        "summary": {"requests": {"total": 1}, "latency": {}, "throughput": {}},
        "exporter_metrics": {"kvcache_kv_cache_hit_ratio": float("nan")},
    }

    assert get_entry_hit_ratio_quality_and_source(legacy_entry) == (None, "missing", "missing")
