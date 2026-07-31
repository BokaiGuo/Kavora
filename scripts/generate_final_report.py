from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_json(path_str: str) -> dict[str, Any]:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def _fmt_float(value: Any, default: str = "NA") -> str:
    if value is None:
        return default
    return f"{float(value):.4f}"


def _fmt_metric_coverage(item: dict[str, Any], *, total_key: str = "total_runs") -> str:
    total = item.get(total_key)
    ok = item.get("num_runs_ok_metrics")
    if total is None or ok is None:
        return "NA"
    if int(total) <= 0:
        return "NA"
    return f"{int(ok)}/{int(total)}"


def _fmt_count_coverage(item: dict[str, Any], *, count_key: str, total_key: str = "total_runs") -> str:
    total = item.get(total_key)
    count = item.get(count_key)
    if total is None or count is None:
        return "NA"
    if int(total) <= 0:
        return "NA"
    return f"{int(count)}/{int(total)}"


def _relpath(target: Path, base_dir: Path) -> str:
    return (
        target.resolve().relative_to(base_dir.resolve()).as_posix()
        if target.is_absolute() and str(target.resolve()).startswith(str(base_dir.resolve()))
        else target.name
    )


def _title(lang: str, key: str) -> str:
    titles = {
        "en": {
            "report": "# Final Experiment Report",
            "decision": "## Decision Summary",
            "context": "## Project Context",
            "setup": "## Experiment Setup",
            "exec": "## Executive Summary",
            "findings": "## Key Findings",
            "reuse": "## Reuse Summary",
            "baseline": "## Baseline Compare",
            "threshold": "## Threshold Scan",
            "capacity": "## Capacity Sweep",
        },
        "zh": {
            "report": "# 最终实验汇报",
            "decision": "## 决策摘要",
            "context": "## 项目背景",
            "setup": "## 实验设置",
            "exec": "## 高层概览",
            "findings": "## 关键发现",
            "reuse": "## 复用实验摘要",
            "baseline": "## 基线与双边界对比",
            "threshold": "## 阈值扫描",
            "capacity": "## 容量扫描",
        },
    }
    return titles[lang][key]


def _reuse_scenario_label(lang: str, scenario: str) -> str:
    if lang == "zh":
        return {"low_reuse": "低复用", "high_reuse": "高复用"}.get(scenario, scenario)
    return scenario


def _build_reuse_section(reuse_doc: dict[str, Any], *, lang: str) -> list[str]:
    if lang == "zh":
        lines = [
            _title(lang, "reuse"),
            "",
            f"- 服务地址：`{reuse_doc['meta']['base_url']}`",
            f"- 模型：`{reuse_doc['meta']['model']}`",
            f"- 重复次数：`{reuse_doc['meta']['repeats']}`",
            "",
            "| 场景 | req/s 均值 | e2e p95 均值(ms) | 命中率均值 | 指标质量 | ok runs | prefix_check | strict runs | token fallback runs | 命中率来源 |",
            "|---|---:|---:|---:|---|---:|---|---:|---:|---|",
        ]
    else:
        lines = [
            _title(lang, "reuse"),
            "",
            f"- base_url: `{reuse_doc['meta']['base_url']}`",
            f"- model: `{reuse_doc['meta']['model']}`",
            f"- repeats: `{reuse_doc['meta']['repeats']}`",
            "",
            "| Scenario | req/s mean | e2e p95 mean(ms) | hit_ratio mean | metric_quality | ok runs | prefix_check | strict runs | token fallback runs | hit_ratio_source |",
            "|---|---:|---:|---:|---|---:|---|---:|---:|---|",
        ]
    for scenario in ("low_reuse", "high_reuse"):
        agg = reuse_doc.get("aggregates", {}).get(scenario, {})
        quality = agg.get("quality_summary", {})
        lines.append(
            "| {scenario} | {req} | {e2e} | {hit} | {quality} | {coverage} | {prefix_check} | {strict_runs} | {token_runs} | {source} |".format(
                scenario=_reuse_scenario_label(lang, scenario),
                req=_fmt_float(agg.get("req_s_mean")),
                e2e=_fmt_float(agg.get("e2e_p95_ms_mean")),
                hit=_fmt_float(agg.get("hit_ratio_mean")),
                quality=str(quality.get("metric_quality", "missing")),
                coverage=_fmt_metric_coverage(quality),
                prefix_check=str(quality.get("prefix_metric_check", "missing")),
                strict_runs=_fmt_count_coverage(quality, count_key="num_runs_prefix_metric_strict"),
                token_runs=_fmt_count_coverage(quality, count_key="num_runs_prefix_metric_token_fallback"),
                source=str(quality.get("hit_ratio_source", "missing")),
            )
        )
    lines.append("")
    return lines


