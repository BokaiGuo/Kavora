from __future__ import annotations

import logging

import httpx

from exporter.adapters.vllm import parse_prometheus_text
from exporter.prometheus_parse import parse_prometheus_text_relaxed_lines
from exporter.schemas import NativeSnapshot

logger = logging.getLogger(__name__)


def _parse_metrics_body(text: str) -> dict[str, float]:
    try:
        return parse_prometheus_text(text)
    except Exception:
        logger.exception("prometheus_client parse failed; falling back to relaxed line parser")
        return parse_prometheus_text_relaxed_lines(text)


def _pick_metric(metrics: dict[str, float], candidates: list[str], default: float = 0.0) -> float:
    for name in candidates:
        if name in metrics:
            return float(metrics[name])
    return default


class SGLangAdapter:
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
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.metrics_url)
            resp.raise_for_status()
            m = _parse_metrics_body(resp.text)

        return NativeSnapshot(
            backend="sglang",
            model=self.model_name,
            instance=self.instance_name,
            model_group=self.model_group,
            total_blocks=_pick_metric(m, ["sglang:kv_total_blocks", "sglang_kv_total_blocks"]),
            usage_perc=_pick_metric(m, ["sglang:kv_cache_usage_perc", "sglang_kv_cache_usage_perc"]),
            active_blocks=_pick_metric(m, ["sglang_obs:kv_active_blocks", "sglang_kv_active_blocks"]),
            reusable_cached_blocks=_pick_metric(
                m, ["sglang_obs:kv_reusable_cached_blocks", "sglang_kv_reusable_cached_blocks"]
            ),
            free_uncached_blocks=_pick_metric(
                m, ["sglang_obs:kv_free_uncached_blocks", "sglang_kv_free_uncached_blocks"]
            ),
            duplicate_cached_blocks=_pick_metric(
                m, ["sglang_obs:kv_duplicate_cached_blocks", "sglang_kv_duplicate_cached_blocks"]
            ),
            prefix_hits=_pick_metric(
                m,
                [
                    "sglang:prefix_cache_hits_total",
                    "sglang_prefix_cache_hits_total",
                    "sglang:prefix_cache_hits",
                    "sglang_prefix_cache_hits",
                ],
            ),
            prefix_queries=_pick_metric(
                m,
                [
                    "sglang:prefix_cache_queries_total",
                    "sglang_prefix_cache_queries_total",
                    "sglang:prefix_cache_queries",
                    "sglang_prefix_cache_queries",
                ],
            ),
            extra=m,
        )
