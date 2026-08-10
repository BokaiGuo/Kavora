from benchmark.paper_report import build_report, markdown


def test_paper_report_has_manifest_and_limitations():
    report = build_report({"config_hash": "a"}, {"config_hash": "b", "status": "validated_real_backend_matrix", "config": {"seed": 7}, "rows": []})
    assert len(report["manifest_hash"]) == 64
    assert "causal performance advantage" in markdown(report)


def test_paper_report_supports_stage2_v2_results():
    stage2 = {
        "config_hash": "b",
        "status": "real_backend_measurement",
        "manifest": {"seed": 7},
        "claim_boundary": "This exact manifest only.",
        "results": [
            {
                "strategy": "static",
                "workload": "random",
                "aggregate": {
                    "throughput_req_s": {"mean": 10.0},
                    "ttft_p95_ms": {"mean": 20.0},
                    "latency_p99_ms": {"mean": 30.0},
                    "error_rate": {"mean": 0.0},
                },
            }
        ],
    }

    report = build_report({"config_hash": "a"}, stage2)
    rendered = markdown(report)

    assert report["manifest"]["seed"] == 7
    assert "static / random" in rendered
    assert "This exact manifest only." in rendered
