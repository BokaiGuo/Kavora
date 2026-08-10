from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from planner.ttft_predictor import _candidate, _percentile, _read_jsonl


def validate_predictor(directory: str | Path, artifact_path: str | Path, *, max_mae_ms: float, max_p95_ms: float) -> dict[str, Any]:
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    coefficients = artifact["coefficients"]
    root = Path(directory)
    decisions = {row.get("request_id"): row for row in _read_jsonl(sorted(root.glob("decisions-*.jsonl"))) if row.get("request_id")}
    outcomes = _read_jsonl(sorted(root.glob("outcomes-*.jsonl")))
    errors = []
    signed = []
    skipped = 0
    dimensions = {"model": artifact.get("model"), "gpu_type": artifact.get("gpu_type"), "backend_engine": artifact.get("backend_engine"), "backend_version": artifact.get("backend_version")}
    for outcome in outcomes:
        decision = decisions.get(outcome.get("request_id"))
        candidate = _candidate(decision or {}, str(outcome.get("actual_backend", "")))
        if not decision or not candidate or not outcome.get("success") or any(outcome.get(key) != expected for key, expected in dimensions.items()):
            skipped += 1
            continue
        try:
            prompt = int(outcome["prompt_tokens"])
            matched = int(outcome["observed_matched_tokens"])
            actual = float(outcome["ttft_ms"])
            queue = float(candidate.get("queue_depth", 0))
            pressure = float(candidate.get("kv_pressure", 0))
            predicted = (
                float(coefficients["intercept_ms"])
                + (prompt - matched) * float(coefficients["uncached_token_ms"])
                + matched * float(coefficients["cached_token_ms"])
                + queue * float(coefficients["queue_penalty_ms"])
                + pressure * float(coefficients["kv_pressure_penalty_ms"])
            )
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if prompt <= 0 or matched < 0 or matched > prompt or actual <= 0 or not all(math.isfinite(value) for value in (actual, predicted)):
            skipped += 1
            continue
        signed.append(actual - predicted)
        errors.append(abs(actual - predicted))
    if not errors:
        raise ValueError("no eligible held-out outcome samples")
    mae = sum(errors) / len(errors)
    p95 = _percentile(errors, .95)
    passed = mae <= max_mae_ms and p95 <= max_p95_ms
    return {
        "schema_version": "kavora-ttft-validation/v1",
        "predictor_version": artifact["predictor_version"],
        "samples": len(errors),
        "skipped_samples": skipped,
        "mae_ms": mae,
        "p95_absolute_error_ms": p95,
        "mean_signed_error_ms": sum(signed) / len(signed),
        "thresholds": {"max_mae_ms": max_mae_ms, "max_p95_absolute_error_ms": max_p95_ms},
        "status": "HELD_OUT_PASS" if passed else "HELD_OUT_FAIL",
        "claim_boundary": "Validation uses an independent journal directory and does not reuse the fitting samples. Production generalization remains scoped to the recorded model, GPU, engine, and backend version.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join([
        "# Kavora Held-Out TTFT Predictor Validation",
        "",
        f"- Predictor: `{report['predictor_version']}`",
        f"- Status: **{report['status']}**",
        f"- Samples: `{report['samples']}`",
        f"- MAE: `{report['mae_ms']:.2f} ms`",
        f"- P95 absolute error: `{report['p95_absolute_error_ms']:.2f} ms`",
        f"- Mean signed error: `{report['mean_signed_error_ms']:.2f} ms`",
        "",
        "## Claim Boundary",
        "",
        report["claim_boundary"],
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a fitted TTFT predictor on an independent outcome journal")
    parser.add_argument("--input", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--max-mae-ms", type=float, default=25)
    parser.add_argument("--max-p95-ms", type=float, default=50)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    result = validate_predictor(args.input, args.artifact, max_mae_ms=args.max_mae_ms, max_p95_ms=args.max_p95_ms)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_markdown(result), encoding="utf-8")
    print(f"wrote {output} and {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
