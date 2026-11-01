"""E3: ``meterline.conformance.compare_shape`` reports differences in keys and nesting between a parsed answer and a
shape file, never by value. Each planted difference below is reported with its path; an equal answer gives ``[]``."""

import json

import pytest

from meterline import conformance
from meterline.conformance import compare_shape

SHAPE = {
    "data": [{"day": "2026-09-15", "usage": {"input": 1, "output": 2}, "tags": ["a"]}],
    "has_more": False,
    "models": {"model-a": {"input": 1, "output": 2}},
    "free": {},
    "notes": [],
}


def answer(**changes):
    base = {
        "data": [{"day": "2026-09-20", "usage": {"input": 10, "output": 20}, "tags": ["x", "y"]}],
        "has_more": True,
        "models": {"model-a": {"input": 10, "output": 20}},
        "free": {"anything": 1},
        "notes": ["whatever", {"shape": "is not compared"}],
    }
    base.update(changes)
    return base


def test_an_equal_answer_gives_an_empty_list_and_values_are_never_compared():
    assert compare_shape(answer(), SHAPE) == []
    assert compare_shape(answer(has_more="yes", data=[]), SHAPE) == []  # an empty answer list has nothing to compare


def test_a_missing_key_is_reported_with_its_path():
    row = {"day": "d", "usage": {"input": 1}, "tags": []}
    assert compare_shape(answer(data=[row]), SHAPE) == ["$.data[0].usage.output: missing"]
    assert compare_shape({k: v for k, v in answer().items() if k != "has_more"}, SHAPE) == ["$.has_more: missing"]


def test_a_key_that_is_not_in_the_shape_is_reported_with_its_path():
    row = {"day": "d", "usage": {"input": 1, "output": 2, "cached": 3}, "tags": [], "extra": 1}
    assert compare_shape(answer(data=[row]), SHAPE) == [
        "$.data[0].extra: not in the documented shape",
        "$.data[0].usage.cached: not in the documented shape",
    ]


def test_list_object_and_scalar_are_told_apart():
    assert compare_shape(answer(has_more=[1]), SHAPE) == ["$.has_more: expected scalar, got array"]
    assert compare_shape(answer(has_more={"a": 1}), SHAPE) == ["$.has_more: expected scalar, got object"]
    row = {"day": "d", "usage": [1, 2], "tags": []}
    assert compare_shape(answer(data=[row]), SHAPE) == ["$.data[0].usage: expected object, got array"]
    row = {"day": "d", "usage": 5, "tags": []}
    assert compare_shape(answer(data=[row]), SHAPE) == ["$.data[0].usage: expected object, got scalar"]
    assert compare_shape(answer(data={"day": "d"}), SHAPE) == ["$.data: expected array, got object"]
    assert compare_shape(answer(data="none"), SHAPE) == ["$.data: expected array, got scalar"]
    assert compare_shape([1], SHAPE) == ["$: expected object, got array"]


def test_a_difference_nested_in_a_list_names_the_item():
    good = {"day": "d", "usage": {"input": 1, "output": 2}, "tags": []}
    bad = {"day": "d", "usage": {"input": 1}, "tags": "x"}
    assert compare_shape(answer(data=[good, good, bad]), SHAPE) == [
        "$.data[2].tags: expected array, got scalar",
        "$.data[2].usage.output: missing",
    ]


def test_a_none_is_a_scalar():
    row = {"day": None, "usage": {"input": None, "output": None}, "tags": []}
    assert compare_shape(answer(data=[row]), SHAPE) == []
    row = {"day": "d", "usage": None, "tags": []}
    assert compare_shape(answer(data=[row]), SHAPE) == ["$.data[0].usage: expected object, got scalar"]


def test_an_empty_dict_or_list_in_the_shape_is_not_compared_but_must_still_be_the_same_kind():
    assert compare_shape(answer(free={"a": {"b": [1]}}, notes=[1, {"a": 1}]), SHAPE) == []
    assert compare_shape(answer(free=[1]), SHAPE) == ["$.free: expected object, got array"]
    assert compare_shape(answer(notes={"a": 1}), SHAPE) == ["$.notes: expected array, got object"]
    assert compare_shape(answer(free=None), SHAPE) == ["$.free: expected object, got scalar"]


def test_a_data_key_map_is_compared_by_the_shape_of_its_values():
    many = {"model-b": {"input": 1, "output": 2}, "model-c": {"input": 1, "output": 2, "extra": 3}, "model-d": {"input": 1}}
    assert compare_shape(answer(models=many), SHAPE, data_keys=["models"]) == [
        "$.models.model-c.extra: not in the documented shape",
        "$.models.model-d.output: missing",
    ]
    assert compare_shape(answer(models={}), SHAPE, data_keys=["models"]) == []
    assert compare_shape(answer(models=[1]), SHAPE, data_keys=["models"]) == ["$.models: expected object, got array"]


def test_without_data_keys_a_map_is_compared_key_by_key():
    other = {"model-b": {"input": 1, "output": 2}}
    assert compare_shape(answer(models=other), SHAPE) == [
        "$.models.model-a: missing",
        "$.models.model-b: not in the documented shape",
    ]


