from exporter.prometheus_parse import aggregate_prometheus_text, parse_prometheus_text


def test_aggregate_sums_labeled_gauge_series() -> None:
    text = """
# HELP x help
# TYPE x gauge
x{gpu="0"} 1
x{gpu="1"} 2
"""
    out = aggregate_prometheus_text(text)
    assert out["x"] == 3.0


def test_parse_prometheus_text_alias_matches_aggregate() -> None:
    text = 'm{a="1"} 10\nm{a="2"} 20\n'
    assert parse_prometheus_text(text) == aggregate_prometheus_text(text)


def test_label_filter_includes_only_matching_series() -> None:
    text = """
# TYPE g gauge
g{model="a",pod="1"} 1
g{model="b",pod="2"} 100
"""
    out = aggregate_prometheus_text(text, label_filter={"model": "a"})
    assert out["g"] == 1.0


def test_histogram_family_skipped_in_aggregate() -> None:
    text = """
# TYPE h histogram
h_bucket{le="1"} 0
h_bucket{le="+Inf"} 3
h_sum 2
h_count 3
# TYPE g gauge
g 5
"""
    out = aggregate_prometheus_text(text)
    assert "g" in out and out["g"] == 5.0
    assert not any(k.startswith("h") for k in out)
