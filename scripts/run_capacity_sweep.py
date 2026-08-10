from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import matplotlib.pyplot as plt

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
from planner.policy import recommend_runs
from planner.auto_calibrator import CalibrationConstraints, CalibrationWeights, calibrate


def _parse_csv_ints(raw: str) -> list[int]:
    vals = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not vals:
        raise ValueError("expected at least one integer value")
    return vals


def _parse_url_host_port(url: str, *, default_port: int) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = int(parsed.port or default_port)
    return host, port


def _build_stack_restart_env(
    *,
    backend: str,
    base_url: str,
    exporter_metrics_url: str,
    results_dir: str,
    served_model_name: str,
    stack_model_path: str = "",
) -> dict[str, str]:
    backend_host, backend_port = _parse_url_host_port(
        base_url,
        default_port=30000 if backend == "sglang" else 8000,
    )
    exporter_host, exporter_port = _parse_url_host_port(exporter_metrics_url, default_port=9108)
    env = dict(os.environ)
    env["RESULTS_DIR"] = results_dir
    env["ONE_CLICK_BACKEND"] = backend
    env["START_EXPORTER"] = "true"
    env["SERVED_MODEL_NAME"] = served_model_name
    # The benchmark MODEL argument is a served model id, but launch scripts
    # interpret MODEL as a filesystem path.
    env["MODEL"] = stack_model_path
    env["HOST"] = backend_host
    env["PORT"] = str(backend_port)
    env["SGLANG_HOST"] = backend_host
    env["SGLANG_PORT"] = str(backend_port)
    env["EXPORTER_HOST"] = exporter_host
    env["EXPORTER_PORT"] = str(exporter_port)
    return env


def _restart_serving_stack(
    *,
    backend: str,
    base_url: str,
    exporter_metrics_url: str,
    results_dir: str,
    served_model_name: str,
    stack_model_path: str = "",
) -> None:
    env = _build_stack_restart_env(
        backend=backend,
        base_url=base_url,
        exporter_metrics_url=exporter_metrics_url,
        results_dir=results_dir,
        served_model_name=served_model_name,
        stack_model_path=stack_model_path,
    )
    print(
        "[capacity-sweep] restarting stack "
        f"backend={backend} base_url={base_url} exporter_metrics_url={exporter_metrics_url}"
    )
    subprocess.run(["bash", str(ROOT / "scripts" / "one_click_down.sh")], cwd=ROOT, env=env, check=True)
    subprocess.run(["bash", str(ROOT / "scripts" / "one_click_up.sh")], cwd=ROOT, env=env, check=True)


def _build_point_recommendation(
    runs: list[dict[str, Any]],
    *,
    e2e_p95_slo_ms: float,
    min_success_rate: float,
    min_hit_ratio: float,
    safety_factor: float,
) -> dict[str, Any]:
    baseline = recommend_runs(
        runs,
        e2e_p95_slo_ms=e2e_p95_slo_ms,
        min_success_rate=min_success_rate,
        min_hit_ratio=None,
        safety_factor=safety_factor,
    )
    dual = recommend_runs(
        runs,
        e2e_p95_slo_ms=e2e_p95_slo_ms,
        min_success_rate=min_success_rate,
        min_hit_ratio=min_hit_ratio,
        safety_factor=safety_factor,
    )
    return {
        "baseline_hard_only_recommended_rps": round(float(baseline["recommended_rps"]), 6),
        "dual_boundary_recommended_rps": round(float(dual["recommended_rps"]), 6),
        "delta_dual_minus_baseline": round(float(dual["recommended_rps"]) - float(baseline["recommended_rps"]), 6),
        "metric_quality": str(dual["metric_quality"]),
        "hit_ratio_source": str(dual["hit_ratio_source"]),
        "num_runs": int(dual["num_runs"]),
        "num_runs_ok_metrics": int(dual["num_runs_ok_metrics"]),
        "num_runs_missing_hit_ratio": int(dual["num_runs_missing_hit_ratio"]),
        "num_runs_stale_metrics": int(dual["num_runs_stale_metrics"]),
        "hit_ratio_comparable": bool(dual["hit_ratio_comparable"]),
        "evidence_quality": str(dual["evidence_quality"]),
    }