def _build_decision_summary(
    *,
    baseline_doc: dict[str, Any] | None,
    threshold_doc: dict[str, Any] | None,
    capacity_doc: dict[str, Any] | None,
    lang: str,
) -> list[str]:
    lines = [_title(lang, "decision"), ""]
    if baseline_doc is not None:
        high = baseline_doc.get("scenarios", {}).get("high_reuse", {})
        if lang == "zh":
            lines.append(
                "- 近期建议：优先关注高复用区间。该区间 baseline 容量为 `{baseline}`，dual-boundary 容量为 `{dual}`。".format(
                    baseline=_fmt_float(high.get("baseline_hard_only_recommended_rps")),
                    dual=_fmt_float(high.get("dual_boundary_recommended_rps")),
                )
            )
        else:
            lines.append(
                "- Near-term recommendation: prioritize the high_reuse operating region, where baseline capacity is `{baseline}` and dual-boundary capacity is `{dual}`.".format(
                    baseline=_fmt_float(high.get("baseline_hard_only_recommended_rps")),
                    dual=_fmt_float(high.get("dual_boundary_recommended_rps")),
                )
            )
    else:
        lines.append(
            "- 近期建议：先用本报告比较 baseline 吞吐与 cache-aware 安全边界，再决定默认 serving 策略。"
            if lang == "zh"
            else "- Near-term recommendation: use this report to compare baseline throughput and cache-aware safety margins before setting a serving default."
        )
    if capacity_doc is not None:
        high_cap = capacity_doc.get("ranking", {}).get("by_scenario", {}).get("high_reuse", {})
        highest = high_cap.get("highest_feasible_point")
        best_safe = high_cap.get("best_safe_point")
        if lang == "zh":
            lines.append(
                "- 容量建议：可把并发 `{highest_c}` 视作高复用场景下的最高可行点，把并发 `{best_c}` 视作更稳妥的 best safe 点。".format(
                    highest_c=("NA" if highest is None else int(highest["concurrency"])),
                    best_c=("NA" if best_safe is None else int(best_safe["concurrency"])),
                )
            )
        else:
            lines.append(
                "- Capacity decision: treat concurrency `{highest_c}` as the highest feasible target and concurrency `{best_c}` as the safer operating point for high_reuse.".format(
                    highest_c=("NA" if highest is None else int(highest["concurrency"])),
                    best_c=("NA" if best_safe is None else int(best_safe["concurrency"])),
                )
            )
    if threshold_doc is not None:
        high_rows = [row for row in threshold_doc.get("rows", []) if str(row.get("scenario")) == "high_reuse"]
        dual_positive = [row for row in high_rows if float(row.get("dual_rps", 0.0)) > 0.0]
        max_threshold = max((float(row["min_hit_ratio"]) for row in dual_positive), default=None)
        if lang == "zh":
            lines.append(
                "- 策略建议：如果希望保留高复用场景下的 dual 容量，`min_hit_ratio` 最好控制在 `{threshold}` 及以下。".format(
                    threshold=_fmt_float(max_threshold),
                )
            )
        else:
            lines.append(
                "- Policy decision: keep `min_hit_ratio` at or below `{threshold}` if you want cache-aware policy constraints without collapsing dual capacity in high_reuse.".format(
                    threshold=_fmt_float(max_threshold),
                )
            )
    lines.append("")
    return lines


