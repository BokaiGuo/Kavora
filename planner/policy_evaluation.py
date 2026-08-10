from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


def _read_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bootstrap_difference(control: list[float], treatment: list[float], samples: int, seed: int) -> tuple[float, float]:
    generator = random.Random(seed)
    differences = []
    for _ in range(samples):
        control_mean = statistics.mean(generator.choice(control) for _ in control)
        treatment_mean = statistics.mean(generator.choice(treatment) for _ in treatment)
        differences.append(treatment_mean - control_mean)
    return float(_percentile(differences, .025) or 0), float(_percentile(differences, .975) or 0)


def _window_means(rows: list[dict[str, Any]]) -> list[float]:
    windows: dict[str, list[float]] = {}
    for row in rows:
        ttft = float(row["outcome"].get("ttft_ms", 0))
        if ttft <= 0:
            continue
        window = str(row["decision"].get("experiment_window", ""))
        windows.setdefault(window, []).append(ttft)
    return [statistics.mean(values) for values in windows.values() if values]


def _arm_metrics(rows: list[dict[str, Any]], slo_ms: float) -> dict[str, Any]:
    ttfts = [float(row["outcome"].get("ttft_ms", 0)) for row in rows if float(row["outcome"].get("ttft_ms", 0)) > 0]
    successes = [row for row in rows if bool(row["outcome"].get("success", False))]
    timestamps = []
    for row in rows:
        value = row["outcome"].get("completed_at")
        if isinstance(value, str):
            try:
                timestamps.append(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
    elapsed = max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0
    prediction_errors = [
        float(row["decision"].get("prediction_error", {}).get("ttft_absolute_error_ms", 0))
        for row in rows
        if row["decision"].get("prediction_error")
    ]
    return {
        "requests": len(rows),
        "successful_requests": len(successes),
        "ttft_samples": len(ttfts),
        "ttft_mean_ms": statistics.mean(ttfts) if ttfts else None,
        "ttft_p95_ms": _percentile(ttfts, .95),
        "slo_violation_rate": sum(value > slo_ms for value in ttfts) / len(ttfts) if ttfts else None,
        "throughput_req_s": len(rows) / elapsed if elapsed > 0 else None,
        "error_rate": 1 - len(successes) / len(rows) if rows else None,
        "fallback_rate": sum(bool(row["decision"].get("fallback", False)) for row in rows) / len(rows) if rows else None,
        "prediction_mae_ms": statistics.mean(prediction_errors) if prediction_errors else None,
    }


def _strata(rows: list[dict[str, Any]], control: str, treatment: str, slo_ms: float) -> list[dict[str, Any]]:
    definitions = {
        "short_prompt": lambda row: int(row["outcome"].get("prompt_tokens", 0)) <= 1024,
        "long_prompt": lambda row: int(row["outcome"].get("prompt_tokens", 0)) > 1024,
        "low_reuse": lambda row: float(row["outcome"].get("observed_cache_hit_ratio", 0) or 0) < .5,
        "high_reuse": lambda row: float(row["outcome"].get("observed_cache_hit_ratio", 0) or 0) >= .5,
        "low_load": lambda row: _actual_queue(row) < 1,
        "high_load": lambda row: _actual_queue(row) >= 1,
    }
    output = []
    for name, predicate in definitions.items():
        selected = [row for row in rows if predicate(row)]
        control_rows = [row for row in selected if row["decision"].get("assigned_policy") == control]
        treatment_rows = [row for row in selected if row["decision"].get("assigned_policy") == treatment]
        control_ttft = [float(row["outcome"].get("ttft_ms", 0)) for row in control_rows if float(row["outcome"].get("ttft_ms", 0)) > 0]
        treatment_ttft = [float(row["outcome"].get("ttft_ms", 0)) for row in treatment_rows if float(row["outcome"].get("ttft_ms", 0)) > 0]
        output.append({
            "stratum": name,
            "control_requests": len(control_rows),
            "treatment_requests": len(treatment_rows),
            "ttft_mean_difference_ms": statistics.mean(treatment_ttft) - statistics.mean(control_ttft) if control_ttft and treatment_ttft else None,
            "control_slo_violation_rate": _arm_metrics(control_rows, slo_ms)["slo_violation_rate"],
            "treatment_slo_violation_rate": _arm_metrics(treatment_rows, slo_ms)["slo_violation_rate"],
        })
    return output


def _actual_queue(row: dict[str, Any]) -> float:
    actual = row["outcome"].get("actual_backend")
    for candidate in row["decision"].get("candidates", []):
        if candidate.get("backend_id") == actual:
            return float(candidate.get("queue_depth", 0))
    return 0


def evaluate_experiment(
    directory: str | Path,
    *,
    experiment_id: str,
    control_policy: str,
    treatment_policy: str,
    slo_ms: float,
    min_requests: int,
    bootstrap_samples: int,
    seed: int,
    guardrails: dict[str, float],
) -> dict[str, Any]:
    root = Path(directory)
    decisions = {row.get("request_id"): row for row in _read_jsonl(sorted(root.glob("decisions-*.jsonl"))) if row.get("experiment_id") == experiment_id}
    outcomes = {row.get("request_id"): row for row in _read_jsonl(sorted(root.glob("outcomes-*.jsonl"))) if row.get("request_id")}
    joined = [{"decision": decision, "outcome": outcomes[request_id]} for request_id, decision in decisions.items() if request_id in outcomes]
    guarded = [row for row in joined if row["decision"].get("warmup") or row["decision"].get("carryover_guard")]
    eligible = [row for row in joined if row not in guarded]
    control_rows = [row for row in eligible if row["decision"].get("assigned_policy") == control_policy]
    treatment_rows = [row for row in eligible if row["decision"].get("assigned_policy") == treatment_policy]
    control_ttft = [float(row["outcome"].get("ttft_ms", 0)) for row in control_rows if float(row["outcome"].get("ttft_ms", 0)) > 0]
    treatment_ttft = [float(row["outcome"].get("ttft_ms", 0)) for row in treatment_rows if float(row["outcome"].get("ttft_ms", 0)) > 0]
    if not control_ttft or not treatment_ttft:
        raise ValueError("both experiment arms require realized TTFT samples")
    difference = statistics.mean(treatment_ttft) - statistics.mean(control_ttft)
    control_windows = _window_means(control_rows)
    treatment_windows = _window_means(treatment_rows)
    clustered = len(control_windows) > 1 and len(treatment_windows) > 1
    ci_low, ci_high = _bootstrap_difference(control_windows if clustered else control_ttft, treatment_windows if clustered else treatment_ttft, bootstrap_samples, seed)
    control_metrics = _arm_metrics(control_rows, slo_ms)
    treatment_metrics = _arm_metrics(treatment_rows, slo_ms)
    assignment_ok = all(
        0 < float(row["decision"].get("assignment_probability", 0)) <= 1
        and row["decision"].get("assigned_policy") in {control_policy, treatment_policy}
        for row in joined
    )
    windows = {
        control_policy: {row["decision"].get("experiment_window") for row in control_rows},
        treatment_policy: {row["decision"].get("experiment_window") for row in treatment_rows},
    }
    policies_by_window: dict[str, set[str]] = {}
    for row in eligible:
        window = str(row["decision"].get("experiment_window", ""))
        policies_by_window.setdefault(window, set()).add(str(row["decision"].get("assigned_policy", "")))
    contaminated_windows = [window for window, policies in policies_by_window.items() if window != "isolated-pool" and len(policies) > 1]
    window_balance = bool(windows[control_policy] and windows[treatment_policy]) and not contaminated_windows
    safety = {
        "errors": max(control_metrics["error_rate"] or 0, treatment_metrics["error_rate"] or 0) <= guardrails.get("max_error_rate", 1),
        "fallback": max(control_metrics["fallback_rate"] or 0, treatment_metrics["fallback_rate"] or 0) <= guardrails.get("max_fallback_rate", 1),
        "p95_ttft": max(control_metrics["ttft_p95_ms"] or math.inf, treatment_metrics["ttft_p95_ms"] or math.inf) <= guardrails.get("max_p95_ttft_ms", math.inf),
        "prediction_drift": (treatment_metrics["prediction_mae_ms"] or 0) <= guardrails.get("max_prediction_mae_ms", math.inf),
    }
    integrity = {
        "assignment": assignment_ok and bool(control_rows) and bool(treatment_rows),
        "warmup_excluded": len(guarded),
        "window_balance": window_balance,
        "joined_outcomes": len(joined),
    }
    promotion = len(eligible) >= min_requests and all(safety.values()) and integrity["assignment"] and integrity["window_balance"] and ci_high < 0
    return {
        "schema_version": "kavora-policy-evaluation/v1",
        "experiment_id": experiment_id,
        "control_policy": control_policy,
        "treatment_policy": treatment_policy,
        "control": control_metrics,
        "treatment": treatment_metrics,
        "effect": {
            "ttft_mean_difference_ms": difference,
            "ttft_relative_percent": difference / statistics.mean(control_ttft) * 100,
            "ci95_low_ms": ci_low,
            "ci95_high_ms": ci_high,
            "ci_method": "window_cluster_bootstrap" if clustered else "request_bootstrap",
            "slo_violation_delta_percentage_points": ((treatment_metrics["slo_violation_rate"] or 0) - (control_metrics["slo_violation_rate"] or 0)) * 100,
        },
        "safety": {key: "PASS" if value else "FAIL" for key, value in safety.items()},
        "integrity": {**integrity, "assignment": "PASS" if integrity["assignment"] else "FAIL", "window_balance": "PASS" if integrity["window_balance"] else "FAIL", "contaminated_windows": contaminated_windows},
        "strata": _strata(eligible, control_policy, treatment_policy, slo_ms),
        "promotion_eligible": promotion,
        "verdict": "PROMOTION_ELIGIBLE" if promotion else "NOT_ELIGIBLE",
        "claim_boundary": "This report estimates policy effects from outcome-grounded randomized assignments. Shared-resource interference is reduced by switchback or isolated pools but is not assumed to be eliminated.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    effect = report["effect"]
    control = report["control"]
    treatment = report["treatment"]
    lines = [
        "# Kavora Policy Evaluation",
        "",
        f"- Experiment: `{report['experiment_id']}`",
        f"- Control: `{report['control_policy']}` ({control['requests']} requests)",
        f"- Treatment: `{report['treatment_policy']}` ({treatment['requests']} requests)",
        f"- Verdict: **{report['verdict']}**",
        "",
        "## Effect",
        "",
        f"- Mean TTFT difference: `{effect['ttft_mean_difference_ms']:.2f} ms`",
        f"- Relative TTFT difference: `{effect['ttft_relative_percent']:.2f}%`",
        f"- Bootstrap 95% CI: `[{effect['ci95_low_ms']:.2f}, {effect['ci95_high_ms']:.2f}] ms`",
        f"- CI method: `{effect['ci_method']}`",
        f"- SLO violation delta: `{effect['slo_violation_delta_percentage_points']:.2f} pp`",
        "",
        "## Safety",
        "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in report["safety"].items())
    lines.extend(["", "## Integrity", ""])
    lines.extend(f"- {key}: `{value}`" for key, value in report["integrity"].items())
    lines.extend(["", "## Claim Boundary", "", report["claim_boundary"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a Kavora online policy experiment")
    parser.add_argument("--input", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--treatment", required=True)
    parser.add_argument("--slo-ms", type=float, default=500)
    parser.add_argument("--min-requests", type=int, default=5000)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-error-rate", type=float, default=.01)
    parser.add_argument("--max-fallback-rate", type=float, default=.02)
    parser.add_argument("--max-p95-ttft-ms", type=float, default=500)
    parser.add_argument("--max-prediction-mae-ms", type=float, default=30)
    parser.add_argument("--out", default="results/experiments/policy-evaluation.json")
    parser.add_argument("--report", default="results/experiments/policy-evaluation.md")
    args = parser.parse_args()
    result = evaluate_experiment(
        args.input,
        experiment_id=args.experiment_id,
        control_policy=args.control,
        treatment_policy=args.treatment,
        slo_ms=args.slo_ms,
        min_requests=args.min_requests,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
        guardrails={"max_error_rate": args.max_error_rate, "max_fallback_rate": args.max_fallback_rate, "max_p95_ttft_ms": args.max_p95_ttft_ms, "max_prediction_mae_ms": args.max_prediction_mae_ms},
    )
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
