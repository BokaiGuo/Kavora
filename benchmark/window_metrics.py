from __future__ import annotations

import math
import time
from typing import Any, Mapping, Sequence

DEFAULT_PREFIX_HITS_KEYS = (
    "vllm:prefix_cache_hits_total",
    "vllm:prefix_cache_hits",
    "sglang:prefix_cache_hits_total",
    "sglang_prefix_cache_hits_total",
    "sglang:prefix_cache_hits",
    "sglang_prefix_cache_hits",
    "sglang:cached_tokens_total",
    "sglang_cached_tokens_total",
)

DEFAULT_PREFIX_QUERIES_KEYS = (
    "vllm:prefix_cache_queries_total",
    "vllm:prefix_cache_queries",
    "sglang:prefix_cache_queries_total",
    "sglang_prefix_cache_queries_total",
    "sglang:prefix_cache_queries",
    "sglang_prefix_cache_queries",
    "sglang:prompt_tokens_total",
    "sglang_prompt_tokens_total",
)

EXPORTER_LAST_SUCCESS_KEYS = (
    "kvcache_exporter_scrape_last_success_timestamp_seconds",
)


def empty_window() -> dict[str, float | None]:
    return {
        "start_ts": None,
        "warmup_end_ts": None,
        "end_ts": None,
    }


def empty_metric_snapshots() -> dict[str, dict[str, Any] | None]:
    return {
        "backend_before": None,
        "backend_after": None,
        "exporter_before": None,
        "exporter_after": None,
    }


def empty_derived_window_metrics(*, metric_source: str = "backend") -> dict[str, Any]:
    return {
        "prefix_hits_delta": None,
        "prefix_queries_delta": None,
        "cache_hit_ratio_window": None,
        "metrics_missing": False,
        "metrics_stale": False,
        "metric_source": metric_source,
    }


def make_metric_snapshot(
    metrics: Mapping[str, float] | None = None,
    *,
    ts: float | None = None,
    error: str = "",
) -> dict[str, Any]:
    snapshot_metrics = {str(k): float(v) for k, v in (metrics or {}).items()}
    return {
        "ts": float(ts if ts is not None else time.time()),
        "metrics": snapshot_metrics,
        "error": error,
    }


def make_run_entry(
    *,
    repeat: int,
    summary: Mapping[str, Any] | None = None,
    exporter_metrics: Mapping[str, float] | None = None,
    start_ts: float | None = None,
    warmup_end_ts: float | None = None,
    end_ts: float | None = None,
    metric_source: str = "backend",
) -> dict[str, Any]:
    entry = {
        "repeat": repeat,
        "window": empty_window(),
        "summary": dict(summary or {}),
        "exporter_metrics": dict(exporter_metrics or {}),
        "metric_snapshots": empty_metric_snapshots(),
        "derived_window_metrics": empty_derived_window_metrics(metric_source=metric_source),
    }
    if start_ts is not None:
        entry["window"]["start_ts"] = float(start_ts)
    if warmup_end_ts is not None:
        entry["window"]["warmup_end_ts"] = float(warmup_end_ts)
    if end_ts is not None:
        entry["window"]["end_ts"] = float(end_ts)
    return entry


def pick_metric(metrics: Mapping[str, float], candidates: Sequence[str]) -> float | None:
    for name in candidates:
        if name in metrics:
            return float(metrics[name])
    return None


def metric_from_snapshot(snapshot: Mapping[str, Any] | None, candidates: Sequence[str]) -> float | None:
    if not snapshot:
        return None
    metrics = snapshot.get("metrics", {})
    if not isinstance(metrics, Mapping):
        return None
    return pick_metric(metrics, candidates)


def snapshot_has_error(snapshot: Mapping[str, Any] | None) -> bool:
    if not snapshot:
        return False
    error = snapshot.get("error", "")
    return bool(error)


def compute_counter_delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    if after < before:
        return None
    return float(after - before)


def compute_hit_ratio_from_deltas(hits_delta: float | None, queries_delta: float | None) -> float | None:
    if hits_delta is None or queries_delta is None or queries_delta <= 0:
        return None
    return max(0.0, min(1.0, float(hits_delta / queries_delta)))


def _coerce_finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def summarize_metric_quality_counts(
    *,
    total_runs: int,
    num_runs_ok_metrics: int,
    num_runs_stale_metrics: int,
    num_runs_missing_hit_ratio: int,
) -> str:
    if total_runs <= 0:
        return "missing"
    if num_runs_ok_metrics == total_runs:
        return "ok"
    if num_runs_stale_metrics == total_runs:
        return "stale"
    if num_runs_missing_hit_ratio == total_runs:
        return "missing"
    return "mixed"


