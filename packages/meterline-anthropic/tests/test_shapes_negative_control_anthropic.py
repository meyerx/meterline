"""Negative control for done-check 12 (``test_done_check_shapes.py``): the shape comparison reports a planted difference
in the fake's answer. It lives in its own file because ``test_done_check_v2.py`` counts the tests of the 17 v1 checks
and check 12 is exactly one of them."""

import httpx

from meterline.conformance import compare_shape
from meterline_anthropic.testing import FakeApi
from test_done_check_shapes import OPTIONAL, REQUESTS, SHAPES


def test_a_planted_difference_in_the_fakes_answer_is_reported(dataset, fake_clock, fake_key_env):
    """Negative control: a key removed, a key added and a row turned into a scalar are each reported with their path."""
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    with httpx.Client(transport=api.transport, base_url="https://fake.invalid/v1/organizations/analytics/") as http:
        response = http.get(
            "usage_report",
            params=REQUESTS["usage_report"],
            headers={"x-api-key": fake_key_env, "anthropic-version": "2023-06-01"},
        )
    body = response.json()
    assert body["data"], "the fake answered no rows: nothing to plant a difference in"
    assert compare_shape(body, SHAPES / "usage_report.json", optional=OPTIONAL) == []
    shape_keys = [k for k in sorted(body["data"][0]) if k not in OPTIONAL]
    removed = {**body, "data": [{k: v for k, v in body["data"][0].items() if k != shape_keys[0]}]}
    added = {**body, "data": [{**body["data"][0], "invented_key": 1}]}
    scalar = {**body, "data": ["a row that is a string"]}
    assert compare_shape(removed, SHAPES / "usage_report.json", optional=OPTIONAL) == [f"$.data[0].{shape_keys[0]}: missing"]
    assert compare_shape(added, SHAPES / "usage_report.json", optional=OPTIONAL) == [
        "$.data[0].invented_key: not in the documented shape"
    ]
    assert compare_shape(scalar, SHAPES / "usage_report.json", optional=OPTIONAL) == ["$.data[0]: expected object, got scalar"]
