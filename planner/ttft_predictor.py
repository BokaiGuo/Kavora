from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "kavora-ttft-predictor/v1"
FEATURE_NAMES = (
    "intercept_ms",
    "uncached_token_ms",
    "cached_token_ms",
    "queue_penalty_ms",
    "kv_pressure_penalty_ms",
)


def _read_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def _candidate(decision: dict[str, Any], backend_id: str) -> dict[str, Any] | None:
    for candidate in decision.get("candidates", []):
        if isinstance(candidate, dict) and candidate.get("backend_id") == backend_id:
            return candidate
    return None


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("predictor design matrix is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                current - factor * pivot_value
                for current, pivot_value in zip(augmented[row], augmented[column], strict=True)
            ]
    return [augmented[index][-1] for index in range(size)]


def _fit(features: list[list[float]], targets: list[float], ridge: float) -> list[float]:
    width = len(FEATURE_NAMES)
    matrix = [[0.0 for _ in range(width)] for _ in range(width)]
    vector = [0.0 for _ in range(width)]
    for row, target in zip(features, targets, strict=True):
        for left in range(width):
            vector[left] += row[left] * target
            for right in range(width):
                matrix[left][right] += row[left] * row[right]
    for index in range(1, width):
        matrix[index][index] += ridge
    coefficients = _solve(matrix, vector)
    slopes = [max(0.0, value) for value in coefficients[1:]]
    intercept = max(
        0.0,
        sum(target - sum(value * slope for value, slope in zip(row[1:], slopes, strict=True)) for row, target in zip(features, targets, strict=True))
        / len(targets),
    )
    return [intercept, *slopes]


def _predict(row: list[float], coefficients: list[float]) -> float:
    return max(0.0, sum(value * coefficient for value, coefficient in zip(row, coefficients, strict=True)))


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def fit_from_journal(
    directory: str | Path,
    *,
    model: str,
    gpu_type: str,
    backend_engine: str,
    backend_version: str,
    ridge: float = 1e-6,
) -> dict[str, Any]:
    root = Path(directory)
    decisions = {
        row.get("request_id"): row
        for row in _read_jsonl(sorted(root.glob("decisions-*.jsonl")))
        if row.get("request_id")
    }
    outcomes = _read_jsonl(sorted(root.glob("outcomes-*.jsonl")))
    features: list[list[float]] = []
    targets: list[float] = []
    skipped_missing_decision = 0
    skipped_missing_observed_cache = 0
    skipped_unsuccessful = 0
    skipped_dimension_mismatch = 0
    skipped_invalid = 0
    for outcome in outcomes:
        decision = decisions.get(outcome.get("request_id"))
        if decision is None:
            skipped_missing_decision += 1
            continue
        if not bool(outcome.get("success", False)):
            skipped_unsuccessful += 1
            continue
        dimensions = {
            "model": model,
            "gpu_type": gpu_type,
            "backend_engine": backend_engine,
            "backend_version": backend_version,
        }
        if any(outcome.get(key) != expected for key, expected in dimensions.items()):
            skipped_dimension_mismatch += 1
            continue
        matched = outcome.get("observed_matched_tokens")
        if matched is None:
            skipped_missing_observed_cache += 1
            continue
        candidate = _candidate(decision, str(outcome.get("actual_backend", "")))
        try:
            prompt_tokens = int(outcome.get("prompt_tokens", 0))
            matched_tokens = int(matched)
            ttft_ms = float(outcome.get("ttft_ms", 0))
            queue_depth = float((candidate or {}).get("queue_depth", 0))
            kv_pressure = float((candidate or {}).get("kv_pressure", 0))
        except (TypeError, ValueError):
            skipped_invalid += 1
            continue
        values = (prompt_tokens, matched_tokens, ttft_ms, queue_depth, kv_pressure)
        if (
            candidate is None
            or prompt_tokens <= 0
            or matched_tokens < 0
            or matched_tokens > prompt_tokens
            or ttft_ms <= 0
            or any(not math.isfinite(float(value)) for value in values)
        ):
            skipped_invalid += 1
            continue
        features.append([1.0, float(prompt_tokens - matched_tokens), float(matched_tokens), queue_depth, kv_pressure])
        targets.append(ttft_ms)
    if len(features) < len(FEATURE_NAMES):
        raise ValueError("at least 5 outcome-grounded samples are required")
    coefficients = _fit(features, targets, ridge)
    predictions = [_predict(row, coefficients) for row in features]
    absolute_errors = [abs(actual - predicted) for actual, predicted in zip(targets, predictions, strict=True)]
    payload = ":".join(f"{value:.12g}" for value in coefficients) + f":{len(features)}:{model}:{gpu_type}:{backend_engine}:{backend_version}"
    version = "fitted-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return {
        "schema_version": SCHEMA_VERSION,
        "predictor_version": version,
        "model": model,
        "gpu_type": gpu_type,
        "backend_engine": backend_engine,
        "backend_version": backend_version,
        "coefficients": {
            **dict(zip(FEATURE_NAMES, coefficients, strict=True)),
            "slo_scale_ms": 25.0,
        },
        "validation": {
            "mae_ms": sum(absolute_errors) / len(absolute_errors),
            "p95_absolute_error_ms": _percentile(absolute_errors, 0.95),
            "samples": len(features),
            "method": "in_sample_clamped_ridge",
        },
        "data_quality": {
            "decisions": len(decisions),
            "outcomes": len(outcomes),
            "used_samples": len(features),
            "skipped_missing_decision": skipped_missing_decision,
            "skipped_missing_observed_cache": skipped_missing_observed_cache,
            "skipped_unsuccessful": skipped_unsuccessful,
            "skipped_dimension_mismatch": skipped_dimension_mismatch,
            "skipped_invalid": skipped_invalid,
        },
        "claim_boundary": "This explainable linear predictor is fitted from realized outcomes with observed cache reuse. Validation is in-sample and must not be treated as a production generalization result.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit an explainable TTFT predictor from Kavora decision/outcome journals")
    parser.add_argument("--input", required=True, help="journal directory containing decisions-*.jsonl and outcomes-*.jsonl")
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--gpu-type", required=True)
    parser.add_argument("--backend-engine", required=True)
    parser.add_argument("--backend-version", required=True)
    parser.add_argument("--ridge", type=float, default=1e-6)
    args = parser.parse_args()
    artifact = fit_from_journal(
        args.input,
        model=args.model,
        gpu_type=args.gpu_type,
        backend_engine=args.backend_engine,
        backend_version=args.backend_version,
        ridge=args.ridge,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
