#!/usr/bin/env python3
"""Download a Hugging Face snapshot into models/hf/<repo tail> (optional [offline] extra)."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-id", required=True)
    ap.add_argument(
        "--local-dir",
        default="",
        help="Destination directory (default: models/hf/<last repo segment>)",
    )
    args = ap.parse_args()

    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:
        raise SystemExit(
            "huggingface_hub is required: pip install -e '.[offline]'"
        ) from e

    root = Path(__file__).resolve().parents[1]
    if args.local_dir:
        dest = Path(args.local_dir)
    else:
        tail = args.repo_id.rstrip("/").split("/")[-1]
        dest = root / "models" / "hf" / tail
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=args.repo_id, local_dir=str(dest))
    print(f"[download_hf_model] wrote {dest}")


if __name__ == "__main__":
    main()
