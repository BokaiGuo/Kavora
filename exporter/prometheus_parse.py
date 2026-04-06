"""Prometheus text exposition parsing with explicit label-aware aggregation.

We use ``prometheus_client.parser.text_string_to_metric_families`` so HELP/TYPE
lines and labeled samples are handled correctly.

Aggregation policy (flat ``dict[str, float]``)
--------------------------------------------
For metric families of type **counter**, **gauge**, or Prometheus **unknown**
(untyped), we **sum** ``sample.value`` over all label sets that share the same
``sample.name``. This matches common "cluster this scrape target" use cases
(e.g. one vLLM process exporting per-GPU series that should be totaled).

**Histogram** and **summary** families are **skipped** in the aggregate map: their
samples use multiple internal names (``_bucket``, ``_sum``, ``_count``) and
blind summing would be misleading. Consumers that need histograms should use
a dedicated path, not this helper.

Optional ``label_filter`` restricts samples: every key in the dict must match
the sample's labels exactly (e.g. ``{"model": "x"}``).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Mapping

from prometheus_client.parser import text_string_to_metric_families

logger = logging.getLogger(__name__)

_AGGREGATABLE = frozenset({"counter", "gauge", "unknown"})


def parse_label_filter_from_env() -> dict[str, str] | None:
    raw = os.environ.get("METRIC_LABEL_FILTER", "").strip()
    if not raw:
        return None
    # Comma-separated k=v pairs: model=m1,instance=i1
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if k:
            out[k] = v
    return out or None


def _labels_match(sample_labels: Mapping[str, str], filt: Mapping[str, str]) -> bool:
    for k, want in filt.items():
        if sample_labels.get(k) != want:
            return False
    return True


def aggregate_prometheus_text(
    text: str,
    *,
    label_filter: Mapping[str, str] | None = None,
) -> dict[str, float]:
    """Parse exposition text and return metric_name -> summed value for counter/gauge/unknown."""
    filt = label_filter if label_filter is not None else parse_label_filter_from_env()
    out: dict[str, float] = {}
    try:
        for family in text_string_to_metric_families(text):
            if family.type not in _AGGREGATABLE:
                continue
            for sample in family.samples:
                if filt is not None and not _labels_match(sample.labels, filt):
                    continue
                name = sample.name
                out[name] = out.get(name, 0.0) + float(sample.value)
        return out
    except Exception:
        logger.warning("prometheus_client parse failed; using relaxed line parser", exc_info=True)
        relaxed = parse_prometheus_text_relaxed_lines(text)
        if filt:
            logger.warning("label_filter ignored in relaxed parse fallback")
        return relaxed


def parse_prometheus_text(text: str) -> dict[str, float]:
    """Backward-compatible name: aggregate labeled series into a single float per metric name."""
    return aggregate_prometheus_text(text)


# Loose line used only for best-effort error messages / debugging
_LINE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([-+0-9.eE]+|NaN|nan|Inf|inf)\s*$")


def parse_prometheus_text_relaxed_lines(text: str) -> dict[str, float]:
    """Fallback when the official parser fails on slightly broken text; still sums duplicate names."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        try:
            out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(2))
        except ValueError:
            continue
    return out