def _build_project_context(*, reuse_doc: dict[str, Any], lang: str) -> list[str]:
    if lang == "zh":
        return [
            _title(lang, "context"),
            "",
            "- 目标：评估 KV cache 复用信号是否足够稳定，能否支持 cache-aware 的 serving 建议，而不是只比较原始吞吐。",
            "- 范围：基于仓库内 exporter + benchmark + planner 链路，对复用场景、阈值扫描、容量扫描做本地离线实验。",
            "- 服务入口：`{base_url}`。".format(base_url=reuse_doc["meta"].get("base_url", "unknown")),
            "- 测试模型：`{model}`。".format(model=reuse_doc["meta"].get("model", "unknown")),
            "",
        ]
    return [
        _title(lang, "context"),
        "",
        "- Objective: evaluate whether KV cache reuse signals are strong enough to support cache-aware serving recommendations, not just raw throughput comparison.",
        "- Scope: local offline experiments over reuse scenarios, threshold scans, and capacity sweep points, using the repository's exporter + benchmark + planner chain.",
        "- Backend entrypoint: `{base_url}`.".format(base_url=reuse_doc["meta"].get("base_url", "unknown")),
        "- Model under test: `{model}`.".format(model=reuse_doc["meta"].get("model", "unknown")),
        "",
    ]


def _build_experiment_setup(*, reuse_doc: dict[str, Any], capacity_doc: dict[str, Any] | None, lang: str) -> list[str]:
    meta = reuse_doc.get("meta", {})
    if lang == "zh":
        lines = [
            _title(lang, "setup"),
            "",
            "- 复用实验：重复 `{repeats}` 次，并发 `{concurrency}`，请求数 `{num_requests}`，输入长度 `{input_len}`，输出长度 `{output_len}`。".format(
                repeats=meta.get("repeats", "NA"),
                concurrency=meta.get("concurrency", "NA"),
                num_requests=meta.get("num_requests", "NA"),
                input_len=meta.get("input_len", "NA"),
                output_len=meta.get("output_len", "NA"),
            ),
            "- 指标来源：backend metrics `{backend_metrics_url}`，exporter metrics `{exporter_metrics_url}`。".format(
                backend_metrics_url=meta.get("backend_metrics_url", "unknown"),
                exporter_metrics_url=meta.get("exporter_metrics_url", "unknown"),
            ),
        ]
    else:
        lines = [
            _title(lang, "setup"),
            "",
            "- Reuse experiment: repeats `{repeats}`, concurrency `{concurrency}`, num_requests `{num_requests}`, input_len `{input_len}`, output_len `{output_len}`.".format(
                repeats=meta.get("repeats", "NA"),
                concurrency=meta.get("concurrency", "NA"),
                num_requests=meta.get("num_requests", "NA"),
                input_len=meta.get("input_len", "NA"),
                output_len=meta.get("output_len", "NA"),
            ),
            "- Metrics source: backend metrics `{backend_metrics_url}` and exporter metrics `{exporter_metrics_url}`.".format(
                backend_metrics_url=meta.get("backend_metrics_url", "unknown"),
                exporter_metrics_url=meta.get("exporter_metrics_url", "unknown"),
            ),
        ]
    if capacity_doc is not None:
        cap_meta = capacity_doc.get("meta", {})
        lines.append(
            (
                "- 容量扫描：场景 `{scenarios}`，并发点 `{concurrency_values}`，每个点重复 `{repeats}` 次。"
                if lang == "zh"
                else "- Capacity sweep: scenarios `{scenarios}`, concurrency_values `{concurrency_values}`, repeats `{repeats}`."
            ).format(
                scenarios=",".join(cap_meta.get("scenarios", [])) or "NA",
                concurrency_values=",".join(str(v) for v in cap_meta.get("concurrency_values", [])) or "NA",
                repeats=cap_meta.get("repeats", "NA"),
            )
        )
    lines.append("")
    return lines


