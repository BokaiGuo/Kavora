from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib import font_manager as fm


def _set_chinese_font() -> fm.FontProperties:
    # Pick an existing CJK font file explicitly to avoid glyph-missing warnings.
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
    ]
    font_path = next((p for p in candidates if Path(p).exists()), "")
    if font_path:
        fm.fontManager.addfont(font_path)
        fp = fm.FontProperties(fname=font_path)
        plt.rcParams["font.family"] = fp.get_name()
    else:
        fp = fm.FontProperties()
    plt.rcParams["axes.unicode_minus"] = False
    return fp


def _recommend(
    runs: list[dict[str, Any]],
    *,
    e2e_p95_slo_ms: float,
    min_success_rate: float,
    min_hit_ratio: float | None,
    safety_factor: float,
) -> float:
    passed: list[float] = []
    for entry in runs:
        s = entry.get("summary", {})
        req = s.get("requests", {})
        total = float(req.get("total", 0) or 0)
        ok = float(req.get("ok", 0) or 0)
        success_rate = (ok / total) if total > 0 else 0.0
        e2e = float(s.get("latency", {}).get("e2e_latency_p95_ms", 0.0) or 0.0)
        req_s = float(s.get("throughput", {}).get("req_s", 0.0) or 0.0)
        hit_ratio = float(entry.get("exporter_metrics", {}).get("kvcache_kv_cache_hit_ratio", 0.0) or 0.0)

        hard_ok = e2e <= e2e_p95_slo_ms and success_rate >= min_success_rate
        hot_ok = True if min_hit_ratio is None else (hit_ratio >= min_hit_ratio)
        if hard_ok and hot_ok:
            passed.append(req_s)
    if not passed:
        return 0.0
    return max(passed) * safety_factor


def _frange(start: float, end: float, step: float) -> list[float]:
    vals: list[float] = []
    x = start
    while x <= end + 1e-9:
        vals.append(round(x, 2))
        x += step
    return vals


