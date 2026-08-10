from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from benchmark.window_metrics import get_entry_evidence_quality, get_entry_hit_ratio_quality_and_source

SCHEMA_VERSION = "kavora-auto-calibration/v1"
DEFAULT_THRESHOLDS = tuple(round(index * 0.05, 2) for index in range(21))
EVIDENCE_UNCERTAINTY = {"strict": 0.0, "estimated": 0.25, "fallback": 0.6, "missing": 1.0}


@dataclass(frozen=True)
class CalibrationConstraints:
    e2e_p95_slo_ms: float
    min_success_rate: float
    min_feasible_runs: int = 3


@dataclass(frozen=True)
class CalibrationWeights:
    instability: float = 0.75
    evidence_uncertainty: float = 0.75
    safety_factor: float = 0.9


def _success_rate(run: dict[str, Any]) -> float:
    requests = run.get("summary", {}).get("requests", {})
    total = float(requests.get("total", 0) or 0)
    return float(requests.get("ok", 0) or 0) / total if total > 0 else 0.0


def _number(run: dict[str, Any], *path: str) -> float:
    value: Any = run
    for key in path:
        value = value.get(key, {}) if isinstance(value, dict) else {}
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _quality_name(qualities: Iterable[str]) -> str:
    values = set(qualities)
    if not values:
        return "missing"
    if len(values) == 1:
        return values.pop()
    return "mixed"


def _evaluate_candidate(
    point: dict[str, Any],
    threshold: float,
    constraints: CalibrationConstraints,
    weights: CalibrationWeights,
) -> dict[str, Any]:
    feasible: list[dict[str, Any]] = []
    rejected_counts = {"slo": 0, "success_rate": 0, "hit_ratio": 0, "metric_quality": 0, "evidence_missing": 0}
    for run in point.get("runs", []):
        hit_ratio, metric_quality, _ = get_entry_hit_ratio_quality_and_source(run)
        evidence_quality = get_entry_evidence_quality(run)
        e2e = _number(run, "summary", "latency", "e2e_latency_p95_ms")
        success_rate = _success_rate(run)
        if e2e > constraints.e2e_p95_slo_ms:
            rejected_counts["slo"] += 1
            continue
        if success_rate < constraints.min_success_rate:
            rejected_counts["success_rate"] += 1
            continue
        if metric_quality != "ok":
            rejected_counts["metric_quality"] += 1
            continue
        if hit_ratio is None or hit_ratio < threshold:
            rejected_counts["hit_ratio"] += 1
            continue
        if evidence_quality == "missing":
            rejected_counts["evidence_missing"] += 1
            continue
        feasible.append({"rps": _number(run, "summary", "throughput", "req_s"), "hit_ratio": hit_ratio, "evidence_quality": evidence_quality})

    rejection_reasons: list[str] = []
    if len(feasible) < constraints.min_feasible_runs:
        rejection_reasons.append("insufficient_feasible_runs")
    if not feasible:
        return {
            "scenario": point.get("scenario", "unknown"), "concurrency": int(point.get("concurrency", 0)), "min_hit_ratio": threshold,
            "feasible_runs": 0, "total_runs": len(point.get("runs", [])), "score": None, "expected_rps": 0.0,
            "rps_lower_bound": 0.0, "instability": 1.0, "evidence_uncertainty": 1.0, "evidence_quality": "missing",
            "rejection_reasons": rejection_reasons, "rejected_counts": rejected_counts, "selected": False,
        }

    rps_values = [item["rps"] for item in feasible]
    mean_rps = statistics.mean(rps_values)
    stddev = statistics.stdev(rps_values) if len(rps_values) > 1 else 0.0
    standard_error = stddev / math.sqrt(len(rps_values))
    lower_bound = max(0.0, mean_rps - 1.96 * standard_error)
    instability = stddev / mean_rps if mean_rps > 0 else 1.0
    evidence_uncertainty = statistics.mean(EVIDENCE_UNCERTAINTY[item["evidence_quality"]] for item in feasible)
    score = lower_bound - weights.instability * mean_rps * instability - weights.evidence_uncertainty * mean_rps * evidence_uncertainty
    if evidence_uncertainty > 0:
        rejection_reasons.append("evidence_uncertainty")
    return {
        "scenario": point.get("scenario", "unknown"), "concurrency": int(point.get("concurrency", 0)), "min_hit_ratio": threshold,
        "feasible_runs": len(feasible), "total_runs": len(point.get("runs", [])), "mean_rps": mean_rps,
        "rps_lower_bound": lower_bound, "expected_rps": lower_bound * weights.safety_factor, "instability": instability,
        "evidence_uncertainty": evidence_uncertainty, "evidence_quality": _quality_name(item["evidence_quality"] for item in feasible),
        "score": score, "rejection_reasons": rejection_reasons, "rejected_counts": rejected_counts, "selected": False,
    }


