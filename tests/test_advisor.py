from exporter.advisor import build_advice
from exporter.schemas import DerivedSnapshot


def snap(**overrides):
    values = dict(backend="vllm", model="m", instance="i", model_group="", total_blocks=100, active_blocks=20, reusable_cached_blocks=40, free_uncached_blocks=40, duplicate_cached_blocks=0, prefix_blocks=40, hidden_reuse_ready_perc=.4, effective_residency_perc=.6, cold_free_perc=.4, cache_hit_ratio=None)
    values.update(overrides)
    return DerivedSnapshot(**values)


def test_advice_preserves_missing_signal_semantics():
    result = build_advice(snap())
    assert any(item["code"] == "cache_hit_ratio_missing" for item in result["recommendations"])


def test_advice_reports_pressure():
    result = build_advice(snap(effective_residency_perc=.95))
    assert any(item["code"] == "kv_pressure_high" for item in result["recommendations"])


def test_advice_blocks_promotion_on_fallback_evidence():
    result = build_advice(snap(cache_hit_ratio=.6, prefix_evidence_quality="fallback"))
    assert any(item["code"] == "cache_evidence_not_strict" for item in result["recommendations"])