def _build_executive_summary(
    *,
    reuse_doc: dict[str, Any],
    baseline_doc: dict[str, Any] | None,
    threshold_doc: dict[str, Any] | None,
    capacity_doc: dict[str, Any] | None,
    lang: str,
) -> list[str]:
    low = reuse_doc.get("aggregates", {}).get("low_reuse", {})
    high = reuse_doc.get("aggregates", {}).get("high_reuse", {})
    if lang == "zh":
        lines = [
            _title(lang, "exec"),
            "",
            "- 本报告汇总了复用行为、baseline vs dual 策略差异、阈值敏感性以及容量扫描建议。",
            "- 复用结论：低复用 req/s 均值 `{low_req}`，高复用 req/s 均值 `{high_req}`；低复用命中率均值 `{low_hit}`（质量 `{low_quality}`，ok runs `{low_cov}`），高复用命中率均值 `{high_hit}`（质量 `{high_quality}`，ok runs `{high_cov}`）。".format(
                low_req=_fmt_float(low.get("req_s_mean")),
                high_req=_fmt_float(high.get("req_s_mean")),
                low_hit=_fmt_float(low.get("hit_ratio_mean")),
                high_hit=_fmt_float(high.get("hit_ratio_mean")),
                low_quality=str(low.get("quality_summary", {}).get("metric_quality", "missing")),
                high_quality=str(high.get("quality_summary", {}).get("metric_quality", "missing")),
                low_cov=_fmt_metric_coverage(low.get("quality_summary", {})),
                high_cov=_fmt_metric_coverage(high.get("quality_summary", {})),
            ),
        ]
    else:
        lines = [
            _title(lang, "exec"),
            "",
            "- This report consolidates reuse behavior, baseline-vs-dual policy comparison, threshold sensitivity, and capacity sweep recommendations.",
            "- Reuse experiment headline: low_reuse req/s mean `{low_req}` vs high_reuse req/s mean `{high_req}`; low_reuse hit_ratio mean `{low_hit}` (quality `{low_quality}`, ok runs `{low_cov}`) vs high_reuse hit_ratio mean `{high_hit}` (quality `{high_quality}`, ok runs `{high_cov}`).".format(
                low_req=_fmt_float(low.get("req_s_mean")),
                high_req=_fmt_float(high.get("req_s_mean")),
                low_hit=_fmt_float(low.get("hit_ratio_mean")),
                high_hit=_fmt_float(high.get("hit_ratio_mean")),
                low_quality=str(low.get("quality_summary", {}).get("metric_quality", "missing")),
                high_quality=str(high.get("quality_summary", {}).get("metric_quality", "missing")),
                low_cov=_fmt_metric_coverage(low.get("quality_summary", {})),
                high_cov=_fmt_metric_coverage(high.get("quality_summary", {})),
            ),
        ]
    if baseline_doc is not None:
        high_baseline = baseline_doc.get("scenarios", {}).get("high_reuse", {})
        lines.append(
            (
                "- 策略结论：高复用场景 baseline 为 `{baseline}`，dual 为 `{dual}`，差值 `{delta}`。"
                if lang == "zh"
                else "- Policy headline: high_reuse baseline `{baseline}` vs dual `{dual}` with delta `{delta}`."
            ).format(
                baseline=_fmt_float(high_baseline.get("baseline_hard_only_recommended_rps")),
                dual=_fmt_float(high_baseline.get("dual_boundary_recommended_rps")),
                delta=_fmt_float(high_baseline.get("delta_dual_minus_baseline")),
            )
        )
    if threshold_doc is not None:
        high_rows = [row for row in threshold_doc.get("rows", []) if str(row.get("scenario")) == "high_reuse"]
        dual_positive = [row for row in high_rows if float(row.get("dual_rps", 0.0)) > 0.0]
        max_threshold = max((float(row["min_hit_ratio"]) for row in dual_positive), default=None)
        lines.append(
            (
                "- 阈值结论：高复用场景中仍能保住 dual 容量的最高阈值约为 `{threshold}`。"
                if lang == "zh"
                else "- Threshold headline: the highest threshold that still preserves dual capacity in high_reuse is `{threshold}`."
            ).format(
                threshold=_fmt_float(max_threshold),
            )
        )
    if capacity_doc is not None:
        cap = capacity_doc.get("ranking", {}).get("by_scenario", {}).get("high_reuse", {})
        highest = cap.get("highest_feasible_point")
        best_safe = cap.get("best_safe_point")
        lines.append(
            (
                "- 容量结论：高复用场景最高可行点是并发 `{highest_c}`、req/s 均值 `{highest_r}`；更保守的 best safe 点是并发 `{best_c}`、dual RPS `{best_r}`。"
                if lang == "zh"
                else "- Capacity headline: high_reuse highest feasible point is concurrency `{highest_c}` at req/s mean `{highest_r}`; best safe point is concurrency `{best_c}` at dual RPS `{best_r}`."
            ).format(
                highest_c=("NA" if highest is None else int(highest["concurrency"])),
                highest_r=_fmt_float(None if highest is None else highest.get("req_s_mean")),
                best_c=("NA" if best_safe is None else int(best_safe["concurrency"])),
                best_r=_fmt_float(None if best_safe is None else best_safe.get("dual_boundary_recommended_rps")),
            )
        )
    lines.append("")
    return lines


