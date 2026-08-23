from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


def validate_capacity_sweep_artifact(artifact_dir: Path) -> list[str]:
    errors: list[str] = []
    summary_path = artifact_dir / "summary.json"
    report_path = artifact_dir / "summary.md"
    if not summary_path.is_file():
        return [f"missing artifact: {summary_path}"]
    if not report_path.is_file():
        errors.append(f"missing artifact: {report_path}")

    try:
        document: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid JSON {summary_path}: {exc}"]

    meta = document.get("meta")
    if not isinstance(meta, dict):
        errors.append("summary.meta must be an object")
    else:
        for key in ("experiment_schema_version", "run_entry_schema_version", "scenarios", "concurrency_values", "repeats"):
            if key not in meta:
                errors.append(f"summary.meta missing {key}")

    points = document.get("points")
    if not isinstance(points, list) or not points:
        errors.append("summary.points must be a non-empty array")
        points = []
    for index, point in enumerate(points):
        prefix = f"summary.points[{index}]"
        if not isinstance(point, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in ("scenario", "concurrency", "runs", "aggregates", "recommendation", "ranking"):
            if key not in point:
                errors.append(f"{prefix} missing {key}")
        aggregates = point.get("aggregates")
        if isinstance(aggregates, dict):
            quality = aggregates.get("quality_summary")
            if not isinstance(quality, dict):
                errors.append(f"{prefix}.aggregates.quality_summary must be an object")
        else:
            errors.append(f"{prefix}.aggregates must be an object")
        if not isinstance(point.get("runs"), list) or not point.get("runs"):
            errors.append(f"{prefix}.runs must be a non-empty array")

    ranking = document.get("ranking")
    if not isinstance(ranking, dict) or not isinstance(ranking.get("by_scenario"), dict):
        errors.append("summary.ranking.by_scenario must be an object")

    if isinstance(meta, dict) and report_path.is_file():
        report = report_path.read_text(encoding="utf-8")
        if "## Ranking Plot" not in report:
            errors.append("summary.md missing Ranking Plot section")
        plot_names = {path.name for path in artifact_dir.glob("*.png")}
        referenced = set()
        for token in report.split("(")[1:]:
            candidate = token.split(")", 1)[0]
            if candidate.endswith(".png"):
                referenced.add(Path(candidate).name)
        if not referenced:
            errors.append("summary.md does not reference a PNG plot")
        for name in referenced:
            plot_path = artifact_dir / name
            if not plot_path.is_file():
                errors.append(f"summary.md references missing plot: {plot_path}")
            elif plot_path.stat().st_size == 0:
                errors.append(f"plot is empty: {plot_path}")
            else:
                try:
                    with Image.open(plot_path) as image:
                        image.verify()
                except (OSError, UnidentifiedImageError) as exc:
                    errors.append(f"plot is not a valid image: {plot_path} ({exc})")
        for name in plot_names:
            if (artifact_dir / name).stat().st_size == 0:
                errors.append(f"plot is empty: {artifact_dir / name}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Kavora capacity sweep artifact")
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()
    errors = validate_capacity_sweep_artifact(args.artifact_dir)
    if errors:
        for error in errors:
            print(f"capacity-sweep artifact: {error}", file=sys.stderr)
        return 1
    print(f"capacity-sweep artifact ok: {args.artifact_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
