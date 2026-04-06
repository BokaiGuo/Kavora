from benchmark.runner import deterministic_request_seed, run_custom_http


def test_deterministic_request_seed_is_stable() -> None:
    s1 = deterministic_request_seed(42, 7)
    s2 = deterministic_request_seed(42, 7)
    s3 = deterministic_request_seed(42, 8)
    assert s1 == s2
    assert s1 != s3


def test_latency_schema_uses_e2e_field() -> None:
    summary = __import__("asyncio").run(
        run_custom_http(
            base_url="http://127.0.0.1:9",  # always fail quickly
            endpoint="/v1/completions",
            model="demo",
            num_requests=2,
            concurrency=1,
            base_seed=1,
            input_len=8,
            output_len=4,
            timeout_s=0.01,
        )
    )
    latency = summary["latency"]
    assert "e2e_latency_p95_ms" in latency
    assert latency["ttft_p95_ms"] is None
    assert latency["tpot_p95_ms"] is None