def _build_key_findings(
    *,
    reuse_doc: dict[str, Any],
    baseline_doc: dict[str, Any] | None,
    threshold_doc: dict[str, Any] | None,
    capacity_doc: dict[str, Any] | None,
    lang: str,
) -> list[str]:
    low = reuse_doc.get("aggregates", {}).get("low_reuse", {})
    high = reuse_doc.get("aggregates", {}).get("high_reuse", {})
    if lang == "zh":
        findings = [
            "- 高复用场景比低复用场景呈现出更强的缓存收益信号，命中率均值分别为 `{high_hit}` 与 `{low_hit}`。".format(
                high_hit=_fmt_float(high.get("hit_ratio_mean")),
                low_hit=_fmt_float(low.get("hit_ratio_mean")),
            ),
            "- 两类复用场景的吞吐均保持稳定，低复用 req/s 均值 `{low_req}`，高复用 req/s 均值 `{high_req}`。".format(
                low_req=_fmt_float(low.get("req_s_mean")),
                high_req=_fmt_float(high.get("req_s_mean")),
            ),
        ]
    else:
        findings = [
            "- High reuse delivers stronger cache benefit signals than low reuse, reflected by hit_ratio mean `{high_hit}` vs `{low_hit}`.".format(
                high_hit=_fmt_float(high.get("hit_ratio_mean")),
                low_hit=_fmt_float(low.get("hit_ratio_mean")),
            ),
            "- Throughput stays competitive across reuse scenarios, with req/s mean `{low_req}` for low_reuse and `{high_req}` for high_reuse.".format(
                low_req=_fmt_float(low.get("req_s_mean")),
                high_req=_fmt_float(high.get("req_s_mean")),
            ),
        ]
    if baseline_doc is not None:
        for scenario in ("low_reuse", "high_reuse"):
            item = baseline_doc.get("scenarios", {}).get(scenario, {})
            findings.append(
                (
                    "- {scenario} 策略差异：baseline `{baseline}`，dual `{dual}`，差值 `{delta}`，来源 `{source}`。"
                    if lang == "zh"
                    else "- {scenario} policy gap: baseline `{baseline}`, dual `{dual}`, delta `{delta}`, source `{source}`."
                ).format(
                    scenario=_reuse_scenario_label(lang, scenario),
                    baseline=_fmt_float(item.get("baseline_hard_only_recommended_rps")),
                    dual=_fmt_float(item.get("dual_boundary_recommended_rps")),
                    delta=_fmt_float(item.get("delta_dual_minus_baseline")),
                    source=str(item.get("hit_ratio_source", "missing")),
                )
            )
    if threshold_doc is not None:
        for scenario in ("low_reuse", "high_reuse"):
            rows = [row for row in threshold_doc.get("rows", []) if str(row.get("scenario")) == scenario]
            dual_positive = [row for row in rows if float(row.get("dual_rps", 0.0)) > 0.0]
            max_threshold = max((float(row["min_hit_ratio"]) for row in dual_positive), default=None)
            findings.append(
                (
                    "- {scenario} 阈值韧性：仍能保持 dual > 0 的最高阈值为 `{threshold}`。"
                    if lang == "zh"
                    else "- {scenario} threshold resilience: max threshold with dual > 0 is `{threshold}`."
                ).format(
                    scenario=_reuse_scenario_label(lang, scenario),
                    threshold=_fmt_float(max_threshold),
                )
            )
    if capacity_doc is not None:
        for scenario in ("low_reuse", "high_reuse"):
            item = capacity_doc.get("ranking", {}).get("by_scenario", {}).get(scenario, {})
            highest = item.get("highest_feasible_point")
            best_safe = item.get("best_safe_point")
            findings.append(
                (
                    "- {scenario} 容量建议：最高可行并发 `{highest_c}`，best safe 并发 `{best_c}`。"
                    if lang == "zh"
                    else "- {scenario} capacity recommendation: highest feasible concurrency `{highest_c}`, best safe concurrency `{best_c}`."
                ).format(
                    scenario=_reuse_scenario_label(lang, scenario),
                    highest_c=("NA" if highest is None else int(highest["concurrency"])),
                    best_c=("NA" if best_safe is None else int(best_safe["concurrency"])),
                )
            )
    return [_title(lang, "findings"), ""] + findings + [""]