def summarize_hit_ratio_source_counts(
    *,
    num_runs_window_hit_ratio: int,
    num_runs_snapshot_fallback_hit_ratio: int,
    num_runs_missing_hit_ratio_source: int,
) -> str:
    present = sum(
        int(count > 0)
        for count in (
            num_runs_window_hit_ratio,
            num_runs_snapshot_fallback_hit_ratio,
            num_runs_missing_hit_ratio_source,
        )
    )
    if present > 1:
        return "mixed"
    if num_runs_window_hit_ratio > 0:
        return "window"
    if num_runs_snapshot_fallback_hit_ratio > 0:
        return "snapshot_fallback"
    return "missing"


def get_entry_prefix_metric_check(entry: Mapping[str, Any]) -> str:
    exporter_metrics = entry.get("exporter_metrics", {})
    if not isinstance(exporter_metrics, Mapping):
        return "missing"

    comparable_present = "kvcache_exporter_prefix_metric_comparable" in exporter_metrics
    token_fallback_present = "kvcache_exporter_prefix_metric_token_fallback" in exporter_metrics
    comparable_value = _coerce_finite_float(exporter_metrics.get("kvcache_exporter_prefix_metric_comparable"))
    token_fallback_value = _coerce_finite_float(exporter_metrics.get("kvcache_exporter_prefix_metric_token_fallback"))

    if comparable_present and comparable_value is not None and comparable_value >= 0.5:
        return "strict"
    if token_fallback_present and token_fallback_value is not None and token_fallback_value >= 0.5:
        return "token_fallback"
    if comparable_present or token_fallback_present:
        return "other"
    return "missing"


