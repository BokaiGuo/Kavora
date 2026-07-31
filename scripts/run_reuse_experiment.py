from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.experiment import (
    EXPERIMENT_SCHEMA_VERSION,
    RUN_ENTRY_SCHEMA_VERSION,
    aggregate_run_entries,
    run_windowed_experiment,
    scenario_config,
)
from benchmark.window_metrics import get_entry_hit_ratio_and_quality, get_entry_prefix_metric_check


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reuse Experiment Report",
        "",
        f"- base_url: `{report['meta']['base_url']}`",
        f"- exporter_metrics_url: `{report['meta']['exporter_metrics_url']}`",
        f"- model: `{report['meta']['model']}`",
        f"- repeats: `{report['meta']['repeats']}`",
        "",
        "| Scenario | Repeat | req/s | e2e_p95_ms | hit_ratio_window | metric_quality | prefix_check | hidden_reuse | ok/total |",
        "|---|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for scenario in ("low_reuse", "high_reuse"):
        for item in report["runs"][scenario]:
            s = item["summary"]
            ok = int(s["requests"]["ok"])
            total = int(s["requests"]["total"])
            hit_ratio, metric_quality = get_entry_hit_ratio_and_quality(item)
            hit_ratio_source = str(item.get("quality", {}).get("hit_ratio_source", "missing"))
            prefix_check = get_entry_prefix_metric_check(item)
            hit_text = "NA" if hit_ratio is None else f"{float(hit_ratio):.4f}"
            lines.append(
                "| {scenario} | {repeat} | {req:.4f} | {e2e:.2f} | {hit} | {quality}/{source} | {prefix_check} | {hidden:.4f} | {ok}/{total} |".format(
                    scenario=scenario,
                    repeat=item["repeat"],
                    req=float(s["throughput"]["req_s"]),
                    e2e=float(s["latency"]["e2e_latency_p95_ms"]),
                    hit=hit_text,
                    quality=metric_quality,
                    source=hit_ratio_source,
                    prefix_check=prefix_check,
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
            "| Scenario | req/s mean | e2e_p95_ms mean | hit_ratio mean | hit missing | hit stale | prefix_check | strict runs | token fallback runs | hidden_reuse mean |",
            "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|",
        ]
    )
    for scenario in ("low_reuse", "high_reuse"):
        agg = report["aggregates"][scenario]
        quality = agg.get("quality_summary", {})
        hit_ratio_mean = agg.get("hit_ratio_mean")
        hit_ratio_text = "NA" if hit_ratio_mean is None else f"{float(hit_ratio_mean):.4f}"
        total_runs = int(quality.get("total_runs", 0))
        lines.append(
            "| {scenario} | {req:.4f} | {e2e:.2f} | {hit} | {missing:d} | {stale:d} | {prefix_check} | {strict_runs}/{total_runs} | {token_runs}/{total_runs} | {hidden:.4f} |".format(
                scenario=scenario,
                req=float(agg.get("req_s_mean", 0.0)),
                e2e=float(agg.get("e2e_p95_ms_mean", 0.0)),
                hit=hit_ratio_text,
                missing=int(quality.get("num_runs_missing_hit_ratio", 0)),
                stale=int(quality.get("num_runs_stale_metrics", 0)),
                prefix_check=str(quality.get("prefix_metric_check", "missing")),
                strict_runs=int(quality.get("num_runs_prefix_metric_strict", 0)),
                token_runs=int(quality.get("num_runs_prefix_metric_token_fallback", 0)),
                total_runs=total_runs,
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
    ap.add_argument("--backend-metrics-url", default="")
    ap.add_argument("--exporter-metrics-url", default="http://localhost:9108/metrics")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--num-requests", type=int, default=80)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--input-len", type=int, default=320)
    ap.add_argument("--output-len", type=int, default=48)
    ap.add_argument("--warmup-requests", type=int, default=0)
    ap.add_argument("--timeout-s", type=float, default=30.0)
    ap.add_argument("--base-seed", type=int, default=42)
    ap.add_argument("--out-dir", default="results/experiments/reuse_local")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    backend_metrics_url = args.backend_metrics_url or f"{args.base_url.rstrip('/')}/metrics"

    runs: dict[str, list[dict[str, Any]]] = {"low_reuse": [], "high_reuse": []}
    for scenario in ("low_reuse", "high_reuse"):
        cfg = scenario_config(scenario)
        for r in range(1, args.repeats + 1):
            entry = run_windowed_experiment(
                repeat=r,
                base_url=args.base_url,
                endpoint=args.endpoint,
                model=args.model,
                backend_metrics_url=backend_metrics_url,
                exporter_metrics_url=args.exporter_metrics_url,
                num_requests=args.num_requests,
                concurrency=args.concurrency,
                base_seed=args.base_seed + r * 1000,
                input_len=args.input_len,
                output_len=args.output_len,
                warmup_requests=args.warmup_requests,
                timeout_s=args.timeout_s,
                shared_prefix_ratio=float(cfg["shared_prefix_ratio"]),
                shared_prefix_len=int(cfg["shared_prefix_len"]),
                unique_suffix_len=int(cfg["unique_suffix_len"]),
            )
            runs[scenario].append(entry)

    report = {
        "meta": {
            "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
            "run_entry_schema_version": RUN_ENTRY_SCHEMA_VERSION,
            "base_url": args.base_url,
            "endpoint": args.endpoint,
            "model": args.model,
            "backend_metrics_url": backend_metrics_url,
            "exporter_metrics_url": args.exporter_metrics_url,
            "repeats": args.repeats,
            "num_requests": args.num_requests,
            "concurrency": args.concurrency,
            "input_len": args.input_len,
            "output_len": args.output_len,
            "warmup_requests": args.warmup_requests,
            "timeout_s": args.timeout_s,
        },
        "runs": runs,
        "aggregates": {
            "low_reuse": aggregate_run_entries(runs["low_reuse"]),
            "high_reuse": aggregate_run_entries(runs["high_reuse"]),
        },
    }

    (out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_to_markdown(report), encoding="utf-8")
    print(f"[reuse-experiment] wrote {out_dir / 'summary.json'}")
    print(f"[reuse-experiment] wrote {out_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
