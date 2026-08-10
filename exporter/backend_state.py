from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from exporter.schemas import DerivedSnapshot

SCHEMA_VERSION = "kavora.backend-state/v1"


def _quality(value: float | None, *, stale: bool = False) -> str:
    if value is None:
        return "missing"
    if stale:
        return "stale"
    return "fresh"


def _signal(
    value: float | None,
    *,
    source: str,
    observed_at_unix_millis: int,
    semantics: str,
    evidence_quality: str,
    stale: bool = False,
) -> dict[str, Any]:
    return {
        "value": value if value is not None else 0.0,
        "has_value": value is not None,
        "quality": _quality(value, stale=stale),
        "source": source or "derived",
        "observed_at_unix_millis": observed_at_unix_millis,
        "semantics": semantics,
        "evidence_quality": evidence_quality,
    }


def snapshot_from_derived(
    snap: DerivedSnapshot,
    *,
    backend_id: str | None = None,
    observed_at_unix_millis: int | None = None,
    stale: bool = False,
) -> dict[str, Any]:
    observed = observed_at_unix_millis or int(time.time() * 1000)
    backend_key = backend_id or f"{snap.backend}:{snap.instance}"
    prefix_source = snap.prefix_hits_metric_name or snap.prefix_queries_metric_name or "derived"
    signals = {
        "total_blocks": _signal(snap.total_blocks, source="derived.total_blocks", observed_at_unix_millis=observed, semantics="gauge", evidence_quality=snap.block_evidence_quality, stale=stale),
        "active_blocks": _signal(snap.active_blocks, source="derived.active_blocks", observed_at_unix_millis=observed, semantics="gauge", evidence_quality=snap.block_evidence_quality, stale=stale),
        "reusable_cached_blocks": _signal(snap.reusable_cached_blocks, source="derived.reusable_cached_blocks", observed_at_unix_millis=observed, semantics="gauge", evidence_quality=snap.block_evidence_quality, stale=stale),
        "free_uncached_blocks": _signal(snap.free_uncached_blocks, source="derived.free_uncached_blocks", observed_at_unix_millis=observed, semantics="gauge", evidence_quality=snap.block_evidence_quality, stale=stale),
        "duplicate_cached_blocks": _signal(snap.duplicate_cached_blocks, source="derived.duplicate_cached_blocks", observed_at_unix_millis=observed, semantics="gauge", evidence_quality=snap.block_evidence_quality, stale=stale),
        "hidden_reuse_ready_perc": _signal(snap.hidden_reuse_ready_perc, source="derived.hidden_reuse_ready_perc", observed_at_unix_millis=observed, semantics="ratio", evidence_quality=snap.block_evidence_quality, stale=stale),
        "effective_residency_perc": _signal(snap.effective_residency_perc, source="derived.effective_residency_perc", observed_at_unix_millis=observed, semantics="ratio", evidence_quality=snap.block_evidence_quality, stale=stale),
        "cold_free_perc": _signal(snap.cold_free_perc, source="derived.cold_free_perc", observed_at_unix_millis=observed, semantics="ratio", evidence_quality=snap.block_evidence_quality, stale=stale),
        "cache_hit_ratio": _signal(snap.cache_hit_ratio, source=prefix_source, observed_at_unix_millis=observed, semantics=snap.prefix_metric_semantics, evidence_quality=snap.prefix_evidence_quality, stale=stale),
        "queue_depth": _signal(snap.queue_depth, source="backend.queue_depth", observed_at_unix_millis=observed, semantics="gauge", evidence_quality="strict" if snap.queue_depth is not None else "missing", stale=stale),
        "running_requests": _signal(snap.running_requests, source="backend.running_requests", observed_at_unix_millis=observed, semantics="gauge", evidence_quality="strict" if snap.running_requests is not None else "missing", stale=stale),
    }
    body = {
        "schema_version": SCHEMA_VERSION,
        "backend_id": backend_key,
        "backend": snap.backend,
        "model": snap.model,
        "instance": snap.instance,
        "model_group": snap.model_group,
        "observed_at_unix_millis": observed,
        "signals": signals,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["snapshot_hash"] = hashlib.sha256(canonical).hexdigest()
    return body


def snapshot_json(snap: DerivedSnapshot, **kwargs: Any) -> str:
    return json.dumps(snapshot_from_derived(snap, **kwargs), sort_keys=True, indent=2) + "\n"
