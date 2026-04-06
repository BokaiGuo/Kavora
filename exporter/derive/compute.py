from __future__ import annotations

from dataclasses import dataclass

from exporter.schemas import DerivedSnapshot, NativeSnapshot


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


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

    if total <= 0 and native.usage_perc > 0:
        total = 100.0
        active = native.usage_perc
        free_uncached = total - active
        reusable = 0.0

    hidden = (reusable / total) if total > 0 else 0.0
    effective = ((active + reusable) / total) if total > 0 else 0.0
    cold = (free_uncached / total) if total > 0 else 0.0

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
    )
