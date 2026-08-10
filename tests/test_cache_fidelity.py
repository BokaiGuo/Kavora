from benchmark.cache_fidelity import build_trace, confidence, evaluate, run
from benchmark.cache_fidelity_report import render


def test_exact_zero_lag_matches_ground_truth() -> None:
    result = evaluate(build_trace(200), "exact-kv-events", 0)
    assert result["routing_accuracy"] == 1
    assert result["fallback_rate"] == 0


def test_confidence_and_accuracy_degrade_with_lag() -> None:
    trace = build_trace(500)
    fresh = evaluate(trace, "shadow-index", 0)
    stale = evaluate(trace, "shadow-index", 10000)
    assert confidence(10000, 0.12) < confidence(0, 0.12)
    assert stale["routing_accuracy"] < fresh["routing_accuracy"]
    assert stale["decision_reversal_rate"] > 0


def test_report_keeps_proxy_claim_boundary_explicit() -> None:
    artifact = run(requests=40)
    report = render(artifact)
    assert "not a real-GPU" in report
    assert "Wrong affinity" in report
