from __future__ import annotations

import time
from typing import Mapping

import httpx

from exporter.prometheus_parse import aggregate_prometheus_text, parse_label_filter_from_env
from benchmark.window_metrics import make_metric_snapshot


def fetch_metrics_snapshot(
    url: str,
    *,
    label_filter: Mapping[str, str] | None = None,
    timeout_s: float = 5.0,
) -> dict[str, object]:
    filt = label_filter if label_filter is not None else parse_label_filter_from_env()
    # Local benchmark scrapes should not inherit proxy settings.
    with httpx.Client(timeout=timeout_s, trust_env=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
        metrics = aggregate_prometheus_text(resp.text, label_filter=filt)
    return make_metric_snapshot(metrics, ts=time.time())


def fetch_exporter_metrics(
    url: str,
    *,
    label_filter: Mapping[str, str] | None = None,
    timeout_s: float = 5.0,
) -> dict[str, float]:
    snapshot = fetch_metrics_snapshot(url, label_filter=label_filter, timeout_s=timeout_s)
    return snapshot["metrics"]  # type: ignore[return-value]


def fetch_exporter_snapshot(
    url: str,
    *,
    label_filter: Mapping[str, str] | None = None,
    timeout_s: float = 5.0,
) -> dict[str, object]:
    return fetch_metrics_snapshot(url, label_filter=label_filter, timeout_s=timeout_s)


def fetch_backend_snapshot(
    url: str,
    *,
    label_filter: Mapping[str, str] | None = None,
    timeout_s: float = 5.0,
) -> dict[str, object]:
    return fetch_metrics_snapshot(url, label_filter=label_filter, timeout_s=timeout_s)


def parse_metrics_text(
    text: str,
    *,
    label_filter: Mapping[str, str] | None = None,
) -> dict[str, float]:
    """Parse already-fetched Prometheus text (tests / fixtures)."""
    filt = label_filter if label_filter is not None else parse_label_filter_from_env()
    return aggregate_prometheus_text(text, label_filter=filt)