def _build_baseline_section(doc: dict[str, Any], *, lang: str) -> list[str]:
    if lang == "zh":
        lines = [
            _title(lang, "baseline"),
            "",
            "| 场景 | baseline_rps | dual_rps | 差值 | 指标质量 | ok runs | prefix_check | strict runs | token fallback runs | 命中率来源 |",
            "|---|---:|---:|---:|---|---:|---|---:|---:|---|",
        ]
    else:
        lines = [
            _title(lang, "baseline"),
            "",
            "| Scenario | baseline_rps | dual_rps | delta | metric_quality | ok runs | prefix_check | strict runs | token fallback runs | hit_ratio_source |",
            "|---|---:|---:|---:|---|---:|---|---:|---:|---|",
        ]
    for scenario in ("low_reuse", "high_reuse"):
        item = doc.get("scenarios", {}).get(scenario, {})
        lines.append(
            "| {scenario} | {baseline} | {dual} | {delta} | {quality} | {coverage} | {prefix_check} | {strict_runs} | {token_runs} | {source} |".format(
                scenario=_reuse_scenario_label(lang, scenario),
                baseline=_fmt_float(item.get("baseline_hard_only_recommended_rps")),
                dual=_fmt_float(item.get("dual_boundary_recommended_rps")),
                delta=_fmt_float(item.get("delta_dual_minus_baseline")),
                quality=str(item.get("metric_quality", "missing")),
                coverage=_fmt_metric_coverage(item, total_key="num_runs"),
                prefix_check=str(item.get("prefix_metric_check", "missing")),
                strict_runs=_fmt_count_coverage(item, count_key="num_runs_prefix_metric_strict", total_key="num_runs"),
                token_runs=_fmt_count_coverage(item, count_key="num_runs_prefix_metric_token_fallback", total_key="num_runs"),
                source=str(item.get("hit_ratio_source", "missing")),
            )
        )
    lines.append("")
    return lines


