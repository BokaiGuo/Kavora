from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    idx = q * (len(xs) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def deterministic_request_seed(base_seed: int, request_id: int) -> int:
    key = f"{base_seed}:{request_id}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    return int(digest[:16], 16)


def _build_prompt(seed: int, input_len: int) -> str:
    rng = random.Random(seed)
    alphabet = "abcdefghijklmnopqrstuvwxyz "
    return "".join(rng.choice(alphabet) for _ in range(max(input_len, 1)))


def _build_prompt_with_reuse(
    *,
    seed: int,
    input_len: int,
    shared_prefix_ratio: float,
    shared_prefix_len: int,
    unique_suffix_len: int,
) -> str:
    use_shared = random.Random(seed ^ 0x5A5A5A5A).random() < max(0.0, min(1.0, shared_prefix_ratio))
    shared_len = shared_prefix_len if use_shared else 0
    shared = "S" * max(0, shared_len)
    unique_target = max(1, input_len - shared_len, unique_suffix_len)
    unique = _build_prompt(seed + 1, unique_target)
    prompt = f"{shared}\n{unique}" if shared else unique
    if len(prompt) > input_len:
        return prompt[:input_len]
    if len(prompt) < input_len:
        return prompt + ("x" * (input_len - len(prompt)))
    return prompt


def _request_digest(prompts: list[str]) -> str:
    h = hashlib.sha256()
    for p in prompts:
        h.update(p.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


async def _one_request(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    endpoint: str,
    model: str,
    seed: int,
    input_len: int,
    output_len: int,
    timeout_s: float,
    shared_prefix_ratio: float,
    shared_prefix_len: int,
    unique_suffix_len: int,
) -> tuple[float, bool]:
    payload = {
        "model": model,
        "prompt": _build_prompt_with_reuse(
            seed=seed,
            input_len=input_len,
            shared_prefix_ratio=shared_prefix_ratio,
            shared_prefix_len=shared_prefix_len,
            unique_suffix_len=unique_suffix_len,
        ),
        "max_tokens": max(1, output_len),
        "temperature": 0.0,
    }
    url = f"{base_url.rstrip('/')}{endpoint}"
    t0 = time.perf_counter()
    try:
        resp = await client.post(url, json=payload, timeout=timeout_s)
        ok = resp.status_code < 400
    except Exception:
        ok = False
    dt = time.perf_counter() - t0
    return dt, ok


async def run_custom_http(
    *,
    base_url: str,
    endpoint: str,
    model: str,
    num_requests: int,
    concurrency: int,
    base_seed: int,
    input_len: int,
    output_len: int,
    timeout_s: float,
    shared_prefix_ratio: float = 0.0,
    shared_prefix_len: int = 0,
    unique_suffix_len: int = 64,
) -> dict[str, Any]:
    req_ids = list(range(max(0, num_requests)))
    req_seeds = [deterministic_request_seed(base_seed, i) for i in req_ids]
    prompts_for_digest = [
        _build_prompt_with_reuse(
            seed=s,
            input_len=input_len,
            shared_prefix_ratio=shared_prefix_ratio,
            shared_prefix_len=shared_prefix_len,
            unique_suffix_len=unique_suffix_len,
        )
        for s in req_seeds
    ]

    sem = asyncio.Semaphore(max(1, concurrency))
    latencies: list[float] = []
    ok_count = 0

    t_start = time.perf_counter()
    async with httpx.AsyncClient() as client:
        async def run_one(seed: int) -> None:
            nonlocal ok_count
            async with sem:
                dt, ok = await _one_request(
                    client,
                    base_url=base_url,
                    endpoint=endpoint,
                    model=model,
                    seed=seed,
                    input_len=input_len,
                    output_len=output_len,
                    timeout_s=timeout_s,
                    shared_prefix_ratio=shared_prefix_ratio,
                    shared_prefix_len=shared_prefix_len,
                    unique_suffix_len=unique_suffix_len,
                )
                latencies.append(dt)
                if ok:
                    ok_count += 1

        await asyncio.gather(*(run_one(s) for s in req_seeds))
    elapsed_s = max(1e-9, time.perf_counter() - t_start)

    e2e_p95_ms = _percentile(latencies, 0.95) * 1000.0
    e2e_mean_ms = (statistics.mean(latencies) * 1000.0) if latencies else 0.0
    fail_count = len(latencies) - ok_count

    return {
        "requests": {
            "total": len(latencies),
            "ok": ok_count,
            "failed": fail_count,
        },
        "latency": {
            # P1-2: custom HTTP path reports e2e semantics explicitly.
            "e2e_latency_p95_ms": e2e_p95_ms,
            "e2e_latency_mean_ms": e2e_mean_ms,
            # Keep keys optional/nullable instead of fake zeros.
            "ttft_p95_ms": None,
            "tpot_p95_ms": None,
        },
        "throughput": {
            "req_s": ok_count / elapsed_s,
            "elapsed_s": elapsed_s,
        },
        "workload": {
            "shared_prefix_ratio": shared_prefix_ratio,
            "shared_prefix_len": shared_prefix_len,
            "unique_suffix_len": unique_suffix_len,
            "input_len": input_len,
            "output_len": output_len,
            "concurrency": concurrency,
            "num_requests": num_requests,
        },
        "reproducibility": {
            "base_seed": base_seed,
            "request_seed_strategy": "sha256(base_seed:request_id)",
            "prompt_digest_sha256": _request_digest(prompts_for_digest),
        },
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Minimal custom HTTP benchmark runner")
    p.add_argument("--base-url", required=True)
    p.add_argument("--endpoint", default="/v1/completions")
    p.add_argument("--model", required=True)
    p.add_argument("--num-requests", type=int, default=32)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--base-seed", type=int, default=42)
    p.add_argument("--input-len", type=int, default=128)
    p.add_argument("--output-len", type=int, default=32)
    p.add_argument("--shared-prefix-ratio", type=float, default=0.0)
    p.add_argument("--shared-prefix-len", type=int, default=0)
    p.add_argument("--unique-suffix-len", type=int, default=64)
    p.add_argument("--timeout-s", type=float, default=30.0)
    p.add_argument("--output", default="results/raw/benchmark_summary.json")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(
        run_custom_http(
            base_url=args.base_url,
            endpoint=args.endpoint,
            model=args.model,
            num_requests=args.num_requests,
            concurrency=args.concurrency,
            base_seed=args.base_seed,
            input_len=args.input_len,
            output_len=args.output_len,
            timeout_s=args.timeout_s,
            shared_prefix_ratio=args.shared_prefix_ratio,
            shared_prefix_len=args.shared_prefix_len,
            unique_suffix_len=args.unique_suffix_len,
        )
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
