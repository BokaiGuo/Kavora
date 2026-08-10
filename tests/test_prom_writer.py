from exporter.registry.prom_writer import PromWriter
from exporter.schemas import DerivedSnapshot


def _snapshot(*, cache_hit_ratio: float | None) -> DerivedSnapshot:
    return DerivedSnapshot(
        backend="vllm",
        model="demo",
        instance="i0",
        model_group="g0",
        total_blocks=100.0,
        active_blocks=40.0,
        reusable_cached_blocks=10.0,
        free_uncached_blocks=50.0,
        duplicate_cached_blocks=0.0,
        prefix_blocks=10.0,
        hidden_reuse_ready_perc=0.1,
        effective_residency_perc=0.5,
        cold_free_perc=0.5,
        cache_hit_ratio=cache_hit_ratio,
        queue_depth=3.0,
        running_requests=2.0,
    )


def test_prom_writer_removes_optional_hit_ratio_metric_when_missing() -> None:
    writer = PromWriter()

    writer.write(_snapshot(cache_hit_ratio=0.5))
    payload = writer.render().decode("utf-8")
    assert "kvcache_kv_cache_hit_ratio 0.5" in payload
    assert "kvcache_backend_queue_depth 3.0" in payload
    assert "kvcache_backend_running_requests 2.0" in payload

    writer.write(_snapshot(cache_hit_ratio=None))
    payload = writer.render().decode("utf-8")
    assert "kvcache_kv_cache_hit_ratio" not in payload

    writer.write(_snapshot(cache_hit_ratio=0.8))
    payload = writer.render().decode("utf-8")
    assert "kvcache_kv_cache_hit_ratio 0.8" in payload
