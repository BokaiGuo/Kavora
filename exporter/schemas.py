from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Backend(str, Enum):
    vllm = "vllm"
    sglang = "sglang"


class NativeSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    backend: str = "vllm"
    model: str = "unknown"
    instance: str = "local"
    model_group: str = ""
    total_blocks: float = 0.0
    usage_perc: float = 0.0
    active_blocks: float = 0.0
    reusable_cached_blocks: float = 0.0
    free_uncached_blocks: float = 0.0
    duplicate_cached_blocks: float = 0.0
    prefix_hits: float = 0.0
    prefix_queries: float = 0.0
    extra: dict[str, float] = Field(default_factory=dict)


class DerivedSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    backend: str
    model: str
    instance: str
    model_group: str
    total_blocks: float
    active_blocks: float
    reusable_cached_blocks: float
    free_uncached_blocks: float
    duplicate_cached_blocks: float
    prefix_blocks: float
    hidden_reuse_ready_perc: float
    effective_residency_perc: float
    cold_free_perc: float
    cache_hit_ratio: float | None = None
