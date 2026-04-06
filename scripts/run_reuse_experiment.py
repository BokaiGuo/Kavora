from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from benchmark.collect import fetch_exporter_metrics
from benchmark.runner import run_custom_http


def _scenario_config(name: str) -> dict[str, Any]:
    if name == "high_reuse":
        return {
            "shared_prefix_ratio": 0.95,
            "shared_prefix_len": 256,
            "unique_suffix_len": 64,
        }
    return {
        "shared_prefix_ratio": 0.0,
        "shared_prefix_len": 0,
        "unique_suffix_len": 256,
    }


def _aggregate(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {"repeats": 0}
    req_s = [float(e["summary"]["throughput"]["req_s"]) for e in entries]
    e2e = [float(e["summary"]["latency"]["e2e_latency_p95_ms"]) for e in entries]
    hit = [float(e.get("exporter_metrics", {}).get("kvcache_kv_cache_hit_ratio", 0.0)) for e in entries]
    hidden = [float(e.get("exporter_metrics", {}).get("kvcache_kv_hidden_reuse_ready_perc", 0.0)) for e in entries]
    return {
        "repeats": len(entries),
        "req_s_mean": sum(req_s) / len(req_s),
        "e2e_p95_ms_mean": sum(e2e) / len(e2e),
        "hit_ratio_mean": sum(hit) / len(hit),
        "hidden_reuse_mean": sum(hidden) / len(hidden),
    }


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reuse Experiment Report",
        "",
        f"- base_url: `{report['meta']['base_url']}`",
        f"- exporter_metrics_url: `{report['meta']['exporter_metrics_url']}`",
        f"- model: `{report['meta']['model']}`",
        f"- repeats: `{report['meta']['repeats']}`",
        "",
        "| Scenario | Repeat | req/s | e2e_p95_ms | hit_ratio | hidden_reuse | ok/total |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in ("low_reuse", "high_reuse"):
        for item in report["runs"][scenario]:
            s = item["summary"]
            ok = int(s["requests"]["ok"])
            total = int(s["requests"]["total"])
            lines.append(
                "| {scenario} | {repeat} | {req:.4f} | {e2e:.2f} | {hit:.4f} | {hidden:.4f} | {ok}/{total} |".format(
                    scenario=scenario,
                    repeat=item["repeat"],
                    req=float(s["throughput"]["req_s"]),
                    e2e=float(s["latency"]["e2e_latency_p95_ms"]),
                    hit=float(item.get("exporter_metrics", {}).get("kvcache_kv_cache_hit_ratio", 0.0)),
                    hidden=float(item.get("exporter_metrics", {}).get("kvcache_kv_hidden_reuse_ready_perc", 0.0)),
                    ok=ok,
                    total=total,
                )
            )
    lines.extend(
        [
            "",
            "## Aggregates",
            "",
            "| Scenario | req/s mean | e2e_p95_ms mean | hit_ratio mean | hidden_reuse mean |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for scenario in ("low_reuse", "high_reuse"):
        agg = report["aggregates"][scenario]
        lines.append(
            "| {scenario} | {req:.4f} | {e2e:.2f} | {hit:.4f} | {hidden:.4f} |".format(
                scenario=scenario,
                req=float(agg.get("req_s_mean", 0.0)),
                e2e=float(agg.get("e2e_p95_ms_mean", 0.0)),
                hit=float(agg.get("hit_ratio_mean", 0.0)),
                hidden=float(agg.get("hidden_reuse_mean", 0.0)),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run low/high reuse local experiment")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--endpoint", default="/v1/completions")
    ap.add_argument("--model", required=True)
    ap.add_argument("--exporter-metrics-url", default="http://localhost:9108/metrics")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--num-requests", type=int, default=80)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--input-len", type=int, default=320)
    ap.add_argument("--output-len", type=int, default=48)
    ap.add_argument("--timeout-s", type=float, default=30.0)
    ap.add_argument("--base-seed", type=int, default=42)
    ap.add_argument("--out-dir", default="results/experiments/reuse_local")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    runs: dict[str, list[dict[str, Any]]] = {"low_reuse": [], "high_reuse": []}
    for scenario in ("low_reuse", "high_reuse"):
        cfg = _scenario_config(scenario)
        for r in range(1, args.repeats + 1):
            summary = asyncio.run(
                run_custom_http(
                    base_url=args.base_url,
                    endpoint=args.endpoint,
                    model=args.model,
                    num_requests=args.num_requests,
                    concurrency=args.concurrency,
                    base_seed=args.base_seed + r * 1000,
                    input_len=args.input_len,
                    output_len=args.output_len,
                    timeout_s=args.timeout_s,
                    shared_prefix_ratio=float(cfg["shared_prefix_ratio"]),
                    shared_prefix_len=int(cfg["shared_prefix_len"]),
                    unique_suffix_len=int(cfg["unique_suffix_len"]),
                )
            )
            try:
                exporter_metrics = fetch_exporter_metrics(args.exporter_metrics_url)
            except Exception:
                exporter_metrics = {}
            runs[scenario].append({"repeat": r, "summary": summary, "exporter_metrics": exporter_metrics})

    report = {
        "meta": {
            "base_url": args.base_url,
            "endpoint": args.endpoint,
            "model": args.model,
            "exporter_metrics_url": args.exporter_metrics_url,
            "repeats": args.repeats,
            "num_requests": args.num_requests,
            "concurrency": args.concurrency,
            "input_len": args.input_len,
            "output_len": args.output_len,
            "timeout_s": args.timeout_s,
        },
        "runs": runs,
        "aggregates": {
            "low_reuse": _aggregate(runs["low_reuse"]),
            "high_reuse": _aggregate(runs["high_reuse"]),
        },
    }

    (out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_to_markdown(report), encoding="utf-8")
    print(f"[reuse-experiment] wrote {out_dir / 'summary.json'}")
    print(f"[reuse-experiment] wrote {out_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
