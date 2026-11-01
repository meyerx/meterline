"""The connector contract (docs/reference/connector-contract.md), checked by the conformance kit."""

from datetime import date

import pytest

from meterline.conformance import RULES, Connector, check
from meterline_anthropic import run
from meterline_anthropic.testing import FakeAPI

ENDPOINTS = ("user_usage_report", "user_cost_report", "usage_report", "cost_report", "users", "summaries")
KEY = "synthetic-key-not-real"


def _fail(fake, status, times):
    for endpoint in ENDPOINTS:
        fake.fail_page(endpoint, 1, status, times=times)


def _duplicate(fake):
    """One answer holds a row twice: the first activity row of the first day that has one is served again."""
    def twice(dataset):
        day = next(d for d, rows in sorted(dataset["activity"].items()) if rows)
        dataset["activity"][day].append(dict(dataset["activity"][day][0]))

    fake.mutate(twice)


CONNECTOR = Connector(
    name="meterline-anthropic",
    run=run,
    make_fake=lambda: FakeAPI(api_key=KEY),
    credentials=lambda fake: {"ANTHROPIC_ANALYTICS_KEY": fake.api_key},
    backfill_options={"since": date(2026, 9, 1)},
    fail=_fail,
    bad_options={"rate_per_minute": 0},
    duplicate=_duplicate,
)


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_the_connector_keeps_the_contract(rule, tmp_path):
    check(rule, CONNECTOR, tmp_path)
