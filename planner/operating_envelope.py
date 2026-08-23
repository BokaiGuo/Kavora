from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _point_resource(point: dict[str, Any]) -> float | None:
    aggregates = point.get("aggregates", {})
    resource = aggregates.get("resource", {}) if isinstance(aggregates, dict) else {}
    return _number(
        aggregates.get("gpu_seconds_mean")
        if isinstance(aggregates, dict)
        else None
    ) or _number(resource.get("gpu_seconds_mean") if isinstance(resource, dict) else None)


def _point_goodput(point: dict[str, Any]) -> tuple[float | None, float | None]:
    aggregates = point.get("aggregates", {})
    if not isinstance(aggregates, dict):
        return None, None
    mean = _number(aggregates.get("goodput_req_s_mean"))
    lower = _number(aggregates.get("goodput_req_s_lcb"))
    if lower is None and mean is not None:
        lower = mean
    return mean, lower


def recommend_operating_point(
    document: dict[str, Any],
    *,
    e2e_p95_slo_ms: float,
    min_success_rate: float = 0.99,
    resource_budget_gpu_seconds: float | None = None,
    safety_factor: float = 0.9,
) -> dict[str, Any]:
    evaluated: list[dict[str, Any]] = []
    for point in document.get("points", []):
        if not isinstance(point, dict):
            continue
        aggregates = point.get("aggregates", {})
        quality = aggregates.get("quality_summary", {}) if isinstance(aggregates, dict) else {}
        goodput_mean, goodput_lcb = _point_goodput(point)
        e2e_p95 = _number(aggregates.get("e2e_p95_ms_mean")) if isinstance(aggregates, dict) else None
        success_rate = _number(aggregates.get("success_rate_mean")) if isinstance(aggregates, dict) else None
        resource = _point_resource(point)
        reasons: list[str] = []
        if goodput_mean is None:
            reasons.append("goodput_missing")
        if e2e_p95 is None or e2e_p95 > e2e_p95_slo_ms:
            reasons.append("e2e_p95_slo")
        if success_rate is not None and success_rate < min_success_rate:
            reasons.append("success_rate")
        if str(quality.get("metric_quality", "missing")) in {"missing", "stale", "invalid"}:
            reasons.append("evidence_quality")
        if resource_budget_gpu_seconds is not None:
            if resource is None:
                reasons.append("resource_missing")
            elif resource > resource_budget_gpu_seconds:
                reasons.append("resource_budget")
        evaluated.append(
            {
                "scenario": point.get("scenario", "unknown"),
                "concurrency": int(point.get("concurrency", 0)),
                "goodput_req_s_mean": goodput_mean,
                "goodput_req_s_lcb": goodput_lcb,
                "e2e_p95_ms_mean": e2e_p95,
                "success_rate_mean": success_rate,
                "gpu_seconds_mean": resource,
                "evidence_quality": quality.get("metric_quality", "missing"),
                "feasible": not reasons,
                "rejection_reasons": reasons,
            }
        )

    feasible = [item for item in evaluated if item["feasible"]]
    feasible.sort(
        key=lambda item: (
            float(item["goodput_req_s_lcb"]),
            float(item["goodput_req_s_mean"]),
            -int(item["concurrency"]),
        ),
        reverse=True,
    )
    selected = feasible[0] if feasible else None
    if selected is not None:
        selected = dict(selected)
        selected["recommended_goodput_req_s"] = float(selected["goodput_req_s_lcb"]) * safety_factor

    pareto: list[dict[str, Any]] = []
    for candidate in feasible:
        dominated = any(
            other is not candidate
            and float(other["goodput_req_s_lcb"]) >= float(candidate["goodput_req_s_lcb"])
            and (
                candidate["gpu_seconds_mean"] is None
                or other["gpu_seconds_mean"] is None
                or float(other["gpu_seconds_mean"]) <= float(candidate["gpu_seconds_mean"])
            )
            and (
                float(other["goodput_req_s_lcb"]) > float(candidate["goodput_req_s_lcb"])
                or (
                    candidate["gpu_seconds_mean"] is not None
                    and other["gpu_seconds_mean"] is not None
                    and float(other["gpu_seconds_mean"]) < float(candidate["gpu_seconds_mean"])
                )
            )
            for other in feasible
        )
        if not dominated:
            pareto.append(candidate)

    return {
        "schema_version": "kavora-operating-envelope/v1",
        "constraints": {
            "e2e_p95_slo_ms": e2e_p95_slo_ms,
            "min_success_rate": min_success_rate,
            "resource_budget_gpu_seconds": resource_budget_gpu_seconds,
            "safety_factor": safety_factor,
        },
        "points": evaluated,
        "pareto_points": pareto,
        "recommendation": selected,
        "status": "recommended" if selected else "blocked",
        "claim_boundary": "Recommendations are valid only for the supplied model, hardware, workload, pool, SLO, and resource evidence window. Missing goodput or resource evidence is not imputed.",
    }


def render_markdown(result: dict[str, Any]) -> str:
    recommendation = result.get("recommendation")
    lines = [
        "# Kavora Operating Envelope",
        "",
        f"- Status: **{result['status']}**",
        f"- SLO: `{result['constraints']['e2e_p95_slo_ms']} ms` e2e p95",
        f"- Resource budget: `{result['constraints']['resource_budget_gpu_seconds'] if result['constraints']['resource_budget_gpu_seconds'] is not None else 'not configured'}` GPU-seconds",
        "",
        "## Recommendation",
        "",
    ]
    if recommendation:
        lines.append(
            f"- scenario `{recommendation['scenario']}`, concurrency `{recommendation['concurrency']}`, recommended goodput `{recommendation['recommended_goodput_req_s']:.4f}` req/s."
        )
    else:
        lines.append("- No point passed goodput, SLO, evidence, and resource gates.")
    lines.extend(["", "## Pareto Points", "", "| Scenario | Concurrency | Goodput LCB | GPU-seconds | E2E p95 |", "|---|---:|---:|---:|---:|"])
    for point in result["pareto_points"]:
        lines.append(
            f"| {point['scenario']} | {point['concurrency']} | {point['goodput_req_s_lcb']:.4f} | {point['gpu_seconds_mean'] if point['gpu_seconds_mean'] is not None else 'missing'} | {point['e2e_p95_ms_mean']:.2f} |"
        )
    lines.extend(["", "## Claim Boundary", "", result["claim_boundary"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recommend a SLO-efficient Kavora operating point")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", default="results/capacity_sweeps/operating-envelope.json", type=Path)
    parser.add_argument("--report", default="results/capacity_sweeps/operating-envelope.md", type=Path)
    parser.add_argument("--e2e-p95-slo-ms", type=float, default=1500)
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    parser.add_argument("--resource-budget-gpu-seconds", type=float, default=None)
    parser.add_argument("--safety-factor", type=float, default=0.9)
    args = parser.parse_args()
    result = recommend_operating_point(
        json.loads(args.input.read_text(encoding="utf-8")),
        e2e_p95_slo_ms=args.e2e_p95_slo_ms,
        min_success_rate=args.min_success_rate,
        resource_budget_gpu_seconds=args.resource_budget_gpu_seconds,
        safety_factor=args.safety_factor,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    args.report.write_text(render_markdown(result), encoding="utf-8")
    print(f"wrote {args.out} and {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
