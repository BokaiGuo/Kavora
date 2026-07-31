from __future__ import annotations

from benchmark.gateway_runner import config_hash, parse_paths, percentile, phase_attribution


def test_percentile_is_deterministic() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([], 0.95) is None


def test_parse_paths_requires_http_url() -> None:
    paths = parse_paths(["direct=http://127.0.0.1:1", "go_rust_stream=https://example.test"])
    assert paths[0].name == "direct"
    assert paths[1].stream is True


def test_phase_attribution_declares_missing_paths() -> None:
    assert phase_attribution([])["status"] == "insufficient_paths"


def test_config_hash_is_order_independent() -> None:
    assert config_hash({"b": 2, "a": 1}) == config_hash({"a": 1, "b": 2})