def _build_threshold_section(doc: dict[str, Any], *, pngs: list[str], lang: str) -> list[str]:
    rows = doc.get("rows", [])
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(str(row["scenario"]), []).append(row)

    if lang == "zh":
        lines = [
            _title(lang, "threshold"),
            "",
            "| 场景 | baseline_rps | 最佳 dual_rps | dual 仍可保留的最高阈值 | 指标质量 | ok runs | prefix_check | strict runs | token fallback runs | 命中率来源 |",
            "|---|---:|---:|---:|---|---:|---|---:|---:|---|",
        ]
    else:
        lines = [
            _title(lang, "threshold"),
            "",
            "| Scenario | baseline_rps | best_dual_rps | max_threshold_with_dual | metric_quality | ok runs | prefix_check | strict runs | token fallback runs | hit_ratio_source |",
            "|---|---:|---:|---:|---|---:|---|---:|---:|---|",
        ]
    for scenario in ("low_reuse", "high_reuse"):
        scenario_rows = by_scenario.get(scenario, [])
        dual_positive = [row for row in scenario_rows if float(row.get("dual_rps", 0.0)) > 0.0]
        max_threshold = max((float(row["min_hit_ratio"]) for row in dual_positive), default=None)
        best_dual = max((float(row["dual_rps"]) for row in scenario_rows), default=0.0)
        baseline = float(scenario_rows[0]["baseline_rps"]) if scenario_rows else None
        quality = str(scenario_rows[0]["metric_quality"]) if scenario_rows else "missing"
        source = str(scenario_rows[0]["hit_ratio_source"]) if scenario_rows else "missing"
        lines.append(
            "| {scenario} | {baseline} | {best_dual} | {threshold} | {quality} | {coverage} | {prefix_check} | {strict_runs} | {token_runs} | {source} |".format(
                scenario=_reuse_scenario_label(lang, scenario),
                baseline=_fmt_float(baseline),
                best_dual=_fmt_float(best_dual),
                threshold=_fmt_float(max_threshold),
                quality=quality,
                coverage=(_fmt_metric_coverage(scenario_rows[0], total_key="num_runs") if scenario_rows else "NA"),
                prefix_check=(str(scenario_rows[0].get("prefix_metric_check", "missing")) if scenario_rows else "missing"),
                strict_runs=(
                    _fmt_count_coverage(scenario_rows[0], count_key="num_runs_prefix_metric_strict", total_key="num_runs")
                    if scenario_rows
                    else "NA"
                ),
                token_runs=(
                    _fmt_count_coverage(
                        scenario_rows[0],
                        count_key="num_runs_prefix_metric_token_fallback",
                        total_key="num_runs",
                    )
                    if scenario_rows
                    else "NA"
                ),
                source=source,
            )
        )
    lines.append("")
    for png in pngs:
        lines.append(f"![{Path(png).stem}]({png})")
        lines.append("")
    return lines


def _build_capacity_section(doc: dict[str, Any], *, png: str | None, lang: str) -> list[str]:
    if lang == "zh":
        lines = [
            _title(lang, "capacity"),
            "",
            "| 场景 | 最高可行并发 | 最高可行 req/s | best safe 并发 | best safe dual_rps |",
            "|---|---:|---:|---:|---:|",
        ]
    else:
        lines = [
            _title(lang, "capacity"),
            "",
            "| Scenario | highest_feasible_concurrency | highest_feasible_req/s | best_safe_concurrency | best_safe_dual_rps |",
            "|---|---:|---:|---:|---:|",
        ]
    by_scenario = doc.get("ranking", {}).get("by_scenario", {})
    for scenario in ("low_reuse", "high_reuse"):
        item = by_scenario.get(scenario, {})
        highest = item.get("highest_feasible_point")
        best_safe = item.get("best_safe_point")
        lines.append(
            "| {scenario} | {hc} | {hr} | {bc} | {bd} |".format(
                scenario=_reuse_scenario_label(lang, scenario),
                hc=("NA" if highest is None else int(highest["concurrency"])),
                hr=_fmt_float(None if highest is None else highest.get("req_s_mean")),
                bc=("NA" if best_safe is None else int(best_safe["concurrency"])),
                bd=_fmt_float(None if best_safe is None else best_safe.get("dual_boundary_recommended_rps")),
            )
        )
    lines.append("")
    if png:
        lines.append(f"![capacity_sweep_ranking]({png})")
        lines.append("")
    return lines


