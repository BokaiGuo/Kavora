from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path


def command_output(command: list[str], fallback: str = "unknown") -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip() or fallback
    except (OSError, subprocess.CalledProcessError):
        return fallback


def build_report(stage1: dict, stage2: dict) -> dict:
    manifest = {
        "schema_version": "kavora-research-manifest/v1",
        "git_revision": command_output(["git", "rev-parse", "HEAD"]),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "gpu": command_output(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]),
        "stage1_config_hash": stage1.get("config_hash", "unknown"),
        "stage2_config_hash": stage2.get("config_hash", "unknown"),
        "stage2_status": stage2.get("status", "unknown"),
        "seed": stage2.get("config", {}).get("seed", 42),
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    report = {
        "schema_version": "kavora-paper-report/v1",
        "generated_at_unix": time.time(),
        "manifest": manifest,
        "manifest_hash": hashlib.sha256(encoded).hexdigest(),
        "research_question": "Can a governed Go/Rust inference control plane preserve OpenAI-compatible behavior while exposing KV-aware routing evidence and safe fallback?",
        "method": {
            "baselines": ["direct backend", "Gateway static path", "Gateway enforced controller path"],
            "metrics": ["throughput_req_s", "ttft_p95_ms", "tail_latency_p99_ms", "error_rate"],
            "quality_boundary": "real measurements, proxy measurements, smoke evidence, and missing/stale signals are reported separately",
        },
        "stage1": stage1,
        "stage2": stage2,
        "limitations": [
            "The current real matrix uses one local vLLM instance and multiple backend identities; it validates control-plane behavior, not independent-replica scaling.",
            "KV-aware performance improvement is not claimed without repeated independent backends and matched workloads.",
            "TTFT and latency are end-to-end measurements, not isolated Go/Rust CPU timings.",
        ],
    }
    return report


def markdown(report: dict) -> str:
    stage2 = report["stage2"]
    lines = [
        "# Kavora Performance Research Report",
        "",
        "## Abstract",
        "",
        report["research_question"],
        "This report evaluates the implemented control plane with reproducible artifacts and keeps mechanism evidence separate from performance claims.",
        "",
        "## Reproducibility Manifest",
        "",
        f"- Manifest hash: `{report['manifest_hash']}`",
        f"- Git revision: `{report['manifest']['git_revision']}`",
        f"- Hardware: `{report['manifest']['gpu']}`",
        f"- Stage 2 status: `{stage2.get('status', 'unknown')}`",
        f"- Seed: `{report['manifest']['seed']}`",
        "",
        "## Method",
        "",
        "| Baseline | Meaning |",
        "|---|---|",
        "| direct backend | vLLM OpenAI endpoint without Kavora |",
        "| Gateway static path | Go Gateway + Rust Policy + static backend candidates |",
        "| Gateway enforced path | Gateway with state-driven candidate preference and safe fallback |",
        "",
        "## Results",
        "",
        "| Path/strategy | Throughput req/s | TTFT p95 ms | p99 ms | Error rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in stage2.get("rows", []):
        def fmt(value): return "n/a" if value is None else f"{value:.3f}"
        lines.append(f"| `{row['strategy']}` | {fmt(row['throughput_req_s'])} | {fmt(row['ttft_p95_ms'])} | {fmt(row['tail_latency_p99_ms'])} | {fmt(row['error_rate'])} |")
    lines += ["", "## Interpretation", "", "The experiment validates the end-to-end path and the enforced controller's safety behavior. It does not establish a causal performance advantage for KV-aware routing.", "", "## Limitations", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage1", required=True)
    parser.add_argument("--stage2", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    stage1 = json.loads(Path(args.stage1).read_text(encoding="utf-8"))
    stage2 = json.loads(Path(args.stage2).read_text(encoding="utf-8"))
    report = build_report(stage1, stage2)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / "research_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "research_report.md").write_text(markdown(report), encoding="utf-8")
    (out / "reproduction_manifest.json").write_text(json.dumps(report["manifest"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out / "research_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
