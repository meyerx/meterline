"""The connector contract (docs/reference/connector-contract.md), checked by the conformance kit."""

import pytest

from meterline.conformance import RULES, Connector, check
from meterline_anthropic_admin import run
from meterline_anthropic_admin.testing import COST_PATH, USAGE_PATH, FakeAPI


def _fail(fake, status, times):
    for path in (USAGE_PATH, COST_PATH):
        fake.fail(path, status, times=times)


def _duplicate(fake):
    """One answer holds a day twice: the second page of the usage report repeats the days of the first."""
    fake.force_limit = 2
    fake.replay(USAGE_PATH)


CONNECTOR = Connector(
    name="meterline-anthropic-admin",
    run=run,
    make_fake=FakeAPI,
    credentials=lambda fake: {"ANTHROPIC_ADMIN_KEY": fake.api_key},
    backfill_options={"since": "2026-09-01", "provider_account_id": "org-one"},
    daily_options={"provider_account_id": "org-one"},
    fail=_fail,
    bad_options={"rate_per_minute": 0},
    duplicate=_duplicate,
)


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_the_connector_keeps_the_contract(rule, tmp_path):
    check(rule, CONNECTOR, tmp_path)
