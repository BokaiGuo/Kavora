from pathlib import Path
import json

from exporter.backend_state import snapshot_from_derived
from exporter.schemas import DerivedSnapshot


def test_backend_state_golden_preserves_missing_signal() -> None:
    golden = json.loads(Path("proto/testdata/backend_state_golden.json").read_text())
    snapshot = snapshot_from_derived(
        DerivedSnapshot(
            backend="vllm", model="demo-model", instance="local", model_group="demo",
            total_blocks=100, active_blocks=20, reusable_cached_blocks=60,
            free_uncached_blocks=20, duplicate_cached_blocks=2, prefix_blocks=60,
            hidden_reuse_ready_perc=0.6, effective_residency_perc=0.8, cold_free_perc=0.2,
            cache_hit_ratio=None, queue_depth=3, running_requests=2,
        ),
        observed_at_unix_millis=1700000000000,
    )
    assert snapshot["signals"]["cache_hit_ratio"]["has_value"] is False
    assert snapshot["signals"]["cache_hit_ratio"]["quality"] == "missing"
    assert snapshot["signals"]["cache_hit_ratio"]["value"] == 0.0
    assert snapshot["signals"]["queue_depth"]["value"] == 3
    assert snapshot["signals"]["running_requests"]["value"] == 2
    assert snapshot["signals"]["cache_hit_ratio"]["evidence_quality"] == "missing"
    assert snapshot["signals"]["effective_residency_perc"]["evidence_quality"] == "missing"
    for name, signal in golden["signals"].items():
        assert snapshot["signals"][name] == signal


def test_stale_snapshot_keeps_value_but_marks_quality() -> None:
    snapshot = snapshot_from_derived(
        DerivedSnapshot(
            backend="vllm", model="m", instance="i", model_group="",
            total_blocks=1, active_blocks=1, reusable_cached_blocks=0,
            free_uncached_blocks=0, duplicate_cached_blocks=0, prefix_blocks=0,
            hidden_reuse_ready_perc=0, effective_residency_perc=1, cold_free_perc=0,
            cache_hit_ratio=None,
        ),
        observed_at_unix_millis=1,
        stale=True,
    )
    assert snapshot["signals"]["total_blocks"]["quality"] == "stale"


def test_backend_state_uses_explicit_gateway_backend_id() -> None:
    snapshot = snapshot_from_derived(
        DerivedSnapshot(
            backend="vllm", model="m", instance="gpu-0", model_group="",
            total_blocks=1, active_blocks=0, reusable_cached_blocks=0,
            free_uncached_blocks=1, duplicate_cached_blocks=0, prefix_blocks=0,
            hidden_reuse_ready_perc=0, effective_residency_perc=0, cold_free_perc=1,
            cache_hit_ratio=0, queue_depth=0, running_requests=0,
        ),
        backend_id="gpu-0",
    )
    assert snapshot["backend_id"] == "gpu-0"
