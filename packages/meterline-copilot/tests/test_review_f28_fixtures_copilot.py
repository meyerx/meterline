"""Review finding F-28: spec v2 section 7 lists ``tests/fixtures/`` for this
connector. The folder holds a synthetic users-1-day report file built from the
documented row of ``docs/api-shapes/copilot/``; these tests read it, so the
folder is checked rather than decorative.

* each line matches the documented row by keys and nesting (the C01 rules);
* the connector loads the file, served as the report of one day, into one
  credits record per person and one CLI record for each row that has CLI use."""

import json
from decimal import Decimal
from pathlib import Path

from meterline_copilot.testing import FakeAPI
from copilot_helpers import FAKE_BILLING_TOKEN, FAKE_TOKEN, SLUG, daily, records
from meterline.conformance import compare_shape
from test_done_check_C01_shapes import OPTIONAL_ROW

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SHAPES = Path(__file__).resolve().parents[3] / "docs" / "api-shapes" / "copilot"
DAY = "2026-09-18"


def _rows():
    text = (FIXTURES / "users_1_day_report.ndjson").read_text(encoding="utf-8")
    return [json.loads(line, parse_float=Decimal) for line in text.splitlines() if line.strip()]


def test_report_fixture_rows_match_the_documented_row():
    rows = _rows()
    assert len(rows) == 3
    problems = [d for row in rows for d in compare_shape(row, SHAPES / "users_1_day_row.ndjson", optional=OPTIONAL_ROW)]
    assert problems == []
    # the file covers both ends of the optional fields: all present, all absent
    assert OPTIONAL_ROW <= set(rows[0])
    assert not OPTIONAL_ROW & set(rows[1])


def test_report_fixture_rows_are_synthetic():
    assert {row["user_login"] for row in _rows()} == {"login-a", "login-b", "login-c"}
    assert all(row["day"] == DAY for row in _rows())


def test_connector_loads_the_report_fixture(fake_clock, data_dir):
    api = FakeAPI(dataset=None, token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, clock=fake_clock,
                     level="organization", slug=SLUG)
    api.users[DAY] = _rows()
    assert daily(data_dir, api, fake_clock) == 0
    usage = [r for r in records(data_dir, "usage") if r.period_start.isoformat() == DAY and r.grain == "day"]
    credits = {r.actor_id: r.quantities for r in usage if r.product == "copilot"}
    assert credits == {
        "101": {"ai_credit": Decimal("12.5")},
        "102": {"ai_credit": Decimal("3.25")},
        "103": {"ai_credit": Decimal("0.0")},
    }
    cli = {r.actor_id: (r.tokens_input_total, r.tokens_output, r.requests) for r in usage if r.product == "copilot_cli"}
    assert cli == {"101": (3800, 5000, 2), "103": (1400, 600, 2)}
