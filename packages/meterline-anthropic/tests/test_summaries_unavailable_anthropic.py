"""B1-b: the seat summaries of a day the API has not published yet answer 400 "date is not yet available", as the
per-user activity does. ``daily`` asks for yesterday's summary, so before the API has it (about 13:00 UTC) the run must
not end in an API failure: it keeps the days it can have, and leaves the others unloaded for the next run.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

from meterline_anthropic.cli import main

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)


def _backfill(fake_api, clock, data_dir, since="2026-10-01"):
    return main(["backfill", "--since", since, "--data-dir", str(data_dir)], transport=fake_api.transport, clock=clock,
                sleep=clock.sleep, now=lambda: NOW, jitter=lambda: 0)


def _state(data_dir):
    return json.loads((Path(data_dir) / "state.json").read_text(encoding="utf-8"))


def test_summaries_date_not_available_anthropic_keeps_the_days_before_it(fake_api, fake_clock, tmp_path, capsys):
    fake_api.set_latest_activity_date(date(2026, 10, 3))
    assert _backfill(fake_api, fake_clock, tmp_path) == 0, capsys.readouterr().err
    state = _state(tmp_path)
    assert state["summaries_loaded"] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert state["activity_loaded"] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    asks = [(e.params["starting_date"], e.params["ending_date"]) for e in fake_api.requests_for("summaries")]
    assert asks == [("2026-10-01", "2026-10-05"), ("2026-10-01", "2026-10-04")]  # refused once, then cut at the latest date


def test_summaries_date_not_available_anthropic_nothing_available_yet(fake_api, fake_clock, tmp_path, capsys):
    fake_api.set_latest_activity_date(date(2026, 9, 30))
    assert _backfill(fake_api, fake_clock, tmp_path) == 0, capsys.readouterr().err
    state = _state(tmp_path)
    assert state["summaries_loaded"] == [] and state["activity_loaded"] == []