def summarize_prefix_metric_checks(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {
        "num_runs_prefix_metric_strict": 0,
        "num_runs_prefix_metric_token_fallback": 0,
        "num_runs_prefix_metric_other": 0,
        "num_runs_prefix_metric_missing": 0,
    }
    for entry in entries:
        check = get_entry_prefix_metric_check(entry)
        if check == "strict":
            counts["num_runs_prefix_metric_strict"] += 1
        elif check == "token_fallback":
            counts["num_runs_prefix_metric_token_fallback"] += 1
        elif check == "other":
            counts["num_runs_prefix_metric_other"] += 1
        else:
            counts["num_runs_prefix_metric_missing"] += 1

    total_runs = len(entries)
    if total_runs <= 0 or counts["num_runs_prefix_metric_missing"] == total_runs:
        summary = "missing"
    elif counts["num_runs_prefix_metric_strict"] == total_runs:
        summary = "strict"
    elif counts["num_runs_prefix_metric_token_fallback"] == total_runs:
        summary = "token_fallback"
    elif counts["num_runs_prefix_metric_other"] == total_runs:
        summary = "other"
    else:
        summary = "mixed"

    counts["prefix_metric_check"] = summary
    counts["prefix_metric_strict_run_fraction"] = (
        float(counts["num_runs_prefix_metric_strict"]) / float(total_runs) if total_runs > 0 else 0.0
    )
    counts["prefix_metric_token_fallback_run_fraction"] = (
        float(counts["num_runs_prefix_metric_token_fallback"]) / float(total_runs) if total_runs > 0 else 0.0
    )
    return counts


def derive_window_metrics(
    before_snapshot: Mapping[str, Any] | None,
    after_snapshot: Mapping[str, Any] | None,
    *,
    prefix_hits_keys: Sequence[str] = DEFAULT_PREFIX_HITS_KEYS,
    prefix_queries_keys: Sequence[str] = DEFAULT_PREFIX_QUERIES_KEYS,
    metric_source: str = "backend",
) -> dict[str, Any]:
    hits_before = metric_from_snapshot(before_snapshot, prefix_hits_keys)
    hits_after = metric_from_snapshot(after_snapshot, prefix_hits_keys)
    queries_before = metric_from_snapshot(before_snapshot, prefix_queries_keys)
    queries_after = metric_from_snapshot(after_snapshot, prefix_queries_keys)

    if hits_before is None and hits_after is not None and not snapshot_has_error(before_snapshot):
        hits_before = 0.0
    if queries_before is None and queries_after is not None and not snapshot_has_error(before_snapshot):
        queries_before = 0.0

    hits_delta = compute_counter_delta(hits_before, hits_after)
    queries_delta = compute_counter_delta(queries_before, queries_after)
    cache_hit_ratio_window = compute_hit_ratio_from_deltas(hits_delta, queries_delta)

    out = empty_derived_window_metrics(metric_source=metric_source)
    out["prefix_hits_delta"] = hits_delta
    out["prefix_queries_delta"] = queries_delta
    out["cache_hit_ratio_window"] = cache_hit_ratio_window
    out["metrics_missing"] = bool(
        any(v is None for v in (hits_before, hits_after, queries_before, queries_after))
        or cache_hit_ratio_window is None
    )
    return out


def snapshot_is_stale(
    snapshot: Mapping[str, Any] | None,
    *,
    reference_ts: float,
    freshness_keys: Sequence[str] = EXPORTER_LAST_SUCCESS_KEYS,
) -> bool:
    last_success = metric_from_snapshot(snapshot, freshness_keys)
    if last_success is None:
        return True
    return float(last_success) < float(reference_ts)


def get_entry_hit_ratio_and_quality(entry: Mapping[str, Any]) -> tuple[float | None, str]:
    value, quality, _ = get_entry_hit_ratio_quality_and_source(entry)
    return value, quality


def get_entry_hit_ratio_quality_and_source(entry: Mapping[str, Any]) -> tuple[float | None, str, str]:
    if "derived_window_metrics" in entry:
        derived = entry.get("derived_window_metrics", {})
        if not isinstance(derived, Mapping):
            return None, "missing", "window"
        value = _coerce_finite_float(derived.get("cache_hit_ratio_window"))
        metrics_missing = bool(derived.get("metrics_missing", False))
        metrics_stale = bool(derived.get("metrics_stale", False))
        if value is not None:
            return value, "stale" if metrics_stale else "ok", "window"
        if metrics_stale:
            return None, "stale", "window"
        return None, "missing", "window"

    exporter_metrics = entry.get("exporter_metrics", {})
    if isinstance(exporter_metrics, Mapping):
        snapshot_value = _coerce_finite_float(exporter_metrics.get("kvcache_kv_cache_hit_ratio"))
        if snapshot_value is not None:
            return snapshot_value, "ok", "snapshot_fallback"
    return None, "missing", "missing"


def summarize_entry_quality(entry: Mapping[str, Any]) -> dict[str, Any]:
    hit_ratio, metric_quality, hit_ratio_source = get_entry_hit_ratio_quality_and_source(entry)
    return {
        "cache_hit_ratio": hit_ratio,
        "metric_quality": metric_quality,
        "hit_ratio_source": hit_ratio_source,
    }


def summarize_runs_quality(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {
        "total_runs": len(entries),
        "num_runs_ok_metrics": 0,
        "num_runs_missing_hit_ratio": 0,
        "num_runs_stale_metrics": 0,
        "num_runs_window_hit_ratio": 0,
        "num_runs_snapshot_fallback_hit_ratio": 0,
        "num_runs_missing_hit_ratio_source": 0,
    }
    for entry in entries:
        _, metric_quality, hit_ratio_source = get_entry_hit_ratio_quality_and_source(entry)
        if metric_quality == "ok":
            counts["num_runs_ok_metrics"] += 1
        elif metric_quality == "stale":
            counts["num_runs_stale_metrics"] += 1
        else:
            counts["num_runs_missing_hit_ratio"] += 1

        if hit_ratio_source == "window":
            counts["num_runs_window_hit_ratio"] += 1
        elif hit_ratio_source == "snapshot_fallback":
            counts["num_runs_snapshot_fallback_hit_ratio"] += 1
        else:
            counts["num_runs_missing_hit_ratio_source"] += 1

    counts["metric_quality"] = summarize_metric_quality_counts(
        total_runs=int(counts["total_runs"]),
        num_runs_ok_metrics=int(counts["num_runs_ok_metrics"]),
        num_runs_stale_metrics=int(counts["num_runs_stale_metrics"]),
        num_runs_missing_hit_ratio=int(counts["num_runs_missing_hit_ratio"]),
    )
    counts["hit_ratio_source"] = summarize_hit_ratio_source_counts(
        num_runs_window_hit_ratio=int(counts["num_runs_window_hit_ratio"]),
        num_runs_snapshot_fallback_hit_ratio=int(counts["num_runs_snapshot_fallback_hit_ratio"]),
        num_runs_missing_hit_ratio_source=int(counts["num_runs_missing_hit_ratio_source"]),
    )
    total_runs = int(counts["total_runs"])
    counts["ok_metric_run_fraction"] = (
        float(counts["num_runs_ok_metrics"]) / float(total_runs) if total_runs > 0 else 0.0
    )
    counts["hit_ratio_comparable"] = bool(
        counts["metric_quality"] == "ok" and counts["hit_ratio_source"] != "mixed"
    )
    counts.update(summarize_prefix_metric_checks(entries))
    return counts
