from __future__ import annotations

from dataclasses import dataclass

from exporter.schemas import DerivedSnapshot, NativeSnapshot


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _usage_ratio(usage_perc: float) -> float:
    if usage_perc <= 0:
        return 0.0
    if usage_perc <= 1.0:
        return _clamp01(usage_perc)
    return _clamp01(usage_perc / 100.0)


@dataclass
class DeriveState:
    """Tracks prior scrapes for counter-like semantics (e.g. prefix hit counters)."""

    last_prefix_hits: float | None = None


@dataclass
class DeriveConfig:
    tokens_per_block: int = 16


def compute_derived(
    native: NativeSnapshot,
    state: DeriveState | None = None,
    cfg: DeriveConfig | None = None,
) -> DerivedSnapshot:
    _ = cfg or DeriveConfig()

    total = max(native.total_blocks, 0.0)
    active = native.active_blocks
    reusable = native.reusable_cached_blocks
    free_uncached = native.free_uncached_blocks
    usage_ratio = _usage_ratio(native.usage_perc)

    if total > 0:
        hidden = reusable / total
        effective = (active + reusable) / total
        cold = free_uncached / total
    else:
        # Keep absolute block counts unknown instead of fabricating a 100-block universe.
        hidden = 0.0
        effective = usage_ratio
        cold = 1.0 - usage_ratio

    if native.prefix_queries > 0:
        hit_ratio = native.prefix_hits / native.prefix_queries
    else:
        hit_ratio = 0.0

    cache_hit_ratio: float | None
    if state is not None:
        hits, queries = native.prefix_hits, native.prefix_queries
        if state.last_prefix_hits is not None and hits < state.last_prefix_hits:
            pass
        state.last_prefix_hits = hits
        if queries > 0:
            cache_hit_ratio = hits / queries
        else:
            cache_hit_ratio = None
    else:
        cache_hit_ratio = hit_ratio if native.prefix_queries > 0 else None

    return DerivedSnapshot(
        backend=native.backend,
        model=native.model,
        instance=native.instance,
        model_group=native.model_group,
        total_blocks=total,
        active_blocks=active,
        reusable_cached_blocks=reusable,
        free_uncached_blocks=free_uncached,
        duplicate_cached_blocks=native.duplicate_cached_blocks,
        prefix_blocks=reusable,
        hidden_reuse_ready_perc=_clamp01(hidden),
        effective_residency_perc=_clamp01(effective),
        cold_free_perc=_clamp01(cold),
        cache_hit_ratio=cache_hit_ratio if cache_hit_ratio is None else _clamp01(cache_hit_ratio),
        prefix_hits_metric_name=native.prefix_hits_metric_name,
        prefix_queries_metric_name=native.prefix_queries_metric_name,
        prefix_metric_semantics=native.prefix_metric_semantics,
        prefix_metric_comparability=native.prefix_metric_comparability,
        prefix_metric_basis=native.prefix_metric_basis,
        queue_depth=native.queue_depth,
        running_requests=native.running_requests,
    )
