import asyncio

from benchmark.runner import deterministic_request_seed, run_custom_http


def test_deterministic_request_seed_is_stable() -> None:
    s1 = deterministic_request_seed(42, 7)
    s2 = deterministic_request_seed(42, 7)
    s3 = deterministic_request_seed(42, 8)
    assert s1 == s2
    assert s1 != s3


def test_latency_schema_uses_e2e_field() -> None:
    summary = asyncio.run(
        run_custom_http(
            base_url="http://127.0.0.1:9",  # always fail quickly
            endpoint="/v1/completions",
            model="demo",
            num_requests=2,
            warmup_requests=1,
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
    assert summary["requests"]["total"] == 2
    assert summary["warmup"]["requests"] == 1
    assert summary["warmup"]["completed"] == 1
    assert summary["timing"]["run_started_ts"] <= summary["timing"]["warmup_end_ts"] <= summary["timing"]["run_finished_ts"]


class _FakeAsyncResponse:
    status_code = 200


class _FakeAsyncClient:
    last_kwargs = None

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        type(self).last_kwargs = kwargs

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001, ANN201
        return None

    async def post(self, url: str, json: dict, timeout: float) -> _FakeAsyncResponse:  # noqa: ARG002
        return _FakeAsyncResponse()


def test_run_custom_http_disables_proxy_inheritance(monkeypatch) -> None:
    monkeypatch.setattr("benchmark.runner.httpx.AsyncClient", _FakeAsyncClient)

    summary = asyncio.run(
        run_custom_http(
            base_url="http://localhost:30000",
            endpoint="/v1/completions",
            model="demo",
            num_requests=1,
            warmup_requests=0,
            concurrency=1,
            base_seed=1,
            input_len=8,
            output_len=4,
            timeout_s=0.01,
        )
    )

    assert _FakeAsyncClient.last_kwargs == {"trust_env": False}
    assert summary["requests"]["ok"] == 1
