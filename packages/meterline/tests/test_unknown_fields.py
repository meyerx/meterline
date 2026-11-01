"""Unknown provider fields are reported, not fatal (review finding A59): the one helper in ``meterline.run`` that the
four normalisers share, and ``RunResult.unknown_fields``.

A normaliser describes the shape it expects; a key the description does not list is counted per endpoint and key path.
The result names keys and counts only, never a value.
"""

import json

from meterline.run import (
    note_unknown_fields,
    result_of_run,
    run_manifests,
    unknown_fields_of,
    warn_unknown_fields,
)

KNOWN = {
    "data": [{"id": None, "usage": {"input": None, "output": None}, "tools": {"*": {"count": None}}, "raw_blob": None}],
    "next_page": None,
}


def _found(node, known=KNOWN, endpoint="report"):
    found = {}
    note_unknown_fields(found, endpoint, node, known)
    return found


def test_unknown_field_none_when_every_key_is_known():
    node = {"data": [{"id": "a", "usage": {"input": 1, "output": 2}, "tools": {"x": {"count": 1}}, "raw_blob": {"any": [1]}}],
            "next_page": None}
    assert _found(node) == {}


def test_unknown_field_is_counted_per_key_path_and_never_carries_a_value():
    planted = "do-not-print-this-value"
    node = {"data": [{"id": "a", "usage": {"input": 1, "output": 2, "cached": planted}, "extra": planted},
                     {"id": "b", "usage": {"input": 1, "output": 2, "cached": planted}}], "added": planted}
    found = _found(node)
    assert found == {("report", "data[].usage.cached"): 2, ("report", "data[].extra"): 1, ("report", "added"): 1}
    assert planted not in json.dumps(unknown_fields_of(found))
    assert planted not in repr(found)


def test_unknown_field_inside_a_data_keyed_map_names_the_wildcard_not_the_key():
    node = {"data": [{"id": "a", "tools": {"edit_tool": {"count": 1, "new": 2}, "write_tool": {"count": 1, "new": 3}}}]}
    found = _found(node)
    assert found == {("report", "data[].tools.*.new"): 2}  # the tool names are data, so they are not in the path


def test_unknown_field_is_not_looked_into_and_a_wrong_shape_is_left_to_the_normaliser():
    node = {"data": "not a list", "next_page": {"nested": 1}, "surprise": {"a": {"b": 1}}}
    assert _found(node) == {("report", "surprise"): 1}


def test_unknown_field_list_is_sorted_with_endpoint_path_and_count():
    found = {("b", "z"): 1, ("a", "y"): 3, ("a", "x"): 2}
    assert unknown_fields_of(found) == [
        {"endpoint": "a", "path": "x", "count": 2}, {"endpoint": "a", "path": "y", "count": 3},
        {"endpoint": "b", "path": "z", "count": 1}]


def test_unknown_field_warning_is_one_line_on_stderr_without_values(capsys):
    entries = unknown_fields_of({("report", "data[].extra"): 4, ("report", "added"): 1})
    warn_unknown_fields("meterline-example", entries)
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and err.startswith("warning:")
    assert "meterline-example" in err and "data[].extra" in err and "added" in err and "4" in err
    assert "Traceback" not in err


def test_unknown_field_warning_is_silent_when_there_is_nothing(capsys):
    warn_unknown_fields("meterline-example", [])
    assert capsys.readouterr().err == ""


def test_unknown_field_warning_lists_at_most_ten_and_counts_the_rest(capsys):
    entries = unknown_fields_of({("e", "f%02d" % n): 1 for n in range(14)})
    warn_unknown_fields("meterline-example", entries)
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "f09" in err and "f10" not in err and "4 more" in err


def test_unknown_field_reaches_the_run_result_from_the_manifest(tmp_path):
    before = run_manifests(tmp_path)
    (tmp_path / "runs").mkdir()
    entries = [{"endpoint": "report", "path": "data[].extra", "count": 2}]
    (tmp_path / "runs" / "b1.json").write_text(json.dumps({"batch_id": "b1", "status": "published", "unknown_fields": entries}))
    result = result_of_run(tmp_path, 0, before)
    assert result.unknown_fields == tuple(entries)
    assert result.ok


def test_unknown_field_is_empty_on_a_manifest_that_has_none(tmp_path):
    before = run_manifests(tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "b1.json").write_text(json.dumps({"batch_id": "b1", "status": "published"}))
    assert result_of_run(tmp_path, 0, before).unknown_fields == ()
    assert result_of_run(tmp_path, 2, run_manifests(tmp_path)).unknown_fields == ()
