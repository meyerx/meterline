"""MF-2: LiteLLM sends spend as a binary float, so a real value has 15 to 19 decimals. The connector keeps the format's
twelve (ROUND_HALF_EVEN) at read, so the record validates, writes to Parquet and loads into gold."""

from decimal import Decimal

import pytest

from litellm_helpers import D1, backfill, latest, manifests, users
from meterline_litellm.testing import row


@pytest.mark.parametrize(
    "sent, held",
    [
        ("0.0027199999999999998", "0.00272"),
        ("1.001818099998933", "1.001818099999"),
        ("0.0000000000005", "0"),  # a tie rounds to the even digit: 0
        ("0.0000000000015", "0.000000000002"),  # a tie rounds to the even digit: 2
        ("0.123456789012", "0.123456789012"),  # twelve decimals pass unchanged
    ],
)
def test_float_spend_is_kept_to_twelve_decimals_litellm(make_proxy, fake_clock, data_dir, capsys, sent, held):
    rows = [row(D1, "user-a", "model-a", prompt_tokens=10, completion_tokens=1, api_requests=1, successful_requests=1,
                spend=Decimal(sent))]
    proxy = make_proxy(rows, users=users("user-a"))
    assert backfill(data_dir, proxy, fake_clock, since=D1) == 0, capsys.readouterr().err
    costs = {r.actor_kind: r.cost for r in latest(data_dir, "usage").values()}
    assert costs["proxy_user"] == Decimal(held)
    assert latest(data_dir, "org_totals").popitem()[1].cost == Decimal(held)
    assert manifests(data_dir)[0]["status"] == "published"
