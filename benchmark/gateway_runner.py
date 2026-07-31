from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import resource
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def config_hash(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_prompt(seed: int, length: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz "
    return "".join(alphabet[(seed + index * 17) % len(alphabet)] for index in range(max(1, length)))


@dataclass(frozen=True)
class PathSpec:
    name: str
    url: str

    @property
    def stream(self) -> bool:
        return "stream" in self.name.lower()


async def run_one(
    client: httpx.AsyncClient,
    path: PathSpec,
    model: str,
    prompt: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": path.stream}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    started = time.perf_counter()
    first_byte: float | None = None
    status = 0
    error = ""
    response_bytes = 0
    try:
        if path.stream:
            async with client.stream("POST", path.url.rstrip("/") + "/v1/chat/completions", json=payload, headers=headers, timeout=timeout) as response:
                status = response.status_code
                async for chunk in response.aiter_bytes():
                    if first_byte is None and chunk:
                        first_byte = time.perf_counter()
                    response_bytes += len(chunk)
                if status < 200 or status >= 300:
                    error = f"HTTP {status}"
        else:
            response = await client.post(path.url.rstrip("/") + "/v1/chat/completions", json=payload, headers=headers, timeout=timeout)
            status = response.status_code
            response_bytes = len(response.content)
            first_byte = time.perf_counter() if response.content else None
            if status < 200 or status >= 300:
                error = f"HTTP {status}"
    except Exception as exc:  # benchmark output must preserve failures, not abort the matrix
        error = f"{type(exc).__name__}: {exc}"
    finished = time.perf_counter()
    return {
        "ok": not error and 200 <= status < 300,
        "status": status,
        "error": error,
        "latency_ms": (finished - started) * 1000,
        "ttft_ms": ((first_byte or finished) - started) * 1000 if first_byte else None,
        "response_bytes": response_bytes,
    }


async def run_path(
    path: PathSpec,
    requests: int,
    concurrency: int,
    model: str,
    input_length: int,
    api_key: str,
    timeout: float,
    seed: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(trust_env=False) as client:
        async def limited(index: int) -> dict[str, Any]:
            async with semaphore:
                return await run_one(client, path, model, build_prompt(seed + index, input_length), api_key, timeout)

        started = time.perf_counter()
        samples = await asyncio.gather(*(limited(index) for index in range(requests)))
        elapsed = max(time.perf_counter() - started, 1e-9)
    successful = [sample for sample in samples if sample["ok"]]
    latencies = [sample["latency_ms"] for sample in successful]
    ttfts = [sample["ttft_ms"] for sample in successful if sample["ttft_ms"] is not None]
    return {
        "path": path.name,
        "url": path.url,
        "stream": path.stream,
        "requests": len(samples),
        "ok": len(successful),
        "failed": len(samples) - len(successful),
        "error_rate": (len(samples) - len(successful)) / max(len(samples), 1),
        "throughput_req_s": len(successful) / elapsed,
        "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95), "p99": percentile(latencies, 0.99)},
        "ttft_ms": {"p50": percentile(ttfts, 0.50), "p95": percentile(ttfts, 0.95), "p99": percentile(ttfts, 0.99)},
        "response_bytes_mean": statistics.mean([sample["response_bytes"] for sample in successful]) if successful else None,
        "sample_errors": [sample["error"] for sample in samples if sample["error"]][:5],
        "elapsed_s": elapsed,
    }


def parse_paths(values: list[str]) -> list[PathSpec]:
    paths = []
    for value in values:
        name, separator, url = value.partition("=")
        if not separator or not name or not url.startswith(("http://", "https://")):
            raise ValueError(f"path must be NAME=HTTP_URL, got {value!r}")
        paths.append(PathSpec(name, url))
    if not paths:
        raise ValueError("at least one --path is required")
    return paths


async def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    paths = parse_paths(args.path)
    configuration = {"paths": [{"name": path.name, "url": path.url} for path in paths], "model": args.model, "requests": args.requests, "concurrency": args.concurrency, "input_length": args.input_length, "seed": args.seed}
    results = [await run_path(path, args.requests, args.concurrency, args.model, args.input_length, args.api_key, args.timeout, args.seed) for path in paths]
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    return {
        "schema_version": "kavora-stage1-gateway-v1",
        "config_hash": config_hash(configuration),
        "configuration": configuration,
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "pid": os.getpid()},
        "memory": {"peak_rss_mb": rss_mb, "semantics": "runner_process_high_water_mark"},
        "paths": results,
        "phase_attribution": phase_attribution(results),
        "generated_at_unix": time.time(),
    }


def phase_attribution(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {result["path"]: result for result in results}
    direct = by_name.get("direct")
    go_only = by_name.get("go_only")
    go_rust = by_name.get("go_rust_unary")
    stream = by_name.get("go_rust_stream")
    if not all((direct, go_only, go_rust)):
        return {"status": "insufficient_paths", "required": ["direct", "go_only", "go_rust_unary"]}
    return {
        "status": "proxy_deltas_only",
        "go_forward_p95_ms": go_only["latency_ms"]["p95"] - direct["latency_ms"]["p95"] if go_only["latency_ms"]["p95"] is not None and direct["latency_ms"]["p95"] is not None else None,
        "rust_policy_plus_rpc_p95_ms": go_rust["latency_ms"]["p95"] - go_only["latency_ms"]["p95"] if go_rust["latency_ms"]["p95"] is not None and go_only["latency_ms"]["p95"] is not None else None,
        "stream_path_present": stream is not None,
        "claim_boundary": "deltas are end-to-end proxies, not isolated CPU timings",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reproducible Kavora Stage 1 gateway path measurements")
    parser.add_argument("--path", action="append", default=[], help="NAME=HTTP_URL; repeat for direct, go_only, go_rust_unary, go_rust_stream")
    parser.add_argument("--out", required=True)
    parser.add_argument("--api-key", default=os.environ.get("KAVORA_API_KEY", ""))
    parser.add_argument("--model", default="demo-model")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--input-length", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.requests <= 0 or args.concurrency <= 0:
        parser.error("--requests and --concurrency must be positive")
    try:
        result = asyncio.run(run_matrix(args))
    except ValueError as exc:
        parser.error(str(exc))
    output = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {output} config_hash={result['config_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
