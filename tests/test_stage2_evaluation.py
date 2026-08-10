from __future__ import annotations

import pytest

from benchmark.stage2_evaluation import (
    aggregate_repetitions,
    balanced_target_order,
    build_workload_messages,
    load_config,
    summarize_samples,
    summarize_vllm_window,
    TargetSpec,
)
from benchmark.stage2_report import render


def _valid_config() -> dict:
    return {
        "model": "qwen-local",
        "model_revision": "snapshot-abc",
        "backend_version": "vllm-1.2.3",
        "repetitions": 10,
        "requests_per_repetition": 4,
        "concurrency": 2,
        "targets": [
            {"strategy": "direct", "url": "http://127.0.0.1:18080"},
            {"strategy": "static", "url": "http://127.0.0.1:18100"},
            {"strategy": "load-aware", "url": "http://127.0.0.1:18101"},
            {"strategy": "kv-aware-shadow", "url": "http://127.0.0.1:18102"},
            {"strategy": "kv-aware-enforced", "url": "http://127.0.0.1:18103"},
        ],
        "backends": [
            {"id": "gpu-0", "metrics_url": "http://127.0.0.1:18080/metrics"},
            {"id": "gpu-1", "metrics_url": "http://127.0.0.1:18081/metrics"},
        ],
    }


def test_load_config_requires_full_real_backend_matrix(tmp_path) -> None:
    path = tmp_path / "stage2.yaml"
    config = _valid_config()
    config["targets"] = config["targets"][:-1]
    path.write_text(__import__("yaml").safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="kv-aware-enforced"):
        load_config(path)


def test_load_config_requires_ten_repetitions_and_two_backends(tmp_path) -> None:
    path = tmp_path / "stage2.yaml"
    config = _valid_config()
    config["repetitions"] = 9
    config["backends"] = config["backends"][:1]
    path.write_text(__import__("yaml").safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="at least 10 repetitions"):
        load_config(path)


