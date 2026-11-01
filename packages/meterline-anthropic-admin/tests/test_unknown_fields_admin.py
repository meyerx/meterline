"""A59: a field the provider sent and the connector does not know is reported, not fatal.

The run publishes as usual and ends with the status it would have had; the manifest lists the field under
``unknown_fields`` (endpoint, key path, count, never a value), one warning goes to stderr, and ``RunResult`` carries the
same list. A required field that is missing or mistyped still fails the run (test_done_check_A04_A09_records.py).
"""

import json
from datetime import date, datetime, timezone

import pytest

from admin_helpers import FAKE_KEY, ORG, backfill, basic_cost, basic_usage, manifests, no_records, records
from meterline_anthropic_admin import run
from meterline_anthropic_admin.testing import FakeAPI

PLANTED = "value-that-must-never-be-printed"
NOW = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)


def _warnings(err):
    return [line for line in err.splitlines() if line.startswith("warning:")]


@pytest.mark.parametrize("which,endpoint,path", [
    ("usage", "usage_report_messages", "data[].results[].surprise_field"),
    ("cost", "cost_report", "data[].results[].surprise_field"),
])
def test_unknown_field_is_listed_in_the_manifest_and_the_run_still_publishes(make_api, fake_clock, data_dir, capsys, which, endpoint, path):
    api = make_api(basic_usage(), basic_cost())
    getattr(api, "extra_%s_fields" % which)["surprise_field"] = PLANTED
    assert backfill(data_dir, api, fake_clock) == 0
    err = capsys.readouterr().err
    assert not no_records(data_dir) and records(data_dir, "usage")
    manifest = manifests(data_dir)[-1]
    assert manifest["status"] == "published" and manifest["failure_kind"] == "none"
    rows = manifest["row_counts"]["%s_rows" % which]
    assert manifest["unknown_fields"] == [{"endpoint": endpoint, "path": path, "count": rows}] and rows > 0
    assert len(_warnings(err)) == 1 and path in _warnings(err)[0] and endpoint in _warnings(err)[0]
    assert PLANTED not in err and PLANTED not in json.dumps(manifest)


def test_unknown_field_inside_a_known_object_is_listed_with_its_full_key_path(make_api, fake_clock, data_dir, capsys):
    api = make_api(basic_usage(), basic_cost())
    api.extra_usage_fields["cache_creation"] = {
        "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0, "ephemeral_24h_input_tokens": 7}
    api.extra_usage_fields["server_tool_use"] = {"web_search_requests": 0, "code_runs": 1}
    assert backfill(data_dir, api, fake_clock) == 0
    paths = {item["path"] for item in manifests(data_dir)[-1]["unknown_fields"]}
    assert paths == {"data[].results[].cache_creation.ephemeral_24h_input_tokens", "data[].results[].server_tool_use.code_runs"}
    assert len(_warnings(capsys.readouterr().err)) == 1


def test_unknown_field_none_on_the_documented_shapes(make_api, fake_clock, data_dir, capsys):
    assert backfill(data_dir, make_api(basic_usage(), basic_cost()), fake_clock) == 0
    assert manifests(data_dir)[-1]["unknown_fields"] == []
    assert _warnings(capsys.readouterr().err) == []


def test_unknown_field_reaches_the_run_result_of_the_python_entry(tmp_path, capsys):
    api = FakeAPI(api_key=FAKE_KEY)
    api.extra_usage_fields["surprise_field"] = PLANTED
    result = run("backfill", since=date(2026, 9, 1), provider_account_id=ORG, data_dir=tmp_path, credentials={"ANTHROPIC_ADMIN_KEY": FAKE_KEY},
                 transport=api.transport, now=lambda: NOW)
    assert result.ok and result.exit_code == 0
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.unknown_fields == tuple(manifest["unknown_fields"]) and len(result.unknown_fields) == 1
    assert result.unknown_fields[0]["path"] == "data[].results[].surprise_field" and result.unknown_fields[0]["count"] > 0
    assert PLANTED not in capsys.readouterr().err


def test_unknown_field_does_not_change_what_is_published(make_api, fake_clock, tmp_path):
    plain, planted = tmp_path / "plain", tmp_path / "planted"
    assert backfill(plain, make_api(basic_usage(), basic_cost()), fake_clock) == 0
    api = make_api(basic_usage(), basic_cost())
    api.extra_usage_fields["surprise_field"] = PLANTED
    api.extra_cost_fields["surprise_field"] = PLANTED
    assert backfill(planted, api, fake_clock) == 0

    def lines(root):
        return {p.name: sorted(p.read_text(encoding="utf-8").splitlines()) for p in sorted((root / "records").rglob("*.jsonl"))}

    assert lines(plain) == lines(planted) and lines(plain)