def _point_rank_record(point: dict[str, Any]) -> dict[str, Any]:
    quality = point["aggregates"]["quality_summary"]
    recommendation = point["recommendation"]
    baseline_rps = float(recommendation["baseline_hard_only_recommended_rps"])
    dual_rps = float(recommendation["dual_boundary_recommended_rps"])
    return {
        "scenario": point["scenario"],
        "concurrency": int(point["concurrency"]),
        "req_s_mean": float(point["aggregates"].get("req_s_mean", 0.0)),
        "e2e_p95_ms_mean": float(point["aggregates"].get("e2e_p95_ms_mean", 0.0)),
        "hit_ratio_mean": point["aggregates"].get("hit_ratio_mean"),
        "metric_quality": str(quality["metric_quality"]),
        "hit_ratio_source": str(quality["hit_ratio_source"]),
        "num_runs_ok_metrics": int(quality.get("num_runs_ok_metrics", 0)),
        "total_runs": int(quality.get("total_runs", 0)),
        "hit_ratio_comparable": bool(recommendation.get("hit_ratio_comparable", False)),
        "baseline_hard_only_recommended_rps": baseline_rps,
        "dual_boundary_recommended_rps": dual_rps,
        "is_feasible": baseline_rps > 0.0,
        "is_best_safe_candidate": dual_rps > 0.0 and bool(recommendation.get("hit_ratio_comparable", False)),
    }


