from __future__ import annotations

from typing import Any


def render(artifact: dict[str, Any]) -> str:
    manifest = artifact["manifest"]
    lines = [
        "# Kavora Cache-Evidence Fidelity and Lag Ablation",
        "",
        f"- Status: `{artifact['status']}`",
        f"- Requests: `{manifest['requests']}`",
        f"- Backends: `{manifest['backends']}`",
        f"- Seed: `{manifest['seed']}`",
        "",
        "| Fidelity | Lag ms | Accuracy | Reuse | TTFT ms | Throughput proxy | Decision us | CPU ns | State bytes | Bandwidth B/s | Imbalance CV | Reversal | Fallback | Wrong affinity |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in artifact["results"]:
        lines.append(
            "| `{fidelity}` | {lag_ms} | {routing_accuracy:.3f} | {cache_reuse_ratio:.3f} | {ttft_mean_ms:.2f} | {throughput_proxy_req_s:.2f} | {decision_latency_us:.2f} | {controller_cpu_ns_per_decision:.0f} | {controller_state_bytes} | {state_bandwidth_bytes_s:.1f} | {backend_imbalance_cv:.3f} | {decision_reversal_rate:.3f} | {fallback_rate:.3f} | {wrong_affinity_rate:.3f} |".format(**row)
        )
    lines.extend(["", "## Claim Boundary", "", artifact["claim_boundary"], ""])
    return "\n".join(lines)
