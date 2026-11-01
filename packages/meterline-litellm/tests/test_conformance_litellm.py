"""The connector contract (docs/reference/connector-contract.md), checked by the conformance kit."""

from datetime import date

import pytest

from meterline.conformance import RULES, Connector, check
from meterline_litellm import run
from meterline_litellm.testing import PATH_A, FakeAPI

def _duplicate(fake):
    """One answer holds a day twice: the second page of the user report repeats the rows of the first."""
    fake.page_cap = 20
    fake.replay(PATH_A)


CONNECTOR = Connector(
    name="meterline-litellm",
    run=run,
    make_fake=FakeAPI,
    credentials=lambda fake: {"LITELLM_API_KEY": fake.api_key, "LITELLM_BASE_URL": "https://proxy.example.com"},
    backfill_options={"since": date(2026, 9, 1), "proxy_name": "proxy-one"},
    daily_options={"proxy_name": "proxy-one"},
    fail=lambda fake, status, times: fake.fail(PATH_A, status, times=times),
    bad_options={"rate_per_minute": 0},
    duplicate=_duplicate,
)


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_the_connector_keeps_the_contract(rule, tmp_path):
    check(rule, CONNECTOR, tmp_path)
