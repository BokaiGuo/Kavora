from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from benchmark.gateway_runner import percentile
from exporter.prometheus_parse import aggregate_prometheus_text

REQUIRED_STRATEGIES = ("direct", "static", "load-aware", "kv-aware-shadow", "kv-aware-enforced")
DEFAULT_WORKLOADS = ("random", "repeated-system", "long-shared-prefix", "tenant-affinity")


@dataclass(frozen=True)
class TargetSpec:
    strategy: str
    url: str
    api_key: str = ""


@dataclass(frozen=True)
class BackendMetricSpec:
    backend_id: str
    metrics_url: str


@dataclass(frozen=True)
class EvaluationConfig:
    model: str
    model_revision: str
    backend_version: str
    repetitions: int
    requests_per_repetition: int
    concurrency: int
    timeout_s: float
    max_tokens: int
    seed: int
    workloads: tuple[str, ...]
    targets: tuple[TargetSpec, ...]
    backends: tuple[BackendMetricSpec, ...]
    source: dict[str, Any]


def _absolute_http_url(value: object, field: str) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute HTTP URL")
    return text.rstrip("/")


def load_config(path: str | Path) -> EvaluationConfig:
    source = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(source, dict):
        raise ValueError("configuration root must be a mapping")
    repetitions = int(source.get("repetitions", 10))
    if repetitions < 10:
        raise ValueError("Stage 2 evidence requires at least 10 repetitions")
    requests = int(source.get("requests_per_repetition", 24))
    concurrency = int(source.get("concurrency", 2))
    if requests <= 0 or concurrency <= 0:
        raise ValueError("requests_per_repetition and concurrency must be positive")
    model = str(source.get("model", "")).strip()
    if not model:
        raise ValueError("model is required")
    model_revision = str(source.get("model_revision", "")).strip()
    if not model_revision:
        raise ValueError("model_revision is required")
    backend_version = str(source.get("backend_version", "")).strip()
    if not backend_version:
        raise ValueError("backend_version is required")

    targets: list[TargetSpec] = []
    for item in source.get("targets", []):
        if not isinstance(item, dict):
            raise ValueError("each target must be a mapping")
        strategy = str(item.get("strategy", "")).strip()
        env_name = str(item.get("api_key_env", "")).strip()
        api_key = os.environ.get(env_name, "") if env_name else str(item.get("api_key", ""))
        targets.append(TargetSpec(strategy, _absolute_http_url(item.get("url"), f"target {strategy} url"), api_key))
    names = [target.strategy for target in targets]
    if len(names) != len(set(names)):
        raise ValueError("target strategies must be unique")
    target_urls = [target.url for target in targets]
    if len(target_urls) != len(set(target_urls)):
        raise ValueError("target URLs must be unique independent endpoints")
    missing = [name for name in REQUIRED_STRATEGIES if name not in names]
    extra = [name for name in names if name not in REQUIRED_STRATEGIES]
    if missing or extra:
        raise ValueError(
            "targets must contain exactly "
            + ", ".join(REQUIRED_STRATEGIES)
            + f"; missing={missing or 'none'} extra={extra or 'none'}"
        )

    backends: list[BackendMetricSpec] = []
    for item in source.get("backends", []):
        if not isinstance(item, dict):
            raise ValueError("each backend must be a mapping")
        backend_id = str(item.get("id", "")).strip()
        if not backend_id:
            raise ValueError("backend id is required")
        backends.append(BackendMetricSpec(backend_id, _absolute_http_url(item.get("metrics_url"), f"backend {backend_id} metrics_url")))
    if len(backends) < 2:
        raise ValueError("Stage 2 evidence requires at least two backend metrics endpoints")
    backend_ids = [backend.backend_id for backend in backends]
    if len(backend_ids) != len(set(backend_ids)):
        raise ValueError("backend ids must be unique")
    metrics_urls = [backend.metrics_url for backend in backends]
    if len(metrics_urls) != len(set(metrics_urls)):
        raise ValueError("backend metrics URLs must be unique independent endpoints")

    workloads = tuple(str(item) for item in source.get("workloads", DEFAULT_WORKLOADS))
    if len(workloads) != len(set(workloads)):
        raise ValueError("workloads must be unique")
    missing_workloads = [name for name in DEFAULT_WORKLOADS if name not in workloads]
    extra_workloads = [name for name in workloads if name not in DEFAULT_WORKLOADS]
    if missing_workloads or extra_workloads:
        raise ValueError(
            "workloads must contain exactly "
            + ", ".join(DEFAULT_WORKLOADS)
            + f"; missing={missing_workloads or 'none'} extra={extra_workloads or 'none'}"
        )
    timeout_s = float(source.get("timeout_s", 120.0))
    max_tokens = int(source.get("max_tokens", 32))
    if timeout_s <= 0 or max_tokens <= 0:
        raise ValueError("timeout_s and max_tokens must be positive")

    return EvaluationConfig(
        model=model,
        model_revision=model_revision,
        backend_version=backend_version,
        repetitions=repetitions,
        requests_per_repetition=requests,
        concurrency=concurrency,
        timeout_s=timeout_s,
        max_tokens=max_tokens,
        seed=int(source.get("seed", 7)),
        workloads=workloads,
        targets=tuple(targets),
        backends=tuple(backends),
        source=source,
    )


