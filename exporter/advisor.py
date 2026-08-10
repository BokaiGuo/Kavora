from __future__ import annotations

from typing import Any

from exporter.schemas import DerivedSnapshot


def build_advice(snapshot: DerivedSnapshot, *, stale: bool = False) -> dict[str, Any]:
    recommendations: list[dict[str, Any]] = []
    if stale:
        recommendations.append({"severity": "critical", "code": "metrics_stale", "action": "check_backend_metrics_endpoint", "reason": "backend metrics are stale; keep static routing and do not promote KV-aware decisions"})
    if snapshot.prefix_evidence_quality in {"estimated", "fallback", "missing"}:
        recommendations.append({"severity": "warning", "code": "cache_evidence_not_strict", "action": "keep_recommendations_dry_run_only", "reason": f"cache evidence quality is {snapshot.prefix_evidence_quality}; do not auto-promote a calibrated policy"})
    if snapshot.cache_hit_ratio is None:
        recommendations.append({"severity": "info", "code": "cache_hit_ratio_missing", "action": "enable_comparable_prefix_counters", "reason": "cache hit ratio is unavailable and must not be inferred from zero"})
    elif snapshot.cache_hit_ratio < 0.2:
        recommendations.append({"severity": "warning", "code": "low_prefix_reuse", "action": "increase_shared_prefix_or_review_prompt_stability", "reason": f"cache hit ratio is {snapshot.cache_hit_ratio:.3f}"})
    if snapshot.cold_free_perc > 0.35:
        recommendations.append({"severity": "info", "code": "cold_capacity_available", "action": "consider_higher_concurrency_or_batch_size", "reason": f"cold free ratio is {snapshot.cold_free_perc:.3f}"})
    if snapshot.effective_residency_perc > 0.9:
        recommendations.append({"severity": "warning", "code": "kv_pressure_high", "action": "reduce_concurrency_or_increase_gpu_memory", "reason": f"effective residency is {snapshot.effective_residency_perc:.3f}"})
    if snapshot.duplicate_cached_blocks > max(snapshot.total_blocks * 0.1, 1):
        recommendations.append({"severity": "warning", "code": "duplicate_cache_pressure", "action": "review_prefix_normalization_and_tenant_isolation", "reason": f"duplicate cached blocks are {snapshot.duplicate_cached_blocks:.1f}"})
    if not recommendations:
        recommendations.append({"severity": "ok", "code": "healthy", "action": "continue_observing", "reason": "no immediate tuning action is indicated"})
    return {"schema_version": "kavora-advice/v1", "backend": snapshot.backend, "model": snapshot.model, "instance": snapshot.instance, "observed_at_unix_millis": 0, "recommendations": recommendations}
