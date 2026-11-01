"""The shape check: every answer of the fake has the keys and nesting of the files under docs/api-shapes/anthropic-admin/,
so the fake cannot drift from what was transcribed. Values are never compared."""

import copy
from pathlib import Path

import pytest

from admin_helpers import basic_cost, basic_usage
from admin_helpers import BASE_URL, FAKE_KEY
from meterline.conformance import compare_shape
from meterline_anthropic_admin.testing import COST_PATH, USAGE_PATH
from meterline_anthropic_admin.client import AdminClient

SHAPES = Path(__file__).resolve().parents[3] / "docs" / "api-shapes" / "anthropic-admin"
WINDOW = {"starting_at": "2026-09-15T00:00:00Z", "ending_at": "2026-09-17T00:00:00Z", "limit": 1}


def _answer(api, clock, path, group_by):
    with AdminClient(FAKE_KEY, base_url=BASE_URL, transport=api.transport, clock=clock, sleep=clock.sleep) as client:
        return client.get(path, {**WINDOW, "group_by[]": group_by})


def _usage_answer(make_api, fake_clock):
    return _answer(make_api(basic_usage()), fake_clock, USAGE_PATH, ["api_key_id", "workspace_id", "model", "service_tier"])


def _cost_answer(make_api, fake_clock):
    return _answer(make_api([], basic_cost()), fake_clock, COST_PATH, ["description", "workspace_id"])


def _rows(answer):
    return [row for bucket in answer["data"] for row in bucket["results"]]


def _first_row(answer):
    """The path of the first row, and the row: a list with nothing in it has nothing to compare."""
    for i, bucket in enumerate(answer["data"]):
        if bucket["results"]:
            return "$.data[%d].results[0]" % i, bucket["results"][0]
    raise AssertionError("the fake answered no row: the shape check would compare nothing")


def test_usage_answer_has_the_keys_and_nesting_of_the_shape_file(make_api, fake_clock):
    answer = _usage_answer(make_api, fake_clock)
    assert _rows(answer)
    assert compare_shape(answer, SHAPES / "usage_report_messages.json") == []


def test_cost_answer_has_the_keys_and_nesting_of_the_shape_file(make_api, fake_clock):
    answer = _cost_answer(make_api, fake_clock)
    assert _rows(answer)
    assert compare_shape(answer, SHAPES / "cost_report.json") == []


def test_a_planted_difference_in_an_answer_is_reported_with_its_path(make_api, fake_clock):
    """Negative control: a key removed from a row, a key added to a row and a list turned into another kind are each
    caught; an answer with no row at all compares nothing, which is why the two checks above ask for a row."""
    for answer, name in ((_usage_answer(make_api, fake_clock), "usage_report_messages.json"),
                         (_cost_answer(make_api, fake_clock), "cost_report.json")):
        where, row = _first_row(answer)
        victim = sorted(row)[0]
        removed = copy.deepcopy(answer)
        del _first_row(removed)[1][victim]
        assert compare_shape(removed, SHAPES / name) == ["%s.%s: missing" % (where, victim)]
        added = copy.deepcopy(answer)
        _first_row(added)[1]["invented_key"] = 1
        assert compare_shape(added, SHAPES / name) == ["%s.invented_key: not in the documented shape" % where]
        listed = copy.deepcopy(answer)
        listed["has_more"] = [True]
        assert compare_shape(listed, SHAPES / name) == ["$.has_more: expected scalar, got array"]
        emptied = copy.deepcopy(answer)
        for bucket in emptied["data"]:
            bucket["results"] = []
        assert compare_shape(emptied, SHAPES / name) == [] and not _rows(emptied)