def _deterministic_text(seed: int, length: int) -> str:
    rng = random.Random(seed)
    words = [f"token{rng.randrange(100000):05d}" for _ in range(max(1, length // 11))]
    return " ".join(words)


def build_workload_messages(workload: str, *, request_index: int, seed: int) -> list[dict[str, str]]:
    unique = _deterministic_text(seed * 100003 + request_index, 256)
    if workload == "random":
        return [{"role": "user", "content": f"Analyze this independent input: {unique}"}]
    if workload == "repeated-system":
        return [
            {"role": "system", "content": "You are Kavora's deterministic routing benchmark assistant. Answer concisely and preserve the supplied identifier."},
            {"role": "user", "content": f"Identifier {request_index}: {unique}"},
        ]
    if workload == "long-shared-prefix":
        shared = ("Kavora evaluates prefix-local inference routing under controlled load. " * 80).strip()
        return [
            {"role": "system", "content": shared},
            {"role": "user", "content": f"Request {request_index}: summarize {unique}"},
        ]
    if workload == "tenant-affinity":
        return [
            {"role": "system", "content": "Continue this tenant-scoped conversation while retaining its shared context."},
            {"role": "user", "content": "We are measuring whether related turns remain local to a reusable backend."},
            {"role": "assistant", "content": "Understood. I will retain the shared experiment context."},
            {"role": "user", "content": f"Turn {request_index}: give one observation about {unique}"},
        ]
    raise ValueError(f"unknown workload: {workload}")


def summarize_samples(samples: list[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    successful = [sample for sample in samples if sample.get("ok")]
    latencies = [float(sample["latency_ms"]) for sample in successful]
    ttfts = [float(sample["ttft_ms"]) for sample in successful if sample.get("ttft_ms") is not None]
    routes = [str(sample.get("backend", "")) for sample in successful if sample.get("backend")]
    return {
        "requests": len(samples),
        "ok": len(successful),
        "failed": len(samples) - len(successful),
        "error_rate": (len(samples) - len(successful)) / max(len(samples), 1),
        "throughput_req_s": len(successful) / max(elapsed_s, 1e-9),
        "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95), "p99": percentile(latencies, 0.99)},
        "ttft_ms": {"p50": percentile(ttfts, 0.50), "p95": percentile(ttfts, 0.95), "p99": percentile(ttfts, 0.99)},
        "routing_distribution": dict(sorted(Counter(routes).items())),
        "route_switches": sum(left != right for left, right in zip(routes, routes[1:])),
        "fallback_count": sum(bool(sample.get("fallback")) for sample in samples),
        "sample_errors": [str(sample.get("error")) for sample in samples if sample.get("error")][:5],
        "elapsed_s": elapsed_s,
    }


def _pick(metrics: dict[str, float], *names: str) -> float | None:
    for name in names:
        if name in metrics:
            return float(metrics[name])
    return None


def summarize_vllm_window(before: dict[str, dict[str, float]], after: dict[str, dict[str, float]]) -> dict[str, Any]:
    hits_delta = 0.0
    queries_delta = 0.0
    complete_counters = 0
    counter_resets = 0
    usage: list[float] = []
    queues: list[float] = []
    for backend_id, after_metrics in after.items():
        before_metrics = before.get(backend_id, {})
        hits_after = _pick(after_metrics, "vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits")
        queries_after = _pick(after_metrics, "vllm:prefix_cache_queries_total", "vllm:prefix_cache_queries")
        hits_before = _pick(before_metrics, "vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits")
        queries_before = _pick(before_metrics, "vllm:prefix_cache_queries_total", "vllm:prefix_cache_queries")
        if None not in (hits_after, queries_after, hits_before, queries_before):
            if float(hits_after) < float(hits_before) or float(queries_after) < float(queries_before):
                counter_resets += 1
            else:
                hits_delta += float(hits_after) - float(hits_before)
                queries_delta += float(queries_after) - float(queries_before)
                complete_counters += 1
        current_usage = _pick(after_metrics, "vllm:kv_cache_usage_perc")
        current_queue = _pick(after_metrics, "vllm:num_requests_waiting", "vllm:num_requests_waiting_total")
        if current_usage is not None:
            usage.append(current_usage)
        if current_queue is not None:
            queues.append(current_queue)
    if counter_resets:
        quality = "counter_reset"
    elif complete_counters == len(after) and after:
        quality = "ok"
    else:
        quality = "missing"
    return {
        "prefix_hits_delta": hits_delta if complete_counters else None,
        "prefix_queries_delta": queries_delta if complete_counters else None,
        "kv_reuse_rate": hits_delta / queries_delta if complete_counters and queries_delta > 0 else None,
        "gpu_kv_utilization_mean": statistics.mean(usage) if usage else None,
        "queue_depth_mean": statistics.mean(queues) if queues else None,
        "metric_quality": quality,
    }


def _mean_ci95(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"mean": None, "ci95_low": None, "ci95_high": None, "n": 0}
    mean = statistics.mean(values)
    if len(values) < 2:
        return {"mean": mean, "ci95_low": None, "ci95_high": None, "n": len(values)}
    margin = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return {"mean": mean, "ci95_low": mean - margin, "ci95_high": mean + margin, "n": len(values)}


def aggregate_repetitions(repetitions: list[dict[str, Any]]) -> dict[str, Any]:
    def values(path: tuple[str, ...]) -> list[float]:
        output: list[float] = []
        for repetition in repetitions:
            current: Any = repetition
            for key in path:
                if not isinstance(current, dict) or current.get(key) is None:
                    current = None
                    break
                current = current[key]
            if isinstance(current, (int, float)):
                output.append(float(current))
        return output

    routing = Counter()
    for repetition in repetitions:
        routing.update(repetition["summary"].get("routing_distribution", {}))
    return {
        "repetitions": len(repetitions),
        "throughput_req_s": _mean_ci95(values(("summary", "throughput_req_s"))),
        "error_rate": _mean_ci95(values(("summary", "error_rate"))),
        "latency_p50_ms": _mean_ci95(values(("summary", "latency_ms", "p50"))),
        "latency_p95_ms": _mean_ci95(values(("summary", "latency_ms", "p95"))),
        "latency_p99_ms": _mean_ci95(values(("summary", "latency_ms", "p99"))),
        "ttft_p50_ms": _mean_ci95(values(("summary", "ttft_ms", "p50"))),
        "ttft_p95_ms": _mean_ci95(values(("summary", "ttft_ms", "p95"))),
        "ttft_p99_ms": _mean_ci95(values(("summary", "ttft_ms", "p99"))),
        "kv_reuse_rate": _mean_ci95(values(("backend_window", "kv_reuse_rate"))),
        "gpu_kv_utilization_mean": _mean_ci95(values(("backend_window", "gpu_kv_utilization_mean"))),
        "queue_depth_mean": _mean_ci95(values(("backend_window", "queue_depth_mean"))),
        "route_switches": sum(int(repetition["summary"].get("route_switches", 0)) for repetition in repetitions),
        "fallback_count": sum(int(repetition["summary"].get("fallback_count", 0)) for repetition in repetitions),
        "routing_distribution": dict(sorted(routing.items())),
        "metric_quality": "ok" if repetitions and all(repetition["backend_window"].get("metric_quality") == "ok" for repetition in repetitions) else "mixed_or_missing",
    }


async def _scrape_metrics(
    client: httpx.AsyncClient,
    backends: tuple[BackendMetricSpec, ...],
    timeout_s: float,
) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    output: dict[str, dict[str, float]] = {}
    errors: dict[str, str] = {}
    for backend in backends:
        try:
            response = await client.get(backend.metrics_url, timeout=timeout_s)
            response.raise_for_status()
            output[backend.backend_id] = aggregate_prometheus_text(response.text)
        except Exception as exc:
            output[backend.backend_id] = {}
            errors[backend.backend_id] = f"{type(exc).__name__}: {exc}"
    return output, errors


async def _run_one(client: httpx.AsyncClient, target: TargetSpec, config: EvaluationConfig, workload: str, index: int) -> dict[str, Any]:
    payload = {
        "model": config.model,
        "messages": build_workload_messages(workload, request_index=index, seed=config.seed),
        "stream": True,
        "max_tokens": config.max_tokens,
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json", "X-Kavora-Benchmark-Workload": workload}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    started = time.perf_counter()
    first_byte: float | None = None
    status = 0
    error = ""
    backend = ""
    fallback = False
    request_id = ""
    response_bytes = 0
    try:
        async with client.stream("POST", target.url + "/v1/chat/completions", json=payload, headers=headers, timeout=config.timeout_s) as response:
            status = response.status_code
            backend = response.headers.get("X-Kavora-Backend", "")
            request_id = response.headers.get("X-Request-ID", "")
            fallback = response.headers.get("X-Kavora-Routing-Fallback", "false").lower() == "true"
            async for chunk in response.aiter_bytes():
                if chunk and first_byte is None:
                    first_byte = time.perf_counter()
                response_bytes += len(chunk)
            if status < 200 or status >= 300:
                error = f"HTTP {status}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finished = time.perf_counter()
    return {
        "index": index,
        "ok": not error and 200 <= status < 300,
        "status": status,
        "error": error,
        "latency_ms": (finished - started) * 1000,
        "ttft_ms": (first_byte - started) * 1000 if first_byte is not None else None,
        "response_bytes": response_bytes,
        "backend": backend,
        "fallback": fallback,
        "request_id": request_id,
    }


async def _run_repetition(client: httpx.AsyncClient, target: TargetSpec, config: EvaluationConfig, workload: str, repetition: int) -> dict[str, Any]:
    before, before_errors = await _scrape_metrics(client, config.backends, config.timeout_s)
    semaphore = asyncio.Semaphore(config.concurrency)

    async def limited(index: int) -> dict[str, Any]:
        async with semaphore:
            return await _run_one(client, target, config, workload, repetition * config.requests_per_repetition + index)

    started = time.perf_counter()
    samples = await asyncio.gather(*(limited(index) for index in range(config.requests_per_repetition)))
    elapsed = time.perf_counter() - started
    after, after_errors = await _scrape_metrics(client, config.backends, config.timeout_s)
    return {
        "repetition": repetition + 1,
        "summary": summarize_samples(samples, elapsed),
        "backend_window": summarize_vllm_window(before, after),
        "metric_scrape_errors": {"before": before_errors, "after": after_errors},
        "samples": samples,
    }


def _git_revision() -> str:
    return _command_output(["git", "rev-parse", "HEAD"])


def _command_output(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _config_hash(source: dict[str, Any]) -> str:
    sanitized = json.loads(json.dumps(source))
    for target in sanitized.get("targets", []):
        target.pop("api_key", None)
    return hashlib.sha256(json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _comparisons(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_workload = {result["workload"]: result for result in results if result["strategy"] == "direct"}
    output = []
    for result in results:
        if result["strategy"] == "direct" or result["workload"] not in by_workload:
            continue
        direct = by_workload[result["workload"]]["aggregate"]
        aggregate = result["aggregate"]
        direct_ttft = direct["ttft_p95_ms"]["mean"]
        target_ttft = aggregate["ttft_p95_ms"]["mean"]
        direct_latency = direct["latency_p95_ms"]["mean"]
        target_latency = aggregate["latency_p95_ms"]["mean"]
        output.append({
            "strategy": result["strategy"],
            "workload": result["workload"],
            "gateway_ttft_p95_overhead_ms": target_ttft - direct_ttft if target_ttft is not None and direct_ttft is not None else None,
            "gateway_latency_p95_overhead_ms": target_latency - direct_latency if target_latency is not None and direct_latency is not None else None,
        })
    return output


def balanced_target_order(targets: tuple[TargetSpec, ...], repetition: int) -> tuple[TargetSpec, ...]:
    if not targets:
        return ()
    offset = repetition % len(targets)
    return targets[offset:] + targets[:offset]


async def run_evaluation(config: EvaluationConfig) -> dict[str, Any]:
    repetitions_by_case: dict[tuple[str, str], list[dict[str, Any]]] = {
        (target.strategy, workload): [] for target in config.targets for workload in config.workloads
    }
    execution_schedule = []
    async with httpx.AsyncClient(trust_env=False) as client:
        for workload in config.workloads:
            for repeat in range(config.repetitions):
                ordered_targets = balanced_target_order(config.targets, repeat)
                execution_schedule.append({
                    "workload": workload,
                    "repetition": repeat + 1,
                    "target_order": [target.strategy for target in ordered_targets],
                })
                for target in ordered_targets:
                    repetition = await _run_repetition(client, target, config, workload, repeat)
                    repetitions_by_case[(target.strategy, workload)].append(repetition)
    results = []
    for target in config.targets:
        for workload in config.workloads:
            repetitions = repetitions_by_case[(target.strategy, workload)]
            results.append({"strategy": target.strategy, "workload": workload, "repetitions": repetitions, "aggregate": aggregate_repetitions(repetitions)})
    manifest = {
        "model": config.model,
        "model_revision": config.model_revision,
        "backend_version": config.backend_version,
        "repetitions": config.repetitions,
        "requests_per_repetition": config.requests_per_repetition,
        "concurrency": config.concurrency,
        "max_tokens": config.max_tokens,
        "seed": config.seed,
        "workloads": list(config.workloads),
        "targets": [{"strategy": target.strategy, "url": target.url} for target in config.targets],
        "backends": [{"id": backend.backend_id, "metrics_url": backend.metrics_url} for backend in config.backends],
        "git_revision": _git_revision(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "gpu": _command_output(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]),
    }
    return {
        "schema_version": "kavora-stage2-evaluation/v2",
        "status": "real_backend_measurement",
        "config_hash": _config_hash(config.source),
        "generated_at_unix": time.time(),
        "manifest": manifest,
        "results": results,
        "execution_schedule": execution_schedule,
        "comparisons": _comparisons(results),
        "claim_boundary": "Measurements describe this exact hardware, model, configuration, and workload manifest only; no general performance claim is made without independent replication.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the reproducible Kavora Stage 2 real-backend routing matrix")
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    if args.validate_only:
        print(f"valid Stage 2 config: {args.config}")
        return 0
    report = asyncio.run(run_evaluation(config))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output} config_hash={report['config_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
