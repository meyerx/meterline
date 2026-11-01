"""A59: a field the provider sent and the connector does not know is reported, not fatal.

The run publishes as usual and ends with the status it would have had; the manifest lists the field under
``unknown_fields`` (endpoint, key path, count, never a value), one warning goes to stderr, and ``RunResult`` carries the
same list. A required field that is missing or mistyped still fails the run (test_missing_required_fields_copilot.py).
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from copilot_helpers import FAKE_BILLING_TOKEN, FAKE_TOKEN
from meterline_copilot import run
from meterline_copilot.cli import main
from meterline_copilot.testing import FakeAPI

PLANTED = "value-that-must-never-be-printed"
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
CREDENTIALS = {"COPILOT_TOKEN": FAKE_TOKEN, "COPILOT_BILLING_TOKEN": FAKE_BILLING_TOKEN}
HOOKS = {"clock": lambda: 0.0, "sleep": lambda s: None, "wall": lambda: 0.0, "jitter": lambda: 0}


def _fake():
    return FakeAPI(token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN)


def _backfill(data_dir, fake):
    return run("backfill", since=date(2026, 9, 1), level="organization", slug="my-org", data_dir=data_dir,
               credentials=CREDENTIALS, transport=fake.transport, now=lambda: NOW, **HOOKS)


def _manifest(result):
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def _warnings(err):
    return [line for line in err.splitlines() if line.startswith("warning:")]


def _each(rows_by_day):
    for rows in rows_by_day.values():
        for row in (rows if isinstance(rows, list) else [rows]):
            yield row


def _plant_user_row(fake):
    for row in _each(fake.users):
        row["surprise_field"] = PLANTED
        row["totals_by_feature"][0]["surprise_count"] = 1


def _plant_aggregate(fake):
    for row in _each(fake.aggregates):
        row["surprise_field"] = PLANTED


def _plant_billing_item(fake):
    for item in _each(fake.billing_day):
        item["surprise_field"] = PLANTED


def _plant_seat(fake):
    for seat in fake.seats:
        seat["surprise_field"] = PLANTED


def _plant_summary(fake):
    fake.summary["surprise_field"] = PLANTED


@pytest.mark.parametrize("plant,endpoint,paths", [
    (_plant_user_row, "users_1_day", {"surprise_field", "totals_by_feature[].surprise_count"}),
    (_plant_aggregate, "aggregate_1_day", {"surprise_field"}),
    (_plant_billing_item, "billing_day", {"usageItems[].surprise_field"}),
    (_plant_seat, "seats", {"seats[].surprise_field"}),
    (_plant_summary, "seat_summary", {"seat_breakdown.surprise_field"}),
])
def test_unknown_field_is_listed_in_the_manifest_and_the_run_still_publishes(plant, endpoint, paths, tmp_path, capsys):
    fake = _fake()
    plant(fake)
    result = _backfill(tmp_path, fake)
    err = capsys.readouterr().err
    assert result.exit_code == 0 and result.ok and sum(result.records.values()) > 0
    manifest = _manifest(result)
    assert manifest["status"] == "published"
    listed = {(u["endpoint"], u["path"]): u["count"] for u in manifest["unknown_fields"]}
    assert set(listed) == {(endpoint, path) for path in paths} and all(count > 0 for count in listed.values())
    assert result.unknown_fields == tuple(manifest["unknown_fields"])
    assert len(_warnings(err)) == 1 and endpoint in _warnings(err)[0]
    assert PLANTED not in err and PLANTED not in json.dumps(manifest)


def test_unknown_field_none_on_the_documented_shapes(tmp_path, capsys):
    result = _backfill(tmp_path, _fake())
    assert result.ok and result.unknown_fields == ()
    assert _manifest(result)["unknown_fields"] == []
    assert _warnings(capsys.readouterr().err) == []


def test_unknown_field_does_not_change_what_is_published(tmp_path):
    plain, planted = tmp_path / "plain", tmp_path / "planted"
    assert _backfill(plain, _fake()).ok
    fake = _fake()
    _plant_user_row(fake)
    _plant_aggregate(fake)
    assert _backfill(planted, fake).ok

    def lines(root: Path):
        return {p.name: sorted(p.read_text(encoding="utf-8").splitlines()) for p in sorted((root / "records").rglob("*.jsonl"))}

    assert lines(plain) == lines(planted) and lines(plain)


def test_unknown_field_exit_code_through_main_is_still_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COPILOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("COPILOT_BILLING_TOKEN", FAKE_BILLING_TOKEN)
    fake = _fake()
    _plant_user_row(fake)
    code = main(["backfill", "--level", "organization", "--slug", "my-org", "--since", "2026-09-01", "--data-dir", str(tmp_path)],
                transport=fake.transport, now=lambda: NOW, **HOOKS)
    assert code == 0 and len(_warnings(capsys.readouterr().err)) == 1