def _build_sweep_ranking(points: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios = sorted({str(point["scenario"]) for point in points})
    by_scenario: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        ranked = [_point_rank_record(point) for point in points if point["scenario"] == scenario]
        ranked.sort(
            key=lambda item: (
                item["req_s_mean"],
                -item["e2e_p95_ms_mean"],
                -item["concurrency"],
            ),
            reverse=True,
        )
        feasible_points = [item for item in ranked if bool(item["is_feasible"])]
        best_safe_points = [item for item in ranked if bool(item["is_best_safe_candidate"])]
        by_scenario[scenario] = {
            "ranked_points": ranked,
            "feasible_points": feasible_points,
            "best_safe_point": best_safe_points[0] if best_safe_points else None,
            "highest_feasible_point": feasible_points[0] if feasible_points else None,
        }
    return {"by_scenario": by_scenario}


def _build_plot_series(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    series: dict[str, dict[str, Any]] = {}
    for scenario, summary in doc["ranking"]["by_scenario"].items():
        ranked_points = summary["ranked_points"]
        xs = [int(item["concurrency"]) for item in ranked_points]
        ys = [float(item["req_s_mean"]) for item in ranked_points]
        feasible = summary["highest_feasible_point"]
        best_safe = summary["best_safe_point"]
        series[scenario] = {
            "x": xs,
            "y": ys,
            "highest_feasible_point": feasible,
            "best_safe_point": best_safe,
        }
    return series


def _plot_capacity_ranking(doc: dict[str, Any], out_png: Path) -> None:
    series = _build_plot_series(doc)
    scenarios = list(series.keys())
    fig, axes = plt.subplots(1, max(1, len(scenarios)), figsize=(6 * max(1, len(scenarios)), 4.8), sharey=True)
    axes_list = list(axes.flat) if hasattr(axes, "flat") else [axes]
    for ax, scenario in zip(axes_list, scenarios, strict=True):
        data = series[scenario]
        ax.plot(data["x"], data["y"], color="#1f77b4", linewidth=1.5, marker="o", label="req/s mean")
        feasible = data["highest_feasible_point"]
        best_safe = data["best_safe_point"]
        if feasible is not None:
            ax.scatter(
                [int(feasible["concurrency"])],
                [float(feasible["req_s_mean"])],
                color="#2ca02c",
                marker="s",
                s=90,
                label="highest feasible",
                zorder=3,
            )
        if best_safe is not None:
            ax.scatter(
                [int(best_safe["concurrency"])],
                [float(best_safe["req_s_mean"])],
                color="#d62728",
                marker="*",
                s=180,
                label="best safe",
                zorder=4,
            )
        ax.set_title(str(scenario))
        ax.set_xlabel("concurrency")
        ax.set_xticks(data["x"])
        ax.grid(True, alpha=0.3)
        ax.legend()
    axes_list[0].set_ylabel("req/s mean")
    fig.suptitle("Capacity Sweep Ranking: feasible vs best safe")
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def _to_markdown(doc: dict[str, Any], *, ranking_plot_name: str) -> str:
    lines = [
        "# Capacity Sweep Report",
        "",
        f"- base_url: `{doc['meta']['base_url']}`",
        f"- model: `{doc['meta']['model']}`",
        f"- repeats_per_point: `{doc['meta']['repeats']}`",
        "",
        "## Ranking Plot",
        "",
        f"![capacity_sweep_ranking]({ranking_plot_name})",
        "",
        "| Scenario | Concurrency | req/s mean | e2e p95 mean(ms) | hit_ratio mean | metric_quality | ok_metric_runs | hit_ratio_source | feasible | best_safe | baseline_rps | dual_rps |",
        "|---|---:|---:|---:|---:|---|---:|---|---|---|---:|---:|",
    ]
    for point in doc["points"]:
        quality = point["aggregates"]["quality_summary"]
        hit_ratio_mean = point["aggregates"]["hit_ratio_mean"]
        ranking = point["ranking"]
        lines.append(
            "| {scenario} | {concurrency} | {req:.4f} | {e2e:.2f} | {hit} | {quality} | {ok_runs}/{total_runs} | {source} | {feasible} | {best_safe} | {baseline:.4f} | {dual:.4f} |".format(
                scenario=point["scenario"],
                concurrency=int(point["concurrency"]),
                req=float(point["aggregates"].get("req_s_mean", 0.0)),
                e2e=float(point["aggregates"].get("e2e_p95_ms_mean", 0.0)),
                hit="NA" if hit_ratio_mean is None else f"{float(hit_ratio_mean):.4f}",
                quality=str(quality["metric_quality"]),
                ok_runs=int(quality.get("num_runs_ok_metrics", 0)),
                total_runs=int(quality.get("total_runs", 0)),
                source=str(quality["hit_ratio_source"]),
                feasible="yes" if ranking["is_feasible"] else "no",
                best_safe="yes" if ranking["is_best_safe_candidate"] else "no",
                baseline=float(point["recommendation"]["baseline_hard_only_recommended_rps"]),
                dual=float(point["recommendation"]["dual_boundary_recommended_rps"]),
            )
        )

    lines.extend(["", "## Recommendations", ""])
    for scenario, summary in doc["ranking"]["by_scenario"].items():
        highest = summary["highest_feasible_point"]
        best_safe = summary["best_safe_point"]
        if highest is None:
            lines.append(f"- {scenario}: no feasible point under the hard constraints.")
        else:
            lines.append(
                f"- {scenario}: highest feasible point is concurrency={highest['concurrency']} req/s_mean={highest['req_s_mean']:.4f}."
            )
        if best_safe is None:
            lines.append(f"- {scenario}: no best safe point passed the dual boundary constraints.")
        else:
            lines.append(
                f"- {scenario}: best safe point is concurrency={best_safe['concurrency']} dual_rps={best_safe['dual_boundary_recommended_rps']:.4f}."
            )
    calibration = doc.get("calibration", {})
    lines.extend(["", "## Auto Calibration", ""])
    if calibration.get("recommendation"):
        recommendation = calibration["recommendation"]
        lines.append(
            f"- threshold={recommendation['min_hit_ratio']:.2f}, max_concurrency={recommendation['max_concurrency']}, expected_rps={recommendation['expected_rps']:.3f}, confidence={recommendation['confidence']:.3f}, evidence={recommendation['evidence_quality']}."
        )
        lines.append(f"- deployment: {calibration['deployment']['status']} -> {calibration['deployment']['recommended_action']}.")
    else:
        lines.append("- calibration blocked: no point passed all evidence and SLO gates.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run capacity sweep across concurrency points")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--endpoint", default="/v1/completions")
    ap.add_argument("--model", required=True)
    ap.add_argument("--backend-metrics-url", default="")
    ap.add_argument("--exporter-metrics-url", default="http://localhost:9108/metrics")
    ap.add_argument("--scenarios", default="low_reuse,high_reuse")
    ap.add_argument("--concurrency-values", default="1,2,4,8")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--num-requests", type=int, default=80)
    ap.add_argument("--input-len", type=int, default=320)
    ap.add_argument("--output-len", type=int, default=48)
    ap.add_argument("--warmup-requests", type=int, default=0)
    ap.add_argument("--timeout-s", type=float, default=30.0)
    ap.add_argument("--base-seed", type=int, default=42)
    ap.add_argument("--e2e-p95-slo-ms", type=float, default=1500.0)
    ap.add_argument("--min-success-rate", type=float, default=0.99)
    ap.add_argument("--min-hit-ratio", type=float, default=0.05)
    ap.add_argument("--safety-factor", type=float, default=0.9)
    ap.add_argument("--out-dir", default="results/capacity_sweeps/local")
    ap.add_argument("--out-png", default="")
    ap.add_argument("--restart-stack-before-each-point", action="store_true")
    ap.add_argument("--stack-backend", choices=("vllm", "sglang"), default="")
    ap.add_argument("--stack-results-dir", default="")
    ap.add_argument("--stack-served-model-name", default="")
    ap.add_argument("--stack-model-path", default="")
    args = ap.parse_args()

    if args.restart_stack_before_each_point and not args.stack_backend:
        ap.error("--restart-stack-before-each-point requires --stack-backend")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    backend_metrics_url = args.backend_metrics_url or f"{args.base_url.rstrip('/')}/metrics"
    scenarios = [part.strip() for part in args.scenarios.split(",") if part.strip()]
    concurrency_values = _parse_csv_ints(args.concurrency_values)

    points: list[dict[str, Any]] = []
    point_index = 0
    for scenario in scenarios:
        cfg = scenario_config(scenario)
        for concurrency in concurrency_values:
            if args.restart_stack_before_each_point:
                _restart_serving_stack(
                    backend=args.stack_backend,
                    base_url=args.base_url,
                    exporter_metrics_url=args.exporter_metrics_url,
                    results_dir=args.stack_results_dir or os.environ.get("RESULTS_DIR", str(ROOT / "results")),
                    served_model_name=args.stack_served_model_name or args.model,
                    stack_model_path=args.stack_model_path,
                )
            runs: list[dict[str, Any]] = []
            for repeat in range(1, args.repeats + 1):
                point_index += 1
                runs.append(
                    run_windowed_experiment(
                        repeat=repeat,
                        base_url=args.base_url,
                        endpoint=args.endpoint,
                        model=args.model,
                        backend_metrics_url=backend_metrics_url,
                        exporter_metrics_url=args.exporter_metrics_url,
                        num_requests=args.num_requests,
                        concurrency=concurrency,
                        base_seed=args.base_seed + point_index * 1000,
                        input_len=args.input_len,
                        output_len=args.output_len,
                        warmup_requests=args.warmup_requests,
                        timeout_s=args.timeout_s,
                        shared_prefix_ratio=float(cfg["shared_prefix_ratio"]),
                        shared_prefix_len=int(cfg["shared_prefix_len"]),
                        unique_suffix_len=int(cfg["unique_suffix_len"]),
                        point_label=f"{scenario}:c{concurrency}",
                    )
                )
            aggregates = aggregate_run_entries(runs)
            recommendation = _build_point_recommendation(
                runs,
                e2e_p95_slo_ms=args.e2e_p95_slo_ms,
                min_success_rate=args.min_success_rate,
                min_hit_ratio=args.min_hit_ratio,
                safety_factor=args.safety_factor,
            )
            points.append(
                {
                    "scenario": scenario,
                    "concurrency": concurrency,
                    "num_requests": args.num_requests,
                    "runs": runs,
                    "aggregates": aggregates,
                    "recommendation": recommendation,
                }
            )

    ranking = _build_sweep_ranking(points)
    for point in points:
        point["ranking"] = _point_rank_record(point)

    calibration = calibrate(
        points,
        constraints=CalibrationConstraints(args.e2e_p95_slo_ms, args.min_success_rate, args.repeats),
        weights=CalibrationWeights(safety_factor=args.safety_factor),
    )
    doc = {
        "meta": {
            "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
            "run_entry_schema_version": RUN_ENTRY_SCHEMA_VERSION,
            "sweep_type": "concurrency",
            "base_url": args.base_url,
            "endpoint": args.endpoint,
            "model": args.model,
            "backend_metrics_url": backend_metrics_url,
            "exporter_metrics_url": args.exporter_metrics_url,
            "scenarios": scenarios,
            "concurrency_values": concurrency_values,
            "repeats": args.repeats,
            "num_requests": args.num_requests,
            "input_len": args.input_len,
            "output_len": args.output_len,
            "warmup_requests": args.warmup_requests,
            "timeout_s": args.timeout_s,
            "policy": {
                "e2e_p95_slo_ms": args.e2e_p95_slo_ms,
                "min_success_rate": args.min_success_rate,
                "min_hit_ratio": args.min_hit_ratio,
                "safety_factor": args.safety_factor,
            },
        },
        "points": points,
        "ranking": ranking,
        "calibration": calibration,
    }

    out_png = Path(args.out_png) if args.out_png else (out_dir / "capacity_sweep_ranking.png")
    _plot_capacity_ranking(doc, out_png)
    (out_dir / "summary.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_to_markdown(doc, ranking_plot_name=out_png.name), encoding="utf-8")
    print(f"[capacity-sweep] wrote {out_dir / 'summary.json'}")
    print(f"[capacity-sweep] wrote {out_dir / 'summary.md'}")
    print(f"[capacity-sweep] wrote {out_png}")


if __name__ == "__main__":
    main()
