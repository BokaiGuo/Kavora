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


def _pick_metric_optional(metrics: dict[str, float], candidates: list[str]) -> float | None:
    for name in candidates:
        if name in metrics:
            return float(metrics[name])
    return None


def _pick_metric_with_name(metrics: dict[str, float], candidates: list[str], default: float = 0.0) -> tuple[float, str]:
    for name in candidates:
        if name in metrics:
            return float(metrics[name]), name
    return default, ""


def _prefix_metric_metadata(hits_metric_name: str, queries_metric_name: str) -> tuple[str, str, str]:
    token_fallback_hits = {"sglang:cached_tokens_total", "sglang_cached_tokens_total"}
    token_fallback_queries = {"sglang:prompt_tokens_total", "sglang_prompt_tokens_total"}
    if hits_metric_name and queries_metric_name:
        if hits_metric_name in token_fallback_hits and queries_metric_name in token_fallback_queries:
            return "token_counter_fallback", "directional", "tokens"
        return "prefix_query_counters", "strict", "queries"
    if hits_metric_name or queries_metric_name:
        return "partial", "missing", "mixed"
    return "missing", "missing", "missing"


class SGLangAdapter:
    def __init__(
        self,
        metrics_url: str,
        model_name: str = "unknown",
        instance_name: str = "local",
        model_group: str = "",
        tokens_per_block: int = 16,
    ) -> None:
        self.metrics_url = metrics_url
        self.model_name = model_name
        self.instance_name = instance_name
        self.model_group = model_group
        self.tokens_per_block = max(1, int(tokens_per_block))

    def _tokens_to_blocks(self, value: float) -> float:
        return float(value) / float(self.tokens_per_block)

    async def collect(self) -> NativeSnapshot:
        # Local backend metrics scrapes should not inherit proxy settings.
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(self.metrics_url)
            resp.raise_for_status()
            m = _parse_metrics_body(resp.text)

        total_blocks = _pick_metric(m, ["sglang:kv_total_blocks", "sglang_kv_total_blocks"])
        if total_blocks == 0.0:
            total_tokens = _pick_metric(m, ["sglang:max_total_num_tokens", "sglang_max_total_num_tokens"])
            if total_tokens > 0.0:
                total_blocks = self._tokens_to_blocks(total_tokens)
        usage_perc = _pick_metric(
            m,
            [
                "sglang:kv_cache_usage_perc",
                "sglang_kv_cache_usage_perc",
                "sglang:token_usage",
                "sglang_token_usage",
            ],
        )
        active_blocks = _pick_metric(m, ["sglang_obs:kv_active_blocks", "sglang_kv_active_blocks"])
        if active_blocks == 0.0:
            active_tokens = _pick_metric(m, ["sglang:num_used_tokens", "sglang_num_used_tokens"])
            if active_tokens > 0.0:
                active_blocks = self._tokens_to_blocks(active_tokens)
        reusable_cached_blocks = _pick_metric(
            m, ["sglang_obs:kv_reusable_cached_blocks", "sglang_kv_reusable_cached_blocks"]
        )
        free_uncached_blocks = _pick_metric(
            m, ["sglang_obs:kv_free_uncached_blocks", "sglang_kv_free_uncached_blocks"]
        )
        if free_uncached_blocks == 0.0 and total_blocks > 0.0 and active_blocks > 0.0:
            free_uncached_blocks = max(0.0, total_blocks - active_blocks)
        prefix_hits, prefix_hits_metric_name = _pick_metric_with_name(
            m,
            [
                "sglang:prefix_cache_hits_total",
                "sglang_prefix_cache_hits_total",
                "sglang:prefix_cache_hits",
                "sglang_prefix_cache_hits",
                "sglang:cached_tokens_total",
                "sglang_cached_tokens_total",
            ],
        )
        prefix_queries, prefix_queries_metric_name = _pick_metric_with_name(
            m,
            [
                "sglang:prefix_cache_queries_total",
                "sglang_prefix_cache_queries_total",
                "sglang:prefix_cache_queries",
                "sglang_prefix_cache_queries",
                "sglang:prompt_tokens_total",
                "sglang_prompt_tokens_total",
            ],
        )
        prefix_metric_semantics, prefix_metric_comparability, prefix_metric_basis = _prefix_metric_metadata(
            prefix_hits_metric_name, prefix_queries_metric_name
        )

        return NativeSnapshot(
            backend="sglang",
            model=self.model_name,
            instance=self.instance_name,
            model_group=self.model_group,
            total_blocks=total_blocks,
            usage_perc=usage_perc,
            active_blocks=active_blocks,
            reusable_cached_blocks=reusable_cached_blocks,
            free_uncached_blocks=free_uncached_blocks,
            duplicate_cached_blocks=_pick_metric(
                m, ["sglang_obs:kv_duplicate_cached_blocks", "sglang_kv_duplicate_cached_blocks"]
            ),
            prefix_hits=prefix_hits,
            prefix_queries=prefix_queries,
            prefix_hits_metric_name=prefix_hits_metric_name,
            prefix_queries_metric_name=prefix_queries_metric_name,
            prefix_metric_semantics=prefix_metric_semantics,
            prefix_metric_comparability=prefix_metric_comparability,
            prefix_metric_basis=prefix_metric_basis,
            queue_depth=_pick_metric_optional(
                m, ["sglang:num_queue_reqs", "sglang_num_queue_reqs", "sglang:num_requests_waiting"]
            ),
            running_requests=_pick_metric_optional(
                m, ["sglang:num_running_reqs", "sglang_num_running_reqs", "sglang:num_requests_running"]
            ),
            extra=m,
        )
