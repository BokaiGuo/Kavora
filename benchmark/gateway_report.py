from __future__ import annotations

import argparse
import json
from pathlib import Path


def render(report: dict) -> str:
    lines = ["# Kavora Stage 1 Gateway Benchmark", "", f"- Schema: `{report['schema_version']}`", f"- Config hash: `{report['config_hash']}`", f"- Environment: `{report['environment']['platform']}` / Python `{report['environment']['python']}`", "", "| Path | OK | Error rate | p50 ms | p95 ms | p99 ms | TTFT p95 ms | req/s | peak RSS MB |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for path in report["paths"]:
        latency = path["latency_ms"]
        ttft = path["ttft_ms"]
        lines.append(f"| `{path['path']}` | {path['ok']}/{path['requests']} | {path['error_rate']:.3f} | {fmt(latency['p50'])} | {fmt(latency['p95'])} | {fmt(latency['p99'])} | {fmt(ttft['p95'])} | {path['throughput_req_s']:.3f} | {report['memory']['peak_rss_mb']:.2f} |")
    lines.extend(["", "## Phase Attribution", "", "```json", json.dumps(report["phase_attribution"], indent=2, sort_keys=True), "```", "", "The phase deltas are end-to-end proxy measurements. They must not be described as isolated Go, RPC, or Rust CPU timings.", ""])
    return "\n".join(lines)


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Kavora Stage 1 benchmark report")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(render(report), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
