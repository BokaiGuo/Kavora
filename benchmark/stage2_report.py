from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _estimate(metric: dict[str, Any]) -> str:
    mean = metric.get("mean")
    low = metric.get("ci95_low")
    high = metric.get("ci95_high")
    if mean is None:
        return "n/a"
    if low is None or high is None:
        return _fmt(mean)
    return f"{_fmt(mean)} [{_fmt(low)}, {_fmt(high)}]"


def render(report: dict[str, Any]) -> str:
    manifest = report["manifest"]
    backend_ids = [backend["id"] if isinstance(backend, dict) else str(backend) for backend in manifest["backends"]]
    lines = [
        "# Kavora Stage 2 Real-Backend Evaluation",
        "",
        f"- Status: `{report['status']}`",
        f"- Config hash: `{report['config_hash']}`",
        f"- Model: `{manifest['model']}`",
        f"- Model revision: `{manifest['model_revision']}`",
        f"- Backend version: `{manifest['backend_version']}`",
        f"- Repetitions: `{manifest['repetitions']}`",
        f"- Backends: `{', '.join(backend_ids)}`",
        f"- GPU: `{manifest['gpu']}`",
        "",
        "Values with brackets are mean and 95% confidence interval across repetitions.",
        "",
        "| Strategy | Workload | TTFT p50 ms | TTFT p95 ms | TTFT p99 ms | E2E p95 ms | Throughput req/s | Error rate | KV reuse | KV util | Queue | Switches | Fallbacks |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in report["results"]:
        aggregate = result["aggregate"]
        lines.append(
            "| `{strategy}` | `{workload}` | {ttft50} | {ttft95} | {ttft99} | {e2e95} | {throughput} | {errors} | {reuse} | {util} | {queue} | {switches} | {fallbacks} |".format(
                strategy=result["strategy"],
                workload=result["workload"],
                ttft50=_estimate(aggregate["ttft_p50_ms"]),
                ttft95=_estimate(aggregate["ttft_p95_ms"]),
                ttft99=_estimate(aggregate["ttft_p99_ms"]),
                e2e95=_estimate(aggregate["latency_p95_ms"]),
                throughput=_estimate(aggregate["throughput_req_s"]),
                errors=_estimate(aggregate["error_rate"]),
                reuse=_estimate(aggregate["kv_reuse_rate"]),
                util=_estimate(aggregate["gpu_kv_utilization_mean"]),
                queue=_estimate(aggregate["queue_depth_mean"]),
                switches=aggregate["route_switches"],
                fallbacks=aggregate["fallback_count"],
            )
        )
    lines.extend(["", "## Gateway Overhead", "", "| Strategy | Workload | TTFT p95 overhead ms | E2E p95 overhead ms |", "|---|---|---:|---:|"])
    for comparison in report.get("comparisons", []):
        lines.append(
            f"| `{comparison['strategy']}` | `{comparison['workload']}` | {_fmt(comparison['gateway_ttft_p95_overhead_ms'])} | {_fmt(comparison['gateway_latency_p95_overhead_ms'])} |"
        )
    lines.extend([
        "",
        "## Claim Boundary",
        "",
        report["claim_boundary"],
        "",
        "A missing KV metric is reported as `n/a`; it is never converted to zero. Shadow routing is evaluated as an observation mode, not described as enforced traffic placement.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the Kavora Stage 2 real-backend report")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(report), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