def calibrate(
    points: list[dict[str, Any]],
    *,
    constraints: CalibrationConstraints,
    weights: CalibrationWeights | None = None,
    thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    weights = weights or CalibrationWeights()
    candidates = [
        _evaluate_candidate(point, float(threshold), constraints, weights)
        for point in points
        for threshold in thresholds
    ]
    selectable = [
        candidate
        for candidate in candidates
        if candidate["score"] is not None and candidate["feasible_runs"] >= constraints.min_feasible_runs
    ]
    selectable.sort(
        key=lambda candidate: (
            candidate["score"],
            -candidate["evidence_uncertainty"],
            candidate["min_hit_ratio"],
            -candidate["concurrency"],
        ),
        reverse=True,
    )
    selected = selectable[0] if selectable else None
    if selected is not None:
        selected["selected"] = True
        selected["rejection_reasons"] = []
    for candidate in candidates:
        if (
            not candidate["selected"]
            and candidate["score"] is not None
            and candidate["feasible_runs"] >= constraints.min_feasible_runs
            and "lower_robust_score" not in candidate["rejection_reasons"]
        ):
            candidate["rejection_reasons"].append("lower_robust_score")
    baseline_candidates = [candidate for candidate in candidates if candidate["min_hit_ratio"] == 0 and candidate["score"] is not None]
    baseline_rps = max((float(candidate["expected_rps"]) for candidate in baseline_candidates), default=0.0)
    expected_rps = float(selected["expected_rps"]) if selected else 0.0
    advantage = (expected_rps / baseline_rps - 1) if baseline_rps > 0 else 0.0
    if selected:
        sample_confidence = min(1.0, selected["feasible_runs"] / max(constraints.min_feasible_runs * 2, 1))
        confidence = sample_confidence * max(0.0, 1 - selected["instability"]) * max(0.0, 1 - selected["evidence_uncertainty"])
        recommendation = {
            "min_hit_ratio": selected["min_hit_ratio"], "max_concurrency": selected["concurrency"],
            "expected_rps": round(expected_rps, 6), "confidence": round(confidence, 6),
            "evidence_quality": selected["evidence_quality"], "scenario": selected["scenario"],
        }
        reason = [
            f"{selected['feasible_runs']} feasible experiment runs",
            f"{selected['evidence_quality']} cache evidence",
            f"RPS lower-bound {selected['rps_lower_bound']:.3f}; robust score {selected['score']:.3f} after instability and evidence penalties",
            f"{advantage * 100:.1f}% expected throughput advantage over the threshold-free baseline",
        ]
        deployment = {"status": "human_approval_required", "recommended_action": "replay_then_canary", "canary_steps": [0.05, 0.25, 0.5, 1.0]}
    else:
        recommendation = None
        reason = ["no threshold/concurrency pair met the SLO, success-rate, evidence, and minimum-run gates"]
        deployment = {"status": "blocked", "recommended_action": "collect_more_evidence", "canary_steps": []}
    return {
        "schema_version": SCHEMA_VERSION,
        "recommendation": recommendation,
        "constraints": {"e2e_p95_ms": constraints.e2e_p95_slo_ms, "success_rate": constraints.min_success_rate, "min_feasible_runs": constraints.min_feasible_runs},
        "weights": {"instability": weights.instability, "evidence_uncertainty": weights.evidence_uncertainty, "safety_factor": weights.safety_factor},
        "reason": reason,
        "deployment": deployment,
        "alternatives": candidates,
        "claim_boundary": "Calibration ranks measured sweep runs and requires human approval. It does not mutate production configuration or establish performance beyond the supplied experiment artifact.",
    }


def render(result: dict[str, Any]) -> str:
    recommendation = result.get("recommendation")
    lines = ["# Kavora Automatic Operating-Envelope Calibration", ""]
    if recommendation:
        lines.extend([
            f"- Minimum hit ratio: `{recommendation['min_hit_ratio']:.2f}`",
            f"- Maximum concurrency: `{recommendation['max_concurrency']}`",
            f"- Expected safe RPS: `{recommendation['expected_rps']:.3f}`",
            f"- Confidence: `{recommendation['confidence']:.3f}`",
            f"- Evidence quality: `{recommendation['evidence_quality']}`",
        ])
    else:
        lines.append("- Recommendation: `BLOCKED`")
    lines.extend(["", "## Reasons", ""] + [f"- {reason}" for reason in result["reason"]])
    lines.extend(["", "## Deployment", "", f"- Status: `{result['deployment']['status']}`", f"- Next action: `{result['deployment']['recommended_action']}`", "", "## Claim Boundary", "", result["claim_boundary"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate a safe cache threshold and concurrency from a capacity sweep")
    parser.add_argument("--input", required=True, help="capacity sweep summary.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--report")
    parser.add_argument("--e2e-p95-slo-ms", type=float, default=500)
    parser.add_argument("--min-success-rate", type=float, default=.995)
    parser.add_argument("--min-feasible-runs", type=int, default=3)
    args = parser.parse_args()
    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = calibrate(source.get("points", []), constraints=CalibrationConstraints(args.e2e_p95_slo_ms, args.min_success_rate, args.min_feasible_runs))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(render(result), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
