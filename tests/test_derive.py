from exporter.derive.compute import DeriveConfig, DeriveState, compute_derived
from exporter.schemas import Backend, NativeSnapshot


def _snapshot(**kwargs: float) -> NativeSnapshot:
    extra = {
        "num_requests_running": 2.0,
        "avg_prefill_kv_computed_tokens": 64.0,
        "avg_max_generation_tokens": 64.0,
        "evictions_total": 1.0,
    }
    prefix_hits = 10.0
    prefix_queries = 20.0
    if "cache_hits_total" in kwargs:
        prefix_hits = float(kwargs.pop("cache_hits_total"))
    if "cache_misses_total" in kwargs:
        m = float(kwargs.pop("cache_misses_total"))
        prefix_queries = prefix_hits + m
    base = dict(
        backend=Backend.vllm.value,
        model="demo",
        instance="i0",
        total_blocks=100.0,
        usage_perc=40.0,
        active_blocks=0.0,
        reusable_cached_blocks=0.0,
        free_uncached_blocks=0.0,
        duplicate_cached_blocks=0.0,
        prefix_hits=prefix_hits,
        prefix_queries=prefix_queries,
        extra=extra,
    )
    base.update(kwargs)
    return NativeSnapshot(**base)


def test_compute_derived_estimation() -> None:
    state = DeriveState()
    cfg = DeriveConfig(tokens_per_block=16)
    out = compute_derived(_snapshot(), state, cfg)
    assert out.active_blocks is not None
    assert out.prefix_blocks is not None
    assert out.hidden_reuse_ready_perc is not None


def test_counter_reset_handling() -> None:
    state = DeriveState()
    cfg = DeriveConfig()
    compute_derived(_snapshot(cache_hits_total=100.0), state, cfg)
    out = compute_derived(_snapshot(cache_hits_total=5.0, cache_misses_total=20.0), state, cfg)
    if out.cache_hit_ratio is not None:
        assert 0.0 <= out.cache_hit_ratio <= 1.0
