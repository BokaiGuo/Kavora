from __future__ import annotations

import asyncio

from exporter.adapters.vllm import VllmAdapter
from exporter.derive.compute import compute_derived
from exporter.registry.prom_writer import PromWriter


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    last_kwargs = None

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        type(self).last_kwargs = kwargs

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001, ANN201
        return None

    async def get(self, url: str) -> _FakeResponse:  # noqa: ARG002
        text = """
# TYPE vllm_obs:kv_total_blocks gauge
vllm_obs:kv_total_blocks{gpu="0"} 512
vllm_obs:kv_total_blocks{gpu="1"} 512
# TYPE vllm_obs:kv_active_blocks gauge
vllm_obs:kv_active_blocks 256
# TYPE vllm_obs:kv_reusable_cached_blocks gauge
vllm_obs:kv_reusable_cached_blocks 128
# TYPE vllm_obs:kv_free_uncached_blocks gauge
vllm_obs:kv_free_uncached_blocks 640
# TYPE vllm_obs:kv_duplicate_cached_blocks gauge
vllm_obs:kv_duplicate_cached_blocks 16
# TYPE vllm:prefix_cache_hits counter
vllm:prefix_cache_hits 80
# TYPE vllm:prefix_cache_queries counter
vllm:prefix_cache_queries 100
"""
        return _FakeResponse(text)


def test_contract_fake_metrics_to_exporter_output(monkeypatch) -> None:
    # Fake a backend scrape text -> adapter parse -> derive -> writer metrics bytes.
    monkeypatch.setattr("exporter.adapters.vllm.httpx.AsyncClient", _FakeAsyncClient)
    adapter = VllmAdapter(
        metrics_url="http://fake/metrics",
        model_name="demo",
        instance_name="i0",
        model_group="g0",
    )
    native = asyncio.run(adapter.collect())
    derived = compute_derived(native)
    writer = PromWriter()
    writer.write(derived)
    payload = writer.render().decode("utf-8")

    assert _FakeAsyncClient.last_kwargs == {"timeout": 5.0, "trust_env": False}
    assert "kvcache_kv_total_blocks" in payload
    assert "kvcache_kv_active_blocks" in payload
    assert "kvcache_kv_hidden_reuse_ready_perc" in payload
    assert "kvcache_kv_cache_hit_ratio" in payload
    assert "kvcache_exporter_prefix_metric_semantics_info" in payload
    assert 'semantics="prefix_query_counters"' in payload
    assert "kvcache_exporter_prefix_metric_comparable 1.0" in payload
