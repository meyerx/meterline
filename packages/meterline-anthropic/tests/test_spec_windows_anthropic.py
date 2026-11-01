"""F-30: the connector keeps to the spec's windows and shapes: an empty
bucket is data (cost null, currency empty) and does not fail the day; a start
earlier than 2026-01-01 or more than 365 days back is refused; a summaries
range is cut at 366 days.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from meterline.records import OrgTotal
from meterline.writers import read_jsonl
from meterline_anthropic.cli import main
from meterline_anthropic.client import AnalyticsClient
from meterline_anthropic.fetch import fetch_summaries_windows

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)


def _run(api, clock, data_dir, *argv, now=NOW):
    return main([*argv, "--data-dir", str(data_dir)], transport=api.transport, clock=clock, sleep=clock.sleep,
                now=lambda: now, jitter=lambda: 0)


def _org_totals(data_dir):
    out = []
    for path in sorted((Path(data_dir) / "records" / "org_totals").glob("*.jsonl")):
        out.extend(read_jsonl(path, OrgTotal))
    return {r.period_start: r for r in out}


def test_empty_bucket_does_not_fail_the_run_and_has_no_cost(fake_api, fake_clock, tmp_path):
    def empty_day(ds):
        ds["org_usage"]["2026-10-02"] = []
        ds["org_cost"]["2026-10-02"] = []

    fake_api.mutate(empty_day)
    assert _run(fake_api, fake_clock, tmp_path, "backfill", "--since", "2026-10-01") == 0
    totals = _org_totals(tmp_path)
    quiet, busy = totals[date(2026, 10, 2)], totals[date(2026, 10, 1)]
    assert quiet.cost is None and quiet.cost_list is None and quiet.cost_basis is None and quiet.currency == ""
    assert quiet.tokens_input_total == 0 and quiet.requests is None
    assert busy.cost is not None and busy.currency == "USD"


def test_empty_cost_bucket_beside_a_usage_bucket_has_no_cost(fake_api, fake_clock, tmp_path):
    fake_api.mutate(lambda ds: ds["org_cost"].__setitem__("2026-10-02", []))
    assert _run(fake_api, fake_clock, tmp_path, "backfill", "--since", "2026-10-01") == 0
    quiet = _org_totals(tmp_path)[date(2026, 10, 2)]
    assert quiet.cost is None and quiet.currency == "" and quiet.tokens_input_total > 0


@pytest.mark.parametrize("command", ["backfill", "daily"])
def test_since_before_the_data_starts_is_refused(command, fake_api, fake_clock, tmp_path, capsys):
    rc = _run(fake_api, fake_clock, tmp_path, command, "--since", "2025-12-31")
    err = capsys.readouterr().err
    assert rc == 2
    assert "2026-01-01" in err and "--since" in err and "Traceback" not in err
    assert fake_api.log == [], "nothing is requested"
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("command", ["backfill", "daily"])
def test_since_more_than_365_days_back_is_refused(command, fake_api, fake_clock, tmp_path, capsys):
    now = datetime(2027, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
    rc = _run(fake_api, fake_clock, tmp_path, command, "--since", "2026-05-31", now=now)  # 366 days back
    err = capsys.readouterr().err
    assert rc == 2
    assert "365 days" in err
    assert fake_api.log == []


@pytest.mark.parametrize("command", ["backfill", "daily"])
def test_since_exactly_365_days_back_is_refused(command, fake_api, fake_clock, tmp_path, capsys):
    # midnight of the start day is then more than 365 days before now, which the API refuses
    now = datetime(2027, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
    rc = _run(fake_api, fake_clock, tmp_path, command, "--since", "2026-06-01", now=now)  # 365 days back
    err = capsys.readouterr().err
    assert rc == 2
    assert "365 days" in err and "Use 2026-06-02 or later" in err
    assert fake_api.log == []


def test_since_364_days_back_and_on_the_first_day_is_allowed(fake_api, fake_clock, tmp_path):
    now = datetime(2027, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
    assert _run(fake_api, fake_clock, tmp_path, "backfill", "--since", "2026-06-02", now=now) == 0
    other = tmp_path / "second"
    assert _run(fake_api, fake_clock, other, "backfill", "--since", "2026-10-01") == 0


def test_a_summaries_range_is_cut_at_366_days(fake_api, fake_clock, tmp_path, fake_key_env):
    with AnalyticsClient(fake_key_env, transport=fake_api.transport, clock=fake_clock, sleep=fake_clock.sleep,
                         jitter=lambda: 0) as client:
        pulls = fetch_summaries_windows(client, tmp_path, date(2025, 1, 1), date(2026, 10, 8), NOW,
                                        provider_account_id="org-synthetic-0001")
    asks = [(e.params["starting_date"], e.params["ending_date"]) for e in fake_api.requests_for("summaries")]
    assert len(asks) == len(pulls) == 2
    for first, end in asks:
        assert (date.fromisoformat(end) - date.fromisoformat(first)).days <= 366
    assert asks[0][0] == "2025-01-01" and asks[1][0] == (date.fromisoformat(asks[0][1])).isoformat()
    assert asks[-1][1] == "2026-10-09"


def test_pulled_at_whole_seconds_anthropic(fake_api, fake_clock, tmp_path):
    """F-31: a clock with a fraction is cut to whole seconds, so the records, the manifest and the batch id agree
    and a rebuild reproduces what was published."""
    micro = NOW.replace(microsecond=123456)
    assert _run(fake_api, fake_clock, tmp_path, "backfill", "--since", "2026-10-01", now=micro) == 0
    totals = _org_totals(tmp_path)
    assert totals and all(r.pulled_at.microsecond == 0 for r in totals.values())
    manifests = sorted((tmp_path / "runs").glob("*.json"))
    assert len(manifests) == 1 and "." not in manifests[0].stem
    assert "." not in json.loads(manifests[0].read_text())["pulled_at"]
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / "records").rglob("*.jsonl")}
    assert main(["rebuild", "--data-dir", str(tmp_path)], transport=fake_api.transport, clock=fake_clock,
                sleep=fake_clock.sleep, now=lambda: micro, jitter=lambda: 0) == 0
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in (tmp_path / "records").rglob("*.jsonl")}
    assert before and before == after
