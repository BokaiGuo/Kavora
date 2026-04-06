#!/usr/bin/env python3
"""Write a small JSON sidecar for local runs (reproducibility / grouping)."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _git_sha(repo_root: Path) -> str | None:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if cp.returncode != 0:
            return None
        s = (cp.stdout or "").strip()
        return s or None
    except OSError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/run_metadata.json")
    ap.add_argument("--backend", default="vllm")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[2]
    now = datetime.now(timezone.utc)
    run_id = args.run_id.strip() or now.strftime("%Y%m%dT%H%M%SZ")

    meta = {
        "run_id": run_id,
        "backend": args.backend,
        "timestamp_utc": now.isoformat(),
        "git_sha": _git_sha(repo_root),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[write_run_metadata] wrote {out}")


if __name__ == "__main__":
    main()
