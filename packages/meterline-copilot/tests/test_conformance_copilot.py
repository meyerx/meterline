"""The connector contract (docs/reference/connector-contract.md), checked by the conformance kit."""

from datetime import date

import pytest

from meterline.conformance import RULES, Connector, check
from meterline_copilot import complete_through, run
from meterline_copilot.cli import is_complete_day
from meterline_copilot.testing import SAMPLE_TOKEN, FakeAPI

LEVEL = {"level": "organization", "slug": "my-org"}


def _duplicate(fake):
    """One answer holds a row twice: the first user row of the first day that has one is served again."""
    day = next(d for d, rows in sorted(fake.users.items()) if rows)
    fake.users[day].append(dict(fake.users[day][0]))


CONNECTOR = Connector(
    name="meterline-copilot",
    run=run,
    make_fake=FakeAPI,
    credentials={"COPILOT_TOKEN": SAMPLE_TOKEN},
    backfill_options={"since": date(2026, 9, 1), **LEVEL},
    daily_options=LEVEL,
    fail=lambda fake, status, times: fake.fail("", status, times=times),
    bad_options={"rate_per_hour": 0},
    duplicate=_duplicate,
    complete_through=complete_through,
    is_final=is_complete_day,
)


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_the_connector_keeps_the_contract(rule, tmp_path):
    check(rule, CONNECTOR, tmp_path)
