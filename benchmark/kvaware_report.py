from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); parser.add_argument("--out", required=True); args = parser.parse_args()
    report = json.loads(Path(args.input).read_text())
    lines = ["# Kavora Stage 2 KV-aware Routing", "", f"- Status: `{report['status']}`", f"- Config hash: `{report['config_hash']}`", "", "| Strategy | Throughput req/s | TTFT p95 ms | p99 ms | Error rate | KV hit ratio | Switches |", "|---|---:|---:|---:|---:|---:|---:|"]
    for row in report["rows"]:
        fmt = lambda value: "n/a" if value is None else f"{value:.3f}"
        lines.append(f"| `{row['strategy']}` | {fmt(row['throughput_req_s'])} | {fmt(row['ttft_p95_ms'])} | {fmt(row['tail_latency_p99_ms'])} | {fmt(row['error_rate'])} | {fmt(row['kv_hit_ratio'])} | {row['route_switches'] if row['route_switches'] is not None else 'n/a'} |")
    lines += ["", "## Promotion boundary", "", report["promotion"]["reason"], ""]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True); Path(args.out).write_text("\n".join(lines), encoding="utf-8"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
