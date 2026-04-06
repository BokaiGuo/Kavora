"""Lightweight comparison of two JSON payloads (hard vs hot pools / runs)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hard", required=True)
    ap.add_argument("--hot", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    hard = json.loads(Path(args.hard).read_text(encoding="utf-8"))
    hot = json.loads(Path(args.hot).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"kind": "hard_vs_hot", "hard": hard, "hot": hot}
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[aggregate_hard_vs_hot] wrote {out}")


if __name__ == "__main__":
    main()