def main() -> None:
    ap = argparse.ArgumentParser(description="Plot threshold vs recommended RPS curves")
    ap.add_argument("--input", required=True, help="reuse experiment summary.json")
    ap.add_argument("--out-png", required=True)
    ap.add_argument("--out-split-png", default="")
    ap.add_argument("--out-zh-png", default="")
    ap.add_argument("--out-zh-split-png", default="")
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--start", type=float, default=0.65)
    ap.add_argument("--end", type=float, default=0.82)
    ap.add_argument("--step", type=float, default=0.01)
    ap.add_argument("--e2e-p95-slo-ms", type=float, default=1500.0)
    ap.add_argument("--min-success-rate", type=float, default=0.99)
    ap.add_argument("--safety-factor", type=float, default=0.9)
    args = ap.parse_args()

    doc = json.loads(Path(args.input).read_text(encoding="utf-8"))
    thresholds = _frange(args.start, args.end, args.step)

    rows: list[dict[str, float | str]] = []
    for t in thresholds:
        for scenario in ("low_reuse", "high_reuse"):
            runs = doc.get("runs", {}).get(scenario, [])
            baseline = _recommend(
                runs,
                e2e_p95_slo_ms=args.e2e_p95_slo_ms,
                min_success_rate=args.min_success_rate,
                min_hit_ratio=None,
                safety_factor=args.safety_factor,
            )
            dual = _recommend(
                runs,
                e2e_p95_slo_ms=args.e2e_p95_slo_ms,
                min_success_rate=args.min_success_rate,
                min_hit_ratio=t,
                safety_factor=args.safety_factor,
            )
            rows.append(
                {
                    "min_hit_ratio": t,
                    "scenario": scenario,
                    "baseline_rps": round(baseline, 6),
                    "dual_rps": round(dual, 6),
                    "delta": round(dual - baseline, 6),
                }
            )

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["min_hit_ratio", "scenario", "baseline_rps", "dual_rps", "delta"])
        w.writeheader()
        w.writerows(rows)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    x = thresholds
    series: dict[str, dict[str, list[float]]] = {
        "low_reuse": {"baseline": [], "dual": []},
        "high_reuse": {"baseline": [], "dual": []},
    }
    for t in thresholds:
        for scenario in ("low_reuse", "high_reuse"):
            r = next(r for r in rows if r["min_hit_ratio"] == t and r["scenario"] == scenario)
            series[scenario]["baseline"].append(float(r["baseline_rps"]))
            series[scenario]["dual"].append(float(r["dual_rps"]))

    plt.figure(figsize=(10, 6))
    plt.plot(x, series["low_reuse"]["baseline"], "--", label="low baseline")
    plt.plot(x, series["low_reuse"]["dual"], "-", label="low dual")
    plt.plot(x, series["high_reuse"]["baseline"], "--", label="high baseline")
    plt.plot(x, series["high_reuse"]["dual"], "-", label="high dual")
    plt.xlabel("min_hit_ratio")
    plt.ylabel("recommended_rps")
    plt.title("Threshold vs Recommended RPS (baseline vs dual)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    out_png = Path(args.out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_png, dpi=160)
    plt.close()

    # Two-panel plot: one scenario per subplot (clearer for presentation).
    out_split_png = (
        Path(args.out_split_png)
        if args.out_split_png
        else out_png.with_name(out_png.stem + "_split" + out_png.suffix)
    )
    out_split_png.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for ax, scenario, title in (
        (axes[0], "low_reuse", "Low Reuse"),
        (axes[1], "high_reuse", "High Reuse"),
    ):
        ax.plot(x, series[scenario]["baseline"], "--", label="baseline")
        ax.plot(x, series[scenario]["dual"], "-", label="dual")
        ax.set_title(title)
        ax.set_xlabel("min_hit_ratio")
        ax.grid(True, alpha=0.3)
    axes[0].set_ylabel("recommended_rps")
    axes[0].legend()
    axes[1].legend()
    fig.suptitle("Threshold vs Recommended RPS (split by scenario)")
    fig.tight_layout()
    fig.savefig(out_split_png, dpi=160)
    plt.close(fig)

    # Chinese-labeled version for presentation.
    fp_zh = _set_chinese_font()
    out_zh_png = (
        Path(args.out_zh_png)
        if args.out_zh_png
        else out_png.with_name(out_png.stem + "_zh" + out_png.suffix)
    )
    out_zh_png.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 6))
    plt.plot(x, series["low_reuse"]["baseline"], "--", label="低复用 基线")
    plt.plot(x, series["low_reuse"]["dual"], "-", label="低复用 双边界")
    plt.plot(x, series["high_reuse"]["baseline"], "--", label="高复用 基线")
    plt.plot(x, series["high_reuse"]["dual"], "-", label="高复用 双边界")
    plt.xlabel("最小命中率阈值（min_hit_ratio）", fontproperties=fp_zh)
    plt.ylabel("推荐 RPS", fontproperties=fp_zh)
    plt.title("阈值-推荐RPS曲线（基线 vs 双边界）", fontproperties=fp_zh)
    plt.grid(True, alpha=0.3)
    plt.legend(prop=fp_zh)
    plt.tight_layout()
    plt.savefig(out_zh_png, dpi=160)
    plt.close()

    out_zh_split_png = (
        Path(args.out_zh_split_png)
        if args.out_zh_split_png
        else out_png.with_name(out_png.stem + "_split_zh" + out_png.suffix)
    )
    out_zh_split_png.parent.mkdir(parents=True, exist_ok=True)
    fig_zh, axes_zh = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for ax, scenario, title in (
        (axes_zh[0], "low_reuse", "低复用场景"),
        (axes_zh[1], "high_reuse", "高复用场景"),
    ):
        ax.plot(x, series[scenario]["baseline"], "--", label="基线")
        ax.plot(x, series[scenario]["dual"], "-", label="双边界")
        ax.set_title(title, fontproperties=fp_zh)
        ax.set_xlabel("最小命中率阈值", fontproperties=fp_zh)
        ax.grid(True, alpha=0.3)
        ax.legend(prop=fp_zh)
    axes_zh[0].set_ylabel("推荐 RPS", fontproperties=fp_zh)
    fig_zh.suptitle("阈值-推荐RPS双子图", fontproperties=fp_zh)
    fig_zh.tight_layout()
    fig_zh.savefig(out_zh_split_png, dpi=160)
    plt.close(fig_zh)

    print(f"[plot-threshold-curve] wrote {out_png}")
    print(f"[plot-threshold-curve] wrote {out_split_png}")
    print(f"[plot-threshold-curve] wrote {out_zh_png}")
    print(f"[plot-threshold-curve] wrote {out_zh_split_png}")
    print(f"[plot-threshold-curve] wrote {out_csv}")
    print(f"[plot-threshold-curve] wrote {out_json}")


if __name__ == "__main__":
    main()
