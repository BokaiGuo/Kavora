from exporter.registry.prom_writer import PromWriter
from exporter.schemas import DerivedSnapshot


def _snapshot(*, semantics: str, comparability: str, basis: str, hits_metric: str, queries_metric: str) -> DerivedSnapshot:
    return DerivedSnapshot(
        backend="sglang",
        model="demo",
        instance="i0",
        model_group="g0",
        total_blocks=64.0,
        active_blocks=16.0,
        reusable_cached_blocks=0.0,
        free_uncached_blocks=48.0,
        duplicate_cached_blocks=0.0,
        prefix_blocks=0.0,
        hidden_reuse_ready_perc=0.0,
        effective_residency_perc=0.25,
        cold_free_perc=0.75,
        cache_hit_ratio=0.6,
        prefix_hits_metric_name=hits_metric,
        prefix_queries_metric_name=queries_metric,
        prefix_metric_semantics=semantics,
        prefix_metric_comparability=comparability,
        prefix_metric_basis=basis,
        prefix_evidence_quality="fallback",
    )


def test_prom_writer_exports_prefix_metric_semantics_self_check() -> None:
    writer = PromWriter()
    writer.write(
        _snapshot(
            semantics="token_counter_fallback",
            comparability="directional",
            basis="tokens",
            hits_metric="sglang:cached_tokens_total",
            queries_metric="sglang:prompt_tokens_total",
        )
    )

    payload = writer.render().decode("utf-8")

    assert "kvcache_exporter_prefix_metric_semantics_info" in payload
    assert 'semantics="token_counter_fallback"' in payload
    assert 'basis="tokens"' in payload
    assert 'hits_metric="sglang:cached_tokens_total"' in payload
    assert "kvcache_exporter_prefix_metric_comparable 0.0" in payload
    assert "kvcache_exporter_prefix_metric_token_fallback 1.0" in payload
    assert 'evidence_quality="fallback"' in payload
    assert "kvcache_exporter_prefix_metric_estimated 0.0" in payload
