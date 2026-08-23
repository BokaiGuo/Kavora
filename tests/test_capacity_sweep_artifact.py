from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scripts.check_capacity_sweep_artifact import validate_capacity_sweep_artifact


def _write_valid_artifact(path: Path) -> None:
    path.mkdir()
    document = {
        "meta": {
            "experiment_schema_version": 3,
            "run_entry_schema_version": 2,
            "scenarios": ["low_reuse"],
            "concurrency_values": [1],
            "repeats": 1,
        },
        "points": [
            {
                "scenario": "low_reuse",
                "concurrency": 1,
                "runs": [{"repeat": 1}],
                "aggregates": {"quality_summary": {"metric_quality": "ok"}},
                "recommendation": {},
                "ranking": {},
            }
        ],
        "ranking": {"by_scenario": {"low_reuse": {}}},
    }
    (path / "summary.json").write_text(json.dumps(document), encoding="utf-8")
    (path / "summary.md").write_text("## Ranking Plot\n\n![plot](capacity_sweep_ranking.png)\n", encoding="utf-8")
    Image.new("RGB", (2, 2), "white").save(path / "capacity_sweep_ranking.png")


def test_validates_complete_capacity_sweep_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "sweep"
    _write_valid_artifact(artifact)

    assert validate_capacity_sweep_artifact(artifact) == []


def test_rejects_missing_point_quality_and_plot(tmp_path: Path) -> None:
    artifact = tmp_path / "sweep"
    _write_valid_artifact(artifact)
    document = json.loads((artifact / "summary.json").read_text(encoding="utf-8"))
    del document["points"][0]["aggregates"]["quality_summary"]
    (artifact / "summary.json").write_text(json.dumps(document), encoding="utf-8")
    (artifact / "capacity_sweep_ranking.png").unlink()

    errors = validate_capacity_sweep_artifact(artifact)

    assert any("quality_summary" in error for error in errors)
    assert any("missing plot" in error for error in errors)
