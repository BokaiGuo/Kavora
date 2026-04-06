from __future__ import annotations

from typing import Mapping

import httpx

from exporter.prometheus_parse import aggregate_prometheus_text, parse_label_filter_from_env


def fetch_exporter_metrics(
    url: str,
    *,
    label_filter: Mapping[str, str] | None = None,
    timeout_s: float = 5.0,
) -> dict[str, float]:
    filt = label_filter if label_filter is not None else parse_label_filter_from_env()
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return aggregate_prometheus_text(resp.text, label_filter=filt)


def parse_metrics_text(
    text: str,
    *,
    label_filter: Mapping[str, str] | None = None,
) -> dict[str, float]:
    """Parse already-fetched Prometheus text (tests / fixtures)."""
    filt = label_filter if label_filter is not None else parse_label_filter_from_env()
    return aggregate_prometheus_text(text, label_filter=filt)
