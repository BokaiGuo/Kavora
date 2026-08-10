from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_pipeline_common_defaults_exporter_ready_url() -> None:
    proc = subprocess.run(
        [
            "bash",
            "-lc",
            "source scripts/lib/pipeline_common.sh && printf '%s\\n%s\\n%s\\n%s\\n' \"$EXPORTER_HEALTH_URL\" \"$EXPORTER_READY_URL\" \"$VLLM_BASE_URL\" \"$VLLM_HEALTH_URL\"",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    health_url, ready_url, vllm_base_url, vllm_health_url = proc.stdout.strip().splitlines()
    assert health_url.endswith("/healthz")
    assert ready_url.endswith("/readyz")
    assert vllm_base_url.endswith(":8000")
    assert vllm_health_url.endswith("/health")


def test_pipeline_scripts_wait_for_exporter_ready_endpoint() -> None:
    one_click = (ROOT / "scripts/one_click_up.sh").read_text(encoding="utf-8")
    pipeline = (ROOT / "scripts/run_pipeline_local_offline_backend.sh").read_text(encoding="utf-8")

    assert 'wait_http_ok "${EXPORTER_READY_URL}"' in one_click
    assert 'wait_http_ok "${EXPORTER_READY_URL}"' in pipeline


def test_sglang_launcher_has_stable_local_defaults() -> None:
    launcher = (ROOT / "scripts/launch_sglang.sh").read_text(encoding="utf-8")

    assert 'PYTHON_FOR_SGLANG="${ROOT}/.venv-sglang/bin/python"' in launcher
    assert 'SGLANG_ENABLE_METRICS="${SGLANG_ENABLE_METRICS:-true}"' in launcher
    assert 'SGLANG_SKIP_SERVER_WARMUP="${SGLANG_SKIP_SERVER_WARMUP:-true}"' in launcher
    assert "SGLANG_MEM_FRACTION=\"${SGLANG_MEM_FRACTION-0.55}\"" in launcher
    assert "SGLANG_CONTEXT_LENGTH=\"${SGLANG_CONTEXT_LENGTH-1024}\"" in launcher
    assert '--served-model-name "${SERVED_MODEL_NAME}"' in launcher
    assert "SGLANG_LAUNCH_ARGS+=(${SGLANG_EXTRA_ARGS})" in launcher


def test_orchestration_scripts_export_stable_launch_parameters() -> None:
    one_click = (ROOT / "scripts/one_click_up.sh").read_text(encoding="utf-8")
    pipeline = (ROOT / "scripts/run_pipeline_local_offline_backend.sh").read_text(encoding="utf-8")
    template = (ROOT / "scripts/experiment_template_local.sh").read_text(encoding="utf-8")

    assert 'TOOL_PYTHON="${ROOT}/.venv/bin/python"' in pipeline
    assert 'TOOL_PYTHON="${ROOT}/.venv/bin/python"' in one_click
    for script in (one_click, pipeline):
        assert "export SGLANG_HOST" in script
        assert "export SGLANG_PORT" in script
        assert "export SERVED_MODEL_NAME" in script
    assert 'ISOLATE_EXPERIMENT_STACK="${ISOLATE_EXPERIMENT_STACK:-1}"' in template
    assert 'ISOLATE_CAPACITY_SWEEP_POINTS="${ISOLATE_CAPACITY_SWEEP_POINTS:-1}"' in template
    assert "--restart-stack-before-each-point" in template


def test_stage2_benchmark_requires_real_config_and_pair_launcher() -> None:
    benchmark = (ROOT / "scripts/benchmark_stage2.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts/launch_stage2_vllm_pair.sh").read_text(encoding="utf-8")

    assert "config.stage2.template.yaml" in benchmark
    assert "benchmark.stage2_evaluation" in benchmark
    assert "kvaware_experiment.py" not in benchmark
    assert 'PORT_A="${PORT_A:-18080}"' in launcher
    assert 'PORT_B="${PORT_B:-18081}"' in launcher
    assert "--enable-prefix-caching" in launcher


def test_stage2_local_stack_wires_distinct_strategy_modes() -> None:
    stack = (ROOT / "scripts/stage2_local_stack.sh").read_text(encoding="utf-8")

    assert "gateway-static static" in stack
    assert "gateway-load load-aware" in stack
    assert "gateway-shadow shadow" in stack
    assert 'PID_DIR/gateway-enforced.pid' in stack
    assert "KAVORA_ROUTING_MODE=enforced" in stack
    assert "KAVORA_CACHE_FIDELITY=exact" in stack
    assert "KAVORA_VLLM_HASH_RESOLVER_URL" in stack
    assert "KAVORA_BACKEND_STATE_URLS" in stack
    assert "make benchmark-stage2" in stack
