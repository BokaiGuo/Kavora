from exporter.adapters.vllm import VllmAdapter, parse_prometheus_text
from exporter.adapters.sglang import SGLangAdapter
import asyncio


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
        # TYPE sglang:max_total_num_tokens gauge
        sglang:max_total_num_tokens 1024
        # TYPE sglang:num_used_tokens gauge
        sglang:num_used_tokens 256
        # TYPE sglang:token_usage gauge
        sglang:token_usage 0.25
        # TYPE sglang:cached_tokens_total counter
        sglang:cached_tokens_total{cache_source="total"} 300
        # TYPE sglang:prompt_tokens_total counter
        sglang:prompt_tokens_total 500
        """
        return _FakeResponse(text)


class _FakeVllmAsyncClient(_FakeAsyncClient):
    async def get(self, url: str) -> _FakeResponse:  # noqa: ARG002
        return _FakeResponse(
            """
            # TYPE vllm:kv_cache_usage_perc gauge
            vllm:kv_cache_usage_perc 0.5
            # TYPE vllm:num_requests_waiting gauge
            vllm:num_requests_waiting 3
            # TYPE vllm:num_requests_running gauge
            vllm:num_requests_running 2
            """
        )


def test_parse_prometheus_text_basic() -> None:
    text = """
    # HELP demo demo
    vllm:prefix_cache_hits 10
    vllm:prefix_cache_queries 25
    kvcache_kv_total_blocks{model="m"} 1024
    """
    samples = parse_prometheus_text(text)
    assert isinstance(samples, dict)
    assert samples["vllm:prefix_cache_hits"] == 10.0
    assert samples["vllm:prefix_cache_queries"] == 25.0
    assert samples["kvcache_kv_total_blocks"] == 1024.0


def test_parse_prometheus_text_skip_bad_line() -> None:
    text = 'not_a_prom_line\nvllm:prefix_cache_hits 2\n'
    samples = parse_prometheus_text(text)
    assert len(samples) == 1
    assert samples["vllm:prefix_cache_hits"] == 2.0


def test_sglang_adapter_supports_cached_token_metrics(monkeypatch) -> None:
    monkeypatch.setattr("exporter.adapters.sglang.httpx.AsyncClient", _FakeAsyncClient)
    adapter = SGLangAdapter(metrics_url="http://fake/metrics")

    native = asyncio.run(adapter.collect())

    assert _FakeAsyncClient.last_kwargs == {"timeout": 5.0, "trust_env": False}
    assert native.total_blocks == 64.0
    assert native.active_blocks == 16.0
    assert native.free_uncached_blocks == 48.0
    assert native.prefix_hits == 300.0
    assert native.prefix_queries == 500.0
    assert native.prefix_metric_semantics == "token_counter_fallback"
    assert native.prefix_metric_comparability == "directional"
    assert native.prefix_metric_basis == "tokens"
    assert native.prefix_evidence_quality == "fallback"
    assert native.block_evidence_quality == "estimated"


def test_vllm_adapter_preserves_queue_signals(monkeypatch) -> None:
    monkeypatch.setattr("exporter.adapters.vllm.httpx.AsyncClient", _FakeVllmAsyncClient)

    native = asyncio.run(VllmAdapter(metrics_url="http://fake/metrics").collect())

    assert native.queue_depth == 3.0
    assert native.running_requests == 2.0
    assert native.prefix_evidence_quality == "missing"
    assert native.block_evidence_quality == "estimated"
