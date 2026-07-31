from benchmark.paper_report import build_report, markdown


def test_paper_report_has_manifest_and_limitations():
    report = build_report({"config_hash": "a"}, {"config_hash": "b", "status": "validated_real_backend_matrix", "config": {"seed": 7}, "rows": []})
    assert len(report["manifest_hash"]) == 64
    assert "causal performance advantage" in markdown(report)
