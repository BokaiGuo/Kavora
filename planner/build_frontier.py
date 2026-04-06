"""Merge per-backend benchmark summaries into one JSON artifact (MRE / reports)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True, help="benchmark_summary.json paths")
    ap.add_argument("--labels", nargs="*", default=[], help="labels aligned with --inputs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    labels = list(args.labels)
    for i in range(len(labels), len(args.inputs)):
        labels.append(Path(args.inputs[i]).stem)

    entries = []
    for path_str, label in zip(args.inputs, labels, strict=True):
        p = Path(path_str)
        data = json.loads(p.read_text(encoding="utf-8"))
        entries.append({"label": str(label), "source": str(p), "summary": data})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"kind": "frontier_candidates", "entries": entries}
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[build_frontier] wrote {out}")


if __name__ == "__main__":
    main()
