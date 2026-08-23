from __future__ import annotations

import asyncio
import time
from typing import Any

from benchmark.collect import fetch_backend_snapshot, fetch_exporter_snapshot
from benchmark.runner import run_custom_http
from benchmark.window_metrics import (
    derive_window_metrics,
    get_entry_hit_ratio_and_quality,
    make_metric_snapshot,
    make_run_entry,
    summarize_entry_quality,
    summarize_runs_quality,
)

RUN_ENTRY_SCHEMA_VERSION = 3
EXPERIMENT_SCHEMA_VERSION = 3


def scenario_config(name: str) -> dict[str, Any]:
    if name == "high_reuse":
        return {
            "shared_prefix_ratio": 0.95,
            "shared_prefix_len": 256,
            "unique_suffix_len": 64,
        }
    return {
        "shared_prefix_ratio": 0.0,
        "shared_prefix_len": 0,
        "unique_suffix_len": 256,
    }


def aggregate_run_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {"repeats": 0, "quality_summary": summarize_runs_quality(entries)}
    req_s = [float(e["summary"]["throughput"]["req_s"]) for e in entries]
    e2e = [float(e["summary"]["latency"]["e2e_latency_p95_ms"]) for e in entries]
    hit_values: list[float] = []
    for entry in entries:
        hit_ratio, metric_quality = get_entry_hit_ratio_and_quality(entry)
        if hit_ratio is not None and metric_quality == "ok":
            hit_values.append(float(hit_ratio))
    hidden = [float(e.get("exporter_metrics", {}).get("kvcache_kv_hidden_reuse_ready_perc", 0.0)) for e in entries]
    goodput_values = [
        float(e["summary"].get("goodput_req_s"))
        for e in entries
        if e["summary"].get("goodput_req_s") is not None
    ]
    gpu_seconds = [
        float(e.get("resource", {}).get("gpu_seconds"))
        for e in entries
        if e.get("resource", {}).get("gpu_seconds") is not None
    ]
    quality_summary = summarize_runs_quality(entries)
    return {
        "repeats": len(entries),
        "req_s_mean": sum(req_s) / len(req_s),
        "e2e_p95_ms_mean": sum(e2e) / len(e2e),
        "hit_ratio_mean": (sum(hit_values) / len(hit_values)) if hit_values else None,
        "hidden_reuse_mean": sum(hidden) / len(hidden),
        "goodput_req_s_mean": sum(goodput_values) / len(goodput_values) if goodput_values else None,
        "gpu_seconds_mean": sum(gpu_seconds) / len(gpu_seconds) if gpu_seconds else None,
        "quality_summary": quality_summary,
        "hit_ratio_missing_count": int(quality_summary["num_runs_missing_hit_ratio"]),
        "hit_ratio_stale_count": int(quality_summary["num_runs_stale_metrics"]),
    }


def run_windowed_experiment(
    *,
    repeat: int,
    base_url: str,
    endpoint: str,
    model: str,
    backend_metrics_url: str,
    exporter_metrics_url: str,
    num_requests: int,
    concurrency: int,
    base_seed: int,
    input_len: int,
    output_len: int,
    warmup_requests: int,
    timeout_s: float,
    shared_prefix_ratio: float,
    shared_prefix_len: int,
    unique_suffix_len: int,
    point_label: str = "",
) -> dict[str, Any]:
    start_ts = time.time()
    backend_before_error = ""
    backend_after_error = ""

    try:
        backend_before = fetch_backend_snapshot(backend_metrics_url)
    except Exception as exc:
        backend_before_error = str(exc)
        backend_before = make_metric_snapshot({}, ts=time.time(), error=backend_before_error)
    try:
        exporter_before = fetch_exporter_snapshot(exporter_metrics_url)
    except Exception as exc:
        exporter_before = make_metric_snapshot({}, ts=time.time(), error=str(exc))

    summary = asyncio.run(
        run_custom_http(
            base_url=base_url,
            endpoint=endpoint,
            model=model,
            num_requests=num_requests,
            concurrency=concurrency,
            base_seed=base_seed,
            input_len=input_len,
            output_len=output_len,
            timeout_s=timeout_s,
            shared_prefix_ratio=shared_prefix_ratio,
            shared_prefix_len=shared_prefix_len,
            unique_suffix_len=unique_suffix_len,
            warmup_requests=warmup_requests,
        )
    )
    timing = summary.get("timing", {})
    warmup_end_ts = timing.get("warmup_end_ts")
    end_ts = float(timing.get("run_finished_ts", time.time()))

    try:
        backend_after = fetch_backend_snapshot(backend_metrics_url)
    except Exception as exc:
        backend_after_error = str(exc)
        backend_after = make_metric_snapshot({}, ts=time.time(), error=backend_after_error)
    try:
        exporter_after = fetch_exporter_snapshot(exporter_metrics_url)
        exporter_metrics = dict(exporter_after["metrics"])  # type: ignore[arg-type]
    except Exception as exc:
        exporter_after = make_metric_snapshot({}, ts=time.time(), error=str(exc))
        exporter_metrics = {}

    entry = make_run_entry(
        repeat=repeat,
        summary=summary,
        exporter_metrics=exporter_metrics,
        start_ts=start_ts,
        warmup_end_ts=float(warmup_end_ts) if warmup_end_ts is not None else None,
        end_ts=end_ts,
    )
    entry["schema_version"] = RUN_ENTRY_SCHEMA_VERSION
    if point_label:
        entry["point_label"] = point_label
    entry["metric_snapshots"]["backend_before"] = backend_before
    entry["metric_snapshots"]["backend_after"] = backend_after
    entry["metric_snapshots"]["exporter_before"] = exporter_before
    entry["metric_snapshots"]["exporter_after"] = exporter_after
    window_metrics = derive_window_metrics(backend_before, backend_after, metric_source="backend")
    window_metrics["metrics_missing"] = bool(backend_before_error or backend_after_error) or bool(
        window_metrics["metrics_missing"]
    )
    # Current run-local hit ratio is derived from backend before/after snapshots,
    # so exporter freshness should not invalidate a valid backend window.
    window_metrics["metrics_stale"] = False
    entry["derived_window_metrics"] = window_metrics
    entry["quality"] = summarize_entry_quality(entry)
    return entry
