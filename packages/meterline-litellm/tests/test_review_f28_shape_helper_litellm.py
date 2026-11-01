"""Review finding F-28: the L01 shape comparison follows the comparison rules of spec v2 section 8. An empty list in the
shape file is not compared (the Copilot check of C01 already skipped it); a non-empty one still is. The rules now live in
``meterline.conformance.compare_shape``; these tests keep the L01 reading of them (its data-key maps and its optional
metrics) pinned."""

from meterline.conformance import compare_shape
from test_done_check_L01_shapes import DATA_KEYED, OPTIONAL_METRICS, REQUIRED_METRICS, differences


def test_an_empty_list_in_the_shape_is_not_compared():
    assert differences({"models": ["model-a"]}, {"models": []}) == []
    assert differences({"models": [{"name": "model-a"}]}, {"models": []}) == []
    assert differences({"models": []}, {"models": []}) == []


def test_a_non_empty_list_in_the_shape_is_still_compared():
    assert differences({"models": [{"name": "model-a"}]}, {"models": ["model-a"]}) == [
        "$.models[0]: expected scalar, got object",
    ]
    assert differences({"teams": [{"id": "t", "extra": 1}]}, {"teams": [{"id": "t"}]}) == [
        "$.teams[0].extra: not in the documented shape",
    ]
    assert differences({"teams": [{"id": "t"}]}, {"teams": [{"id": "t", "extra": 1}]}) == [
        "$.teams[0].extra: missing",
    ]


def test_an_empty_list_in_the_shape_still_needs_a_list():
    assert differences({"models": "model-a"}, {"models": []}) == ["$.models: expected array, got scalar"]
    assert differences({}, {"models": []}) == ["$.models: missing"]


def test_an_empty_map_in_the_shape_is_free_form_and_a_data_key_map_is_compared_by_its_values():
    assert differences({"metadata": {"any": [1]}}, {"metadata": {}}) == []
    shape = {"models": {"model-a": {"metrics": {"spend": 1}}}}
    answer = {"models": {"m1": {"metrics": {"spend": 2}}, "m2": {"metrics": {"spend": 3, "extra": 4}}}}
    assert "models" in DATA_KEYED
    assert differences(answer, shape) == ["$.models.m2.metrics.extra: not in the documented shape"]
    assert compare_shape(answer, shape) != differences(answer, shape)  # without the data keys the map is read key by key


def test_only_the_four_required_metrics_must_be_present_and_only_directly_inside_a_metrics_map():
    shape = {"metrics": {"spend": 1, "total_tokens": 2}, "metadata": {"total_tokens": 3}}
    assert REQUIRED_METRICS == {"spend", "prompt_tokens", "completion_tokens", "api_requests"}
    assert "metrics.total_tokens" in OPTIONAL_METRICS and "metrics.spend" not in OPTIONAL_METRICS
    assert differences({"metrics": {"spend": 1}, "metadata": {"total_tokens": 3}}, shape) == []
    assert differences({"metrics": {"spend": 1}, "metadata": {}}, shape) == ["$.metadata.total_tokens: missing"]
    assert differences({"metrics": {"total_tokens": 2}, "metadata": {"total_tokens": 3}}, shape) == ["$.metrics.spend: missing"]