def test_load_config_rejects_duplicate_workloads_and_extra_strategy_labels(tmp_path) -> None:
    path = tmp_path / "stage2.yaml"
    config = _valid_config()
    config["workloads"] = ["random", "random"]
    config["targets"].append({"strategy": "renamed-static", "url": "http://127.0.0.1:18104"})
    path.write_text(__import__("yaml").safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly"):
        load_config(path)


def test_load_config_requires_all_four_workloads(tmp_path) -> None:
    path = tmp_path / "stage2.yaml"
    config = _valid_config()
    config["workloads"] = ["random"]
    path.write_text(__import__("yaml").safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="workloads must contain exactly"):
        load_config(path)


def test_workloads_preserve_controls_and_shared_context() -> None:
    random_a = build_workload_messages("random", request_index=0, seed=7)
    random_b = build_workload_messages("random", request_index=1, seed=7)
    repeated_a = build_workload_messages("repeated-system", request_index=0, seed=7)
    repeated_b = build_workload_messages("repeated-system", request_index=1, seed=7)
    long_a = build_workload_messages("long-shared-prefix", request_index=0, seed=7)
    long_b = build_workload_messages("long-shared-prefix", request_index=1, seed=7)
    multi_turn = build_workload_messages("tenant-affinity", request_index=2, seed=7)

    assert random_a != random_b
    assert repeated_a[0] == repeated_b[0]
    assert long_a[0]["content"] == long_b[0]["content"]
    assert len(long_a[0]["content"]) > len(repeated_a[0]["content"])
    assert [message["role"] for message in multi_turn] == ["system", "user", "assistant", "user"]


def test_summarize_samples_reports_routing_and_fallbacks() -> None:
    samples = [
        {"ok": True, "latency_ms": 100.0, "ttft_ms": 20.0, "backend": "gpu-0", "fallback": False},
        {"ok": True, "latency_ms": 120.0, "ttft_ms": 30.0, "backend": "gpu-0", "fallback": False},
        {"ok": True, "latency_ms": 140.0, "ttft_ms": 40.0, "backend": "gpu-1", "fallback": True},
        {"ok": False, "latency_ms": 160.0, "ttft_ms": None, "backend": "", "fallback": False},
    ]

    summary = summarize_samples(samples, elapsed_s=2.0)

    assert summary["ok"] == 3
    assert summary["error_rate"] == 0.25
    assert summary["throughput_req_s"] == 1.5
    assert summary["ttft_ms"]["p50"] == 30.0
    assert summary["ttft_ms"]["p95"] == 39.0
    assert summary["ttft_ms"]["p99"] == pytest.approx(39.8)
    assert summary["routing_distribution"] == {"gpu-0": 2, "gpu-1": 1}
    assert summary["route_switches"] == 1
    assert summary["fallback_count"] == 1


def test_vllm_window_uses_counter_deltas_and_after_gauges() -> None:
    before = {
        "gpu-0": {"vllm:prefix_cache_hits_total": 10.0, "vllm:prefix_cache_queries_total": 20.0},
        "gpu-1": {"vllm:prefix_cache_hits_total": 4.0, "vllm:prefix_cache_queries_total": 10.0},
    }
    after = {
        "gpu-0": {
            "vllm:prefix_cache_hits_total": 18.0,
            "vllm:prefix_cache_queries_total": 30.0,
            "vllm:kv_cache_usage_perc": 0.70,
            "vllm:num_requests_waiting": 2.0,
        },
        "gpu-1": {
            "vllm:prefix_cache_hits_total": 7.0,
            "vllm:prefix_cache_queries_total": 15.0,
            "vllm:kv_cache_usage_perc": 0.50,
            "vllm:num_requests_waiting": 0.0,
        },
    }

    window = summarize_vllm_window(before, after)

    assert window["prefix_hits_delta"] == 11.0
    assert window["prefix_queries_delta"] == 15.0
    assert window["kv_reuse_rate"] == 11 / 15
    assert window["gpu_kv_utilization_mean"] == 0.6
    assert window["queue_depth_mean"] == 1.0
    assert window["metric_quality"] == "ok"


def test_vllm_window_marks_counter_reset_instead_of_fabricating_zero_delta() -> None:
    before = {"gpu-0": {"vllm:prefix_cache_hits_total": 10.0, "vllm:prefix_cache_queries_total": 20.0}}
    after = {"gpu-0": {"vllm:prefix_cache_hits_total": 2.0, "vllm:prefix_cache_queries_total": 4.0}}

    window = summarize_vllm_window(before, after)

    assert window["kv_reuse_rate"] is None
    assert window["metric_quality"] == "counter_reset"


def test_aggregate_and_report_keep_claim_boundary_explicit() -> None:
    repetitions = [
        {
            "summary": {
                "throughput_req_s": 10.0,
                "error_rate": 0.0,
                "latency_ms": {"p50": 100.0, "p95": 120.0, "p99": 130.0},
                "ttft_ms": {"p50": 20.0, "p95": 30.0, "p99": 35.0},
                "route_switches": 1,
                "fallback_count": 0,
                "routing_distribution": {"gpu-0": 2, "gpu-1": 2},
            },
            "backend_window": {"kv_reuse_rate": 0.5, "gpu_kv_utilization_mean": 0.4, "queue_depth_mean": 1.0, "metric_quality": "ok"},
        },
        {
            "summary": {
                "throughput_req_s": 12.0,
                "error_rate": 0.0,
                "latency_ms": {"p50": 90.0, "p95": 110.0, "p99": 125.0},
                "ttft_ms": {"p50": 18.0, "p95": 28.0, "p99": 32.0},
                "route_switches": 2,
                "fallback_count": 1,
                "routing_distribution": {"gpu-0": 1, "gpu-1": 3},
            },
            "backend_window": {"kv_reuse_rate": 0.7, "gpu_kv_utilization_mean": 0.6, "queue_depth_mean": 2.0, "metric_quality": "ok"},
        },
    ]

    aggregate = aggregate_repetitions(repetitions)
    report = {
        "schema_version": "kavora-stage2-evaluation/v2",
        "status": "real_backend_measurement",
        "config_hash": "abc",
        "manifest": {
            "model": "qwen-local",
            "model_revision": "snapshot-abc",
            "backend_version": "vllm-1.2.3",
            "repetitions": 10,
            "backends": ["gpu-0", "gpu-1"],
            "gpu": "test-gpu",
        },
        "results": [{"strategy": "static", "workload": "random", "aggregate": aggregate}],
        "comparisons": [],
        "claim_boundary": "Measurements describe this manifest only; no general performance claim is made.",
    }

    markdown = render(report)

    assert aggregate["throughput_req_s"]["mean"] == 11.0
    assert aggregate["kv_reuse_rate"]["mean"] == 0.6
    assert aggregate["fallback_count"] == 1
    assert "## Claim Boundary" in markdown
    assert "Measurements describe this manifest only" in markdown
    assert "TTFT p95" in markdown


def test_balanced_target_order_rotates_every_strategy_through_each_position() -> None:
    targets = tuple(
        TargetSpec(name, f"http://{name}.test")
        for name in ["direct", "static", "load-aware", "kv-aware-shadow", "kv-aware-enforced"]
    )

    schedules = [balanced_target_order(targets, repetition) for repetition in range(10)]

    for position in range(5):
        counts = {target.strategy: 0 for target in targets}
        for schedule in schedules:
            counts[schedule[position].strategy] += 1
        assert set(counts.values()) == {2}