def _build_report(
    *,
    reuse_doc: dict[str, Any],
    baseline_doc: dict[str, Any] | None,
    threshold_doc: dict[str, Any] | None,
    threshold_pngs: list[str],
    capacity_doc: dict[str, Any] | None,
    capacity_png: str | None,
    lang: str = "en",
) -> str:
    lines = [_title(lang, "report"), ""]
    lines.extend(
        _build_decision_summary(
            baseline_doc=baseline_doc,
            threshold_doc=threshold_doc,
            capacity_doc=capacity_doc,
            lang=lang,
        )
    )
    lines.extend(_build_project_context(reuse_doc=reuse_doc, lang=lang))
    lines.extend(_build_experiment_setup(reuse_doc=reuse_doc, capacity_doc=capacity_doc, lang=lang))
    lines.extend(
        _build_executive_summary(
            reuse_doc=reuse_doc,
            baseline_doc=baseline_doc,
            threshold_doc=threshold_doc,
            capacity_doc=capacity_doc,
            lang=lang,
        )
    )
    lines.extend(
        _build_key_findings(
            reuse_doc=reuse_doc,
            baseline_doc=baseline_doc,
            threshold_doc=threshold_doc,
            capacity_doc=capacity_doc,
            lang=lang,
        )
    )
    lines.extend(_build_reuse_section(reuse_doc, lang=lang))
    if baseline_doc is not None:
        lines.extend(_build_baseline_section(baseline_doc, lang=lang))
    if threshold_doc is not None:
        lines.extend(_build_threshold_section(threshold_doc, pngs=threshold_pngs, lang=lang))
    if capacity_doc is not None:
        lines.extend(_build_capacity_section(capacity_doc, png=capacity_png, lang=lang))
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate one final markdown report from reuse, threshold, and capacity artifacts")
    ap.add_argument("--reuse-summary", required=True)
    ap.add_argument("--baseline-compare", default="")
    ap.add_argument("--threshold-json", default="")
    ap.add_argument("--threshold-png", action="append", default=[])
    ap.add_argument("--capacity-summary", default="")
    ap.add_argument("--capacity-png", default="")
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--lang", choices=["en", "zh"], default="en")
    args = ap.parse_args()

    out_md = Path(args.out_md)
    out_dir = out_md.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    reuse_doc = _read_json(args.reuse_summary)
    baseline_doc = _read_json(args.baseline_compare) if args.baseline_compare else None
    threshold_doc = _read_json(args.threshold_json) if args.threshold_json else None
    capacity_doc = _read_json(args.capacity_summary) if args.capacity_summary else None

    threshold_pngs = [_relpath(Path(path), out_dir) for path in args.threshold_png]
    capacity_png = _relpath(Path(args.capacity_png), out_dir) if args.capacity_png else None

    report = _build_report(
        reuse_doc=reuse_doc,
        baseline_doc=baseline_doc,
        threshold_doc=threshold_doc,
        threshold_pngs=threshold_pngs,
        capacity_doc=capacity_doc,
        capacity_png=capacity_png,
        lang=args.lang,
    )
    out_md.write_text(report, encoding="utf-8")
    print(f"[final-report] wrote {out_md}")


if __name__ == "__main__":
    main()
