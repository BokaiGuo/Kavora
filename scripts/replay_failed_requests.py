#!/usr/bin/env python3
"""Replay JSONL lines as HTTP POSTs (minimal helper for failed benchmark rows)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--timeout-s", type=float, default=60.0)
    args = ap.parse_args()

    if not args.jsonl.is_file():
        raise SystemExit(f"missing {args.jsonl}")

    with args.jsonl.open(encoding="utf-8") as f, httpx.Client(timeout=args.timeout_s) as client:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row: dict[str, Any] = json.loads(line)
            url = row.get("url") or row.get("request_url")
            if not url:
                base = row.get("base_url", "").rstrip("/")
                path = row.get("path", "")
                url = f"{base}{path}" if base else ""
            if not url:
                print(f"[replay] skip line {line_no}: no url", file=sys.stderr)
                continue
            body = row.get("json") if "json" in row else row.get("body")
            headers = row.get("headers") if isinstance(row.get("headers"), dict) else None
            r = client.post(url, json=body, headers=headers)
            print(f"[replay] line={line_no} status={r.status_code} url={url}")


if __name__ == "__main__":
    main()
