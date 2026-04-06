from exporter.adapters.vllm import parse_prometheus_text


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
