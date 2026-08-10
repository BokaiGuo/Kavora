from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "kavora-cache-fidelity/v1"
FIDELITIES = ("none", "affinity", "shadow-index", "exact-kv-events")
DEFAULT_LAGS_MS = (0, 100, 500, 1000, 2000, 5000, 10000)


@dataclass(frozen=True)
class TraceRequest:
    request_id: str
    cache_key: str
    prompt_tokens: int
    exact_backend: str
    match_ratio: float


def build_trace(requests: int = 1000, backends: int = 4, seed: int = 17) -> list[TraceRequest]:
    if requests <= 0 or backends < 2:
        raise ValueError("requests must be positive and backends must be at least two")
    trace = []
    for index in range(requests):
        digest = hashlib.sha256(f"{seed}:{index // 3}".encode()).digest()
        exact = f"gpu-{digest[0] % backends}"
        trace.append(TraceRequest(f"req-{index}", f"prefix-{index // 3}", 256 + digest[1] * 8, exact, 0.55 + (digest[2] / 255) * 0.4))
    return trace


def confidence(lag_ms: int, decay_lambda: float) -> float:
    return math.exp(-decay_lambda * lag_ms / 1000)


def _alternate_backend(request: TraceRequest, backends: int) -> str:
    return f"gpu-{int(request.request_id.split('-')[-1]) % backends}"


def _predicted_backend(request: TraceRequest, fidelity: str, lag_ms: int, backends: int) -> tuple[str, float, bool]:
    conf = confidence(lag_ms, {"none": 0, "affinity": 0.18, "shadow-index": 0.12, "exact-kv-events": 0.35}[fidelity])
    if fidelity == "none":
        return _alternate_backend(request, backends), 0.0, False
    if conf < 0.2:
        return _alternate_backend(request, backends), conf, True
    error_threshold = {"affinity": 62, "shadow-index": 28, "exact-kv-events": 0}[fidelity] + min(80, lag_ms // 150)
    digest = hashlib.sha256(f"{fidelity}:{request.cache_key}:{lag_ms}".encode()).digest()
    if digest[0] < error_threshold:
        wrong = (int(request.exact_backend.split("-")[-1]) + 1 + digest[1] % (backends - 1)) % backends
        return f"gpu-{wrong}", conf, False
    return request.exact_backend, conf, False


def evaluate(trace: list[TraceRequest], fidelity: str, lag_ms: int, backends: int = 4) -> dict[str, Any]:
    if fidelity not in FIDELITIES:
        raise ValueError(f"unknown fidelity {fidelity}")
    started = time.perf_counter_ns()
    selected: list[str] = []
    correct = fallback = wrong_affinity = reversals = 0
    ttft: list[float] = []
    reuse: list[float] = []
    for request in trace:
        backend, conf, fell_back = _predicted_backend(request, fidelity, lag_ms, backends)
        selected.append(backend)
        is_correct = backend == request.exact_backend
        correct += int(is_correct)
        fallback += int(fell_back)
        wrong_affinity += int(fidelity == "affinity" and not is_correct and not fell_back)
        zero_lag_backend, _, _ = _predicted_backend(request, fidelity, 0, backends)
        reversals += int(backend != zero_lag_backend)
        effective_reuse = request.match_ratio * conf if is_correct and fidelity != "none" else 0.0
        reuse.append(effective_reuse)
        queue_penalty = selected.count(backend) / len(trace) * 18
        ttft.append(18 + request.prompt_tokens * 0.055 * (1 - effective_reuse) + queue_penalty)
    elapsed_ns = time.perf_counter_ns() - started
    counts = [selected.count(f"gpu-{index}") for index in range(backends)]
    mean_count = statistics.mean(counts)
    state_bytes = {"none": 0, "affinity": len(trace) * 48, "shadow-index": backends * 256, "exact-kv-events": len(trace) * 96}[fidelity]
    update_bytes = {"none": 0, "affinity": 32, "shadow-index": 128, "exact-kv-events": 96}[fidelity]
    avg_ttft = statistics.mean(ttft)
    return {
        "fidelity": fidelity,
        "lag_ms": lag_ms,
        "requests": len(trace),
        "routing_accuracy": correct / len(trace),
        "cache_reuse_ratio": statistics.mean(reuse),
        "ttft_mean_ms": avg_ttft,
        "throughput_proxy_req_s": 1000 / avg_ttft,
        "controller_cpu_ns_per_decision": elapsed_ns / len(trace),
        "controller_state_bytes": state_bytes,
        "state_bandwidth_bytes_s": 0 if lag_ms == 0 else update_bytes * 1000 / lag_ms,
        "decision_latency_us": elapsed_ns / len(trace) / 1000,
        "wrong_affinity_rate": wrong_affinity / len(trace),
        "backend_imbalance_cv": statistics.pstdev(counts) / mean_count,
        "decision_reversal_rate": reversals / len(trace),
        "fallback_rate": fallback / len(trace),
        "mean_confidence": confidence(lag_ms, {"none": 0, "affinity": 0.18, "shadow-index": 0.12, "exact-kv-events": 0.35}[fidelity]) if fidelity != "none" else 0,
    }


def run(requests: int = 1000, backends: int = 4, seed: int = 17, lags_ms: tuple[int, ...] = DEFAULT_LAGS_MS) -> dict[str, Any]:
    trace = build_trace(requests, backends, seed)
    results = [evaluate(trace, fidelity, lag, backends) for fidelity in FIDELITIES for lag in lags_ms]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "deterministic_trace_ablation",
        "manifest": {"requests": requests, "backends": backends, "seed": seed, "lags_ms": list(lags_ms), "python": platform.python_version()},
        "results": results,
        "claim_boundary": "This artifact validates fidelity and staleness sensitivity on a deterministic routing trace. It is not a real-GPU latency or throughput result; the Stage 2 real-backend artifact remains required for performance claims.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Kavora cache-evidence fidelity and lag ablations")
    parser.add_argument("--out", default="results/stage4/cache_fidelity.json")
    parser.add_argument("--report", default="results/stage4/cache_fidelity.md")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--backends", type=int, default=4)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()
    artifact = run(args.requests, args.backends, args.seed)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    from benchmark.cache_fidelity_report import render

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render(artifact), encoding="utf-8")
    print(f"wrote {output} and {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
