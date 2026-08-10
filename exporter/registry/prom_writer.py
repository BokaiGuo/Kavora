from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Info, generate_latest

from exporter.schemas import DerivedSnapshot


class PromWriter:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._scrape_failures = Counter(
            "kvcache_exporter_scrape_failures_total",
            "Total failed backend metric scrapes",
            registry=self.registry,
        )
        self._prefix_metric_info = Info(
            "kvcache_exporter_prefix_metric_semantics",
            "Selected prefix hit/query metric semantics and source names",
            registry=self.registry,
        )
        self._prefix_metric_comparable = Gauge(
            "kvcache_exporter_prefix_metric_comparable",
            "1 when prefix hit ratio comes from strict comparable query-based counters",
            registry=self.registry,
        )
        self._prefix_metric_token_fallback = Gauge(
            "kvcache_exporter_prefix_metric_token_fallback",
            "1 when prefix hit ratio falls back to token counters such as cached_tokens_total/prompt_tokens_total",
            registry=self.registry,
        )
        self._prefix_metric_estimated = Gauge(
            "kvcache_exporter_prefix_metric_estimated",
            "1 when prefix evidence is estimated rather than strict or fallback",
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
            ("kvcache_backend_queue_depth", "Backend requests waiting in queue"),
            ("kvcache_backend_running_requests", "Backend requests currently running"),
            ("kvcache_exporter_scrape_last_success_timestamp_seconds", "Unix time of last successful backend scrape"),
            ("kvcache_exporter_scrape_consecutive_failures", "Consecutive failed scrapes since last success"),
        ]
        self._gauge_help = {name: help_text for name, help_text in names}
        self._optional_gauges = {"kvcache_kv_cache_hit_ratio", "kvcache_backend_queue_depth", "kvcache_backend_running_requests"}
        self._gauges = {
            name: Gauge(name, help_text, registry=self.registry)
            for name, help_text in names
            if name not in self._optional_gauges
        }

    def _set_optional_gauge(self, name: str, value: float | None) -> None:
        gauge = self._gauges.get(name)
        if value is None:
            if gauge is not None:
                self.registry.unregister(gauge)
                del self._gauges[name]
            return

        if gauge is None:
            gauge = Gauge(name, self._gauge_help[name], registry=self.registry)
            self._gauges[name] = gauge
        gauge.set(value)

    def write(self, snap: DerivedSnapshot) -> None:
        self._gauges["kvcache_kv_total_blocks"].set(snap.total_blocks)
        self._gauges["kvcache_kv_active_blocks"].set(snap.active_blocks)
        self._gauges["kvcache_kv_reusable_cached_blocks"].set(snap.reusable_cached_blocks)
        self._gauges["kvcache_kv_free_uncached_blocks"].set(snap.free_uncached_blocks)
        self._gauges["kvcache_kv_duplicate_cached_blocks"].set(snap.duplicate_cached_blocks)
        self._gauges["kvcache_kv_hidden_reuse_ready_perc"].set(snap.hidden_reuse_ready_perc)
        self._gauges["kvcache_kv_effective_residency_perc"].set(snap.effective_residency_perc)
        self._gauges["kvcache_kv_cold_free_perc"].set(snap.cold_free_perc)
        self._set_optional_gauge("kvcache_kv_cache_hit_ratio", snap.cache_hit_ratio)
        self._set_optional_gauge("kvcache_backend_queue_depth", snap.queue_depth)
        self._set_optional_gauge("kvcache_backend_running_requests", snap.running_requests)
        self._prefix_metric_info.info(
            {
                "backend": snap.backend or "unknown",
                "semantics": snap.prefix_metric_semantics or "missing",
                "comparability": snap.prefix_metric_comparability or "missing",
                "basis": snap.prefix_metric_basis or "missing",
                "hits_metric": snap.prefix_hits_metric_name or "missing",
                "queries_metric": snap.prefix_queries_metric_name or "missing",
                "evidence_quality": snap.prefix_evidence_quality or "missing",
            }
        )
        self._prefix_metric_comparable.set(1.0 if snap.prefix_metric_comparability == "strict" else 0.0)
        self._prefix_metric_token_fallback.set(1.0 if snap.prefix_metric_semantics == "token_counter_fallback" else 0.0)
        self._prefix_metric_estimated.set(1.0 if snap.prefix_evidence_quality == "estimated" else 0.0)

    def set_scrape_health(self, *, last_success_ts: float | None, consecutive_failures: int) -> None:
        if last_success_ts is not None:
            self._gauges["kvcache_exporter_scrape_last_success_timestamp_seconds"].set(last_success_ts)
        self._gauges["kvcache_exporter_scrape_consecutive_failures"].set(float(consecutive_failures))

    def inc_scrape_failure(self) -> None:
        self._scrape_failures.inc()

    def render(self) -> bytes:
        return generate_latest(self.registry)