def test_a_data_key_map_may_sit_deeper_and_inside_another_data_key_map():
    shape = {"by_day": {"2026-09-15": {"models": {"model-a": {"input": 1}}}}}
    good = {"by_day": {"2026-09-16": {"models": {"m1": {"input": 5}, "m2": {"input": 6}}}, "2026-09-17": {"models": {}}}}
    bad = {"by_day": {"2026-09-16": {"models": {"m1": {"input": 5, "x": 1}}}}}
    assert compare_shape(good, shape, data_keys={"by_day", "models"}) == []
    assert compare_shape(bad, shape, data_keys={"by_day", "models"}) == [
        "$.by_day.2026-09-16.models.m1.x: not in the documented shape",
    ]


def test_an_optional_key_may_be_absent_but_is_compared_when_present():
    shape = {"id": 1, "nick": "a", "plan": {"seats": 1, "note": "n"}}
    full = {"id": 2, "nick": "b", "plan": {"seats": 3, "note": "m"}}
    bare = {"id": 2, "plan": {"seats": 3}}
    assert compare_shape(full, shape, optional=["nick", "note"]) == []
    assert compare_shape(bare, shape, optional=["nick", "note"]) == []  # a bare name counts at every depth
    assert compare_shape(bare, shape) == ["$.nick: missing", "$.plan.note: missing"]
    wrong = {"id": 2, "nick": ["a"], "plan": {"seats": 3, "note": {"a": 1}}}
    assert compare_shape(wrong, shape, optional=["nick", "note"]) == [
        "$.nick: expected scalar, got array",
        "$.plan.note: expected scalar, got object",
    ]
    extra = {"id": 2, "other": 1, "plan": {"seats": 3}}
    assert compare_shape(extra, shape, optional=["nick", "note"]) == ["$.other: not in the documented shape"]


def test_a_dotted_optional_entry_names_the_parent_and_nothing_else():
    shape = {"metrics": {"spend": 1, "tokens": 2}, "totals": {"spend": 1, "tokens": 2}}
    both_bare = {"metrics": {"spend": 1}, "totals": {"spend": 1}}
    assert compare_shape(both_bare, shape, optional=["metrics.tokens"]) == ["$.totals.tokens: missing"]
    assert compare_shape(both_bare, shape, optional=["tokens"]) == []
    assert compare_shape(both_bare, shape, optional=["metrics.tokens", "totals.tokens"]) == []
    assert compare_shape(both_bare, shape, optional=["metrics.spend"]) == [
        "$.metrics.tokens: missing",
        "$.totals.tokens: missing",
    ]


def test_a_dotted_optional_entry_also_works_below_a_data_key():
    shape = {"models": {"model-a": {"metrics": {"spend": 1, "tokens": 2}}}}
    bare = {"models": {"m1": {"metrics": {"spend": 1}}, "m2": {"metrics": {"tokens": 2}}}}
    assert compare_shape(bare, shape, data_keys={"models"}, optional=["metrics.tokens"]) == ["$.models.m2.metrics.spend: missing"]


def test_the_shape_is_read_from_a_json_file_or_an_ndjson_first_line_or_taken_as_loaded(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps({"a": 1, "b": [{"c": 2}]}), encoding="utf-8")
    (tmp_path / "rows.ndjson").write_text('{"a": 1, "b": 2}\n{"zzz": 1}\n', encoding="utf-8")
    (tmp_path / "blank.ndjson").write_text('\n\n{"a": 1}\n', encoding="utf-8")
    assert compare_shape({"a": 5, "b": [{"c": 9}]}, tmp_path / "one.json") == []
    assert compare_shape({"a": 5, "b": [{}]}, str(tmp_path / "one.json")) == ["$.b[0].c: missing"]
    assert compare_shape({"a": 5, "b": 6}, tmp_path / "rows.ndjson") == []  # only the first line is the shape
    assert compare_shape({"a": 5}, tmp_path / "rows.ndjson") == ["$.b: missing"]
    assert compare_shape({"a": 5}, tmp_path / "blank.ndjson") == []
    assert compare_shape({"a": 5, "b": 6}, {"a": 1, "b": 2}) == []  # an already loaded shape


def test_a_shape_file_of_another_kind_or_a_missing_one_is_an_error_and_not_an_empty_list(tmp_path):
    (tmp_path / "shape.txt").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        compare_shape({}, tmp_path / "shape.txt")
    with pytest.raises(FileNotFoundError):
        compare_shape({}, tmp_path / "absent.json")
    (tmp_path / "empty.ndjson").write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError):
        compare_shape({}, tmp_path / "empty.ndjson")


def test_the_order_of_the_answer_does_not_change_the_report():
    row_a = {"day": "d", "tags": [], "usage": {"output": 1}}
    row_b = {"usage": {"output": 1}, "tags": [], "day": "d"}
    assert compare_shape(answer(data=[row_a]), SHAPE) == compare_shape(answer(data=[row_b]), SHAPE)


def test_compare_shape_is_public_and_documented_in_the_module_docstring():
    assert "compare_shape" in conformance.__all__
    assert "compare_shape" in conformance.__doc__
