from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_final_report import _build_report, _read_json


def _optional(path: Path) -> Path | None:
    return path if path.is_file() else None


def _copy_plot(source: Path | None, output_dir: Path, *, name: str) -> Path | None:
    if source is None:
        return None
    destination = output_dir / name
    shutil.copy2(source, destination)
    return destination


def _build_one_report(
    *,
    experiment_dir: Path,
    capacity_dir: Path | None,
    output_dir: Path,
    language: str,
    output_name: str,
) -> Path:
    reuse_summary = experiment_dir / "summary.json"
    if not reuse_summary.is_file():
        raise FileNotFoundError(f"missing required reuse summary: {reuse_summary}")

    baseline = _optional(experiment_dir / "baseline_compare.json")
    threshold_json = _optional(experiment_dir / "threshold_recommended_rps_curve.json")
    threshold_pngs = [
        path
        for path in (
            _optional(experiment_dir / "threshold_recommended_rps_curve.png"),
            _optional(experiment_dir / "threshold_recommended_rps_curve_split.png"),
        )
        if path is not None
    ]
    capacity_summary = _optional(capacity_dir / "summary.json") if capacity_dir else None
    capacity_png = _optional(capacity_dir / "capacity_sweep_ranking.png") if capacity_dir else None

    output_dir.mkdir(parents=True, exist_ok=True)
    copied_threshold_pngs = [_copy_plot(path, output_dir, name=path.name) for path in threshold_pngs]
    copied_threshold_pngs = [path for path in copied_threshold_pngs if path is not None]
    copied_capacity_png = _copy_plot(capacity_png, output_dir, name="capacity_sweep_ranking.png")

    report = _build_report(
        reuse_doc=_read_json(str(reuse_summary)),
        baseline_doc=_read_json(str(baseline)) if baseline else None,
        threshold_doc=_read_json(str(threshold_json)) if threshold_json else None,
        threshold_pngs=[path.name for path in copied_threshold_pngs],
        capacity_doc=_read_json(str(capacity_summary)) if capacity_summary else None,
        capacity_png=copied_capacity_png.name if copied_capacity_png else None,
        lang=language,
    )
    output_path = output_dir / output_name
    output_path.write_text(report, encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a bilingual Kavora experiment report bundle")
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--capacity-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--language", choices=("en", "zh", "both"), default="both")
    args = parser.parse_args()

    experiment_dir = args.experiment_dir.resolve()
    capacity_dir = args.capacity_dir.resolve() if args.capacity_dir else None
    output_dir = (args.out_dir or experiment_dir).resolve()
    languages = ("en", "zh") if args.language == "both" else (args.language,)
    for language in languages:
        filename = "final_report.md" if language == "en" else "final_report_zh.md"
        output_path = _build_one_report(
            experiment_dir=experiment_dir,
            capacity_dir=capacity_dir,
            output_dir=output_dir,
            language=language,
            output_name=filename,
        )
        print(f"[report-bundle] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
