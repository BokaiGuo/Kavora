from benchmark.collect import fetch_metrics_snapshot


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    last_kwargs = None

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        type(self).last_kwargs = kwargs

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001, ANN201
        return None

    def get(self, url: str) -> _FakeResponse:  # noqa: ARG002
        return _FakeResponse(
            """
# TYPE demo gauge
demo{gpu="0"} 1
demo{gpu="1"} 2
"""
        )


def test_fetch_metrics_snapshot_returns_timestamped_metric_map(monkeypatch) -> None:
    monkeypatch.setattr("benchmark.collect.httpx.Client", _FakeClient)
    snapshot = fetch_metrics_snapshot("http://fake/metrics")

    assert _FakeClient.last_kwargs == {"timeout": 5.0, "trust_env": False}
    assert isinstance(snapshot["ts"], float)
    assert snapshot["error"] == ""
    assert snapshot["metrics"]["demo"] == 3.0
