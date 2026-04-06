from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from exporter.schemas import DerivedSnapshot


class PromWriter:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._scrape_failures = Counter(
            "kvcache_exporter_scrape_failures_total",
            "Total failed backend metric scrapes",
            registry=self.registry,
        )
        names = [
            ("kvcache_kv_total_blocks", "Total blocks"),
            ("kvcache_kv_active_blocks", "Active blocks"),
            ("kvcache_kv_reusable_cached_blocks", "Reusable cached blocks"),
            ("kvcache_kv_free_uncached_blocks", "Cold free blocks"),
            ("kvcache_kv_duplicate_cached_blocks", "Duplicate cached blocks"),
            ("kvcache_kv_hidden_reuse_ready_perc", "Hidden reuse ratio"),
            ("kvcache_kv_effective_residency_perc", "Effective residency ratio"),
            ("kvcache_kv_cold_free_perc", "Cold free ratio"),
            ("kvcache_kv_cache_hit_ratio", "Prefix hit ratio"),
            ("kvcache_exporter_scrape_last_success_timestamp_seconds", "Unix time of last successful backend scrape"),
            ("kvcache_exporter_scrape_consecutive_failures", "Consecutive failed scrapes since last success"),
        ]
        self._gauges = {
            n: Gauge(n, help, registry=self.registry) for n, help in names
        }

    def write(self, snap: DerivedSnapshot) -> None:
        self._gauges["kvcache_kv_total_blocks"].set(snap.total_blocks)
        self._gauges["kvcache_kv_active_blocks"].set(snap.active_blocks)
        self._gauges["kvcache_kv_reusable_cached_blocks"].set(snap.reusable_cached_blocks)
        self._gauges["kvcache_kv_free_uncached_blocks"].set(snap.free_uncached_blocks)
        self._gauges["kvcache_kv_duplicate_cached_blocks"].set(snap.duplicate_cached_blocks)
        self._gauges["kvcache_kv_hidden_reuse_ready_perc"].set(snap.hidden_reuse_ready_perc)
        self._gauges["kvcache_kv_effective_residency_perc"].set(snap.effective_residency_perc)
        self._gauges["kvcache_kv_cold_free_perc"].set(snap.cold_free_perc)
        if snap.cache_hit_ratio is not None:
            self._gauges["kvcache_kv_cache_hit_ratio"].set(snap.cache_hit_ratio)

    def set_scrape_health(self, *, last_success_ts: float | None, consecutive_failures: int) -> None:
        if last_success_ts is not None:
            self._gauges["kvcache_exporter_scrape_last_success_timestamp_seconds"].set(last_success_ts)
        self._gauges["kvcache_exporter_scrape_consecutive_failures"].set(float(consecutive_failures))

    def inc_scrape_failure(self) -> None:
        self._scrape_failures.inc()

    def render(self) -> bytes:
        return generate_latest(self.registry)
