from __future__ import annotations

import logging

import httpx

from exporter.prometheus_parse import aggregate_prometheus_text, parse_prometheus_text_relaxed_lines
from exporter.schemas import NativeSnapshot

logger = logging.getLogger(__name__)

# Re-export for ``exporter.adapters.sglang`` and tests.
parse_prometheus_text = aggregate_prometheus_text


def _parse_metrics_body(text: str) -> dict[str, float]:
    try:
        return aggregate_prometheus_text(text)
    except Exception:
        logger.exception("prometheus_client parse failed; falling back to relaxed line parser")
        return parse_prometheus_text_relaxed_lines(text)


def _pick_metric_with_name(metrics: dict[str, float], *keys: str, default: float = 0.0) -> tuple[float, str]:
    for key in keys:
        if key in metrics:
            return float(metrics[key]), key
    return default, ""


def _prefix_metric_metadata(hits_metric_name: str, queries_metric_name: str) -> tuple[str, str, str, str]:
    if hits_metric_name and queries_metric_name:
        return "prefix_query_counters", "strict", "queries", "strict"
    if hits_metric_name or queries_metric_name:
        return "partial", "missing", "mixed", "missing"
    return "missing", "missing", "missing", "missing"


class VllmAdapter:
    def __init__(
        self,
        metrics_url: str,
        model_name: str = "unknown",
        instance_name: str = "local",
        model_group: str = "",
    ) -> None:
        self.metrics_url = metrics_url
        self.model_name = model_name
        self.instance_name = instance_name
        self.model_group = model_group

    async def collect(self) -> NativeSnapshot:
        # Local backend metrics scrapes should not inherit proxy settings.
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(self.metrics_url)
            resp.raise_for_status()
            m = _parse_metrics_body(resp.text)

        def _pick(*keys: str, default: float = 0.0) -> float:
            for k in keys:
                if k in m:
                    return float(m[k])
            return default

        def _pick_optional(*keys: str) -> float | None:
            for key in keys:
                if key in m:
                    return float(m[key])
            return None

        prefix_hits, prefix_hits_metric_name = _pick_metric_with_name(
            m, "vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits"
        )
        prefix_queries, prefix_queries_metric_name = _pick_metric_with_name(
            m, "vllm:prefix_cache_queries_total", "vllm:prefix_cache_queries"
        )
        prefix_metric_semantics, prefix_metric_comparability, prefix_metric_basis, prefix_evidence_quality = _prefix_metric_metadata(
            prefix_hits_metric_name, prefix_queries_metric_name
        )

        block_metric_names = {
            "vllm_obs:kv_total_blocks",
            "vllm_obs:kv_active_blocks",
            "vllm_obs:kv_reusable_cached_blocks",
            "vllm_obs:kv_free_uncached_blocks",
        }
        if block_metric_names.issubset(m):
            block_evidence_quality = "strict"
        elif "vllm:kv_cache_usage_perc" in m:
            block_evidence_quality = "estimated"
        else:
            block_evidence_quality = "missing"

        return NativeSnapshot(
            backend="vllm",
            model=self.model_name,
            instance=self.instance_name,
            model_group=self.model_group,
            total_blocks=_pick("vllm_obs:kv_total_blocks"),
            usage_perc=_pick("vllm:kv_cache_usage_perc"),
            active_blocks=_pick("vllm_obs:kv_active_blocks"),
            reusable_cached_blocks=_pick("vllm_obs:kv_reusable_cached_blocks"),
            free_uncached_blocks=_pick("vllm_obs:kv_free_uncached_blocks"),
            duplicate_cached_blocks=_pick("vllm_obs:kv_duplicate_cached_blocks"),
            prefix_hits=prefix_hits,
            prefix_queries=prefix_queries,
            prefix_hits_metric_name=prefix_hits_metric_name,
            prefix_queries_metric_name=prefix_queries_metric_name,
            prefix_metric_semantics=prefix_metric_semantics,
            prefix_metric_comparability=prefix_metric_comparability,
            prefix_metric_basis=prefix_metric_basis,
            prefix_evidence_quality=prefix_evidence_quality,
            block_evidence_quality=block_evidence_quality,
            queue_depth=_pick_optional("vllm:num_requests_waiting"),
            running_requests=_pick_optional("vllm:num_requests_running"),
            extra=m,
        )
