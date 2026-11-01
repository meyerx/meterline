"""B1-e: Copilot gaps found in the pre-publication review.

* a seat with a null assignee (the schema allows it) aborted every run and every later daily with exit 2;
* ``ai_credits_used`` is hard-required but exists only since 2026-06-19, so a backfill from before then could die;
* the token families that are read nowhere (``totals_by_client``, ``totals_by_vscode_agent``,
  ``totals_by_3rd_party_agent``) are counted in the run manifest, so the under-count is visible per run;
* the fake and the shape file served the 28-day wrapper for the one-day aggregate report; the real record is flat.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from copilot_helpers import at, backfill, daily, latest, manifests, records
from meterline_copilot.testing import aggregate_row, seat, seat_without_assignee, user_row

SHAPES = Path(__file__).resolve().parents[3] / "docs" / "api-shapes" / "copilot"


def test_seat_null_assignee_copilot(make_api, fake_clock, data_dir, capsys):
    api = make_api()
    api.seats = [seat("login1", 1), seat_without_assignee()]
    assert daily(data_dir, api, fake_clock) == 0, capsys.readouterr().err
    assert [r.actor_login for r in records(data_dir, "seat_assignments")] == ["login1"]
    assert manifests(data_dir)[-1]["seats_without_assignee"] == 1
    assert records(data_dir, "seats")[-1].seats == 2  # the provider's own total counts the seat nobody holds


def test_seat_null_assignee_copilot_does_not_hide_a_missing_page(make_api, fake_clock, data_dir, capsys):
    api = make_api()
    api.seats = [seat("login1", 1), seat_without_assignee()]
    api.total_seats = 4  # two seats are missing from the pages
    assert daily(data_dir, api, fake_clock) == 2
    assert "total_seats" in capsys.readouterr().err


DAY_BEFORE = "2026-06-18"
DAY_FROM = "2026-06-19"
LATER = at("2026-06-22T06:00:00")


def test_ai_credits_optional_before_2026_06_19(make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    api.users[DAY_BEFORE] = [user_row(DAY_BEFORE, 1, "login1", level=level, credits=None),
                             user_row(DAY_BEFORE, 2, "login2", level=level, credits=Decimal("3"))]
    assert backfill(data_dir, api, fake_clock, since=DAY_BEFORE, now=LATER) == 0, capsys.readouterr().err
    credit = {r.actor_id: r for r in records(data_dir, "usage") if r.product == "copilot"}
    assert set(credit) == {"2"} and credit["2"].quantities == {"ai_credit": Decimal("3")}  # absent: no credit quantity
    assert manifests(data_dir)[-1]["ai_credits_absent"] == 1


def test_ai_credits_optional_before_2026_06_19_but_required_from_that_day(make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    api.users[DAY_FROM] = [user_row(DAY_FROM, 1, "login1", level=level, credits=None)]
    assert backfill(data_dir, api, fake_clock, since=DAY_FROM, now=LATER) == 2
    err = capsys.readouterr().err
    assert "ai_credits_used" in err and "nothing published" in err
    assert not list(Path(data_dir).glob("records/*/*.jsonl"))


CLIENT = {"client": "vscode", "request_count": 9, "session_count": 2,
          "token_usage": {"prompt_tokens_sum": 9000, "output_tokens_sum": 1800, "avg_tokens_per_request": Decimal("1200.0")},
          "totals_by_model": [{"model": "model-x", "model_selection_mode": "auto", "request_count": 9,
                               "token_usage": {"prompt_tokens_sum": 9000, "output_tokens_sum": 1800,
                                               "avg_tokens_per_request": Decimal("1200.0")}}]}


def test_unread_token_families_counted_copilot(make_api, fake_clock, data_dir, level, capsys):
    day = "2026-09-18"
    api = make_api()
    api.users[day] = [
        user_row(day, 1, "login1", level=level, cli=True, totals_by_client=[CLIENT]),
        user_row(day, 2, "login2", level=level, totals_by_client=[dict(CLIENT, client="jetbrains")]),
    ]
    assert daily(data_dir, api, fake_clock) == 0, capsys.readouterr().err
    unread = manifests(data_dir)[-1]["unread_token_families"]
    assert unread["totals_by_client.prompt_tokens_sum"] == 18000  # the per-model copies inside are not added again
    assert unread["totals_by_client.output_tokens_sum"] == 3600
    assert unread["totals_by_vscode_agent.prompt_tokens_sum"] == 0 and unread["totals_by_3rd_party_agent.output_tokens_sum"] == 0
    # the tokens in them are not in the records: only the CLI row of login1 carries tokens
    tokens = [(r.actor_id, r.product, r.tokens_input_total) for r in records(data_dir, "usage") if r.tokens_input_total is not None]
    assert tokens == [("1", "copilot_cli", 3800)]  # the fake CLI block's own prompt tokens


def test_unread_token_families_counted_copilot_zero_when_absent(make_api, fake_clock, data_dir, level, capsys):
    day = "2026-09-18"
    api = make_api()
    api.users[day] = [user_row(day, 1, "login1", level=level)]
    assert daily(data_dir, api, fake_clock) == 0, capsys.readouterr().err
    unread = manifests(data_dir)[-1]["unread_token_families"]
    assert sorted(unread) == sorted("%s.%s" % (f, m) for f in ("totals_by_client", "totals_by_vscode_agent", "totals_by_3rd_party_agent")
                                    for m in ("prompt_tokens_sum", "output_tokens_sum"))
    assert set(unread.values()) == {0}


def test_one_day_flat_layout_copilot(make_api, fake_clock, data_dir, level, capsys):
    day = "2026-09-18"
    row = aggregate_row(day, 3, 5, 7)
    assert "day_totals" not in row and "report_start_day" not in row  # the one-day record is flat
    assert (row["day"], row["daily_active_users"], row["weekly_active_users"], row["monthly_active_users"]) == (day, 3, 5, 7)
    api = make_api()
    api.aggregates[day] = row
    assert daily(data_dir, api, fake_clock) == 0, capsys.readouterr().err
    snap = latest(data_dir, "seats")
    assert [(s.daily_active, s.weekly_active, s.monthly_active) for s in snap.values() if s.period_start.isoformat() == day] == [(3, 5, 7)]
    assert day not in manifests(data_dir)[-1]["aggregate_missing"]


def test_one_day_flat_layout_copilot_shape_file():
    first = json.loads((SHAPES / "aggregate_1_day_row.ndjson").read_text(encoding="utf-8").splitlines()[0])
    assert "day_totals" not in first and "report_start_day" not in first
    assert {"day", "daily_active_users", "weekly_active_users", "monthly_active_users"} <= set(first)
