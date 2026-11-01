"""A04 to A09 (spec section 8): usage rows to records, the console key, cents to dollars, the other products,
the workspace collision and the shapes the connector cannot use. A field it does not know is no longer one of them:
it is reported, and the run publishes (test_unknown_fields_admin.py)."""

import json
from decimal import Decimal

from admin_helpers import (D1, D2, NOW, ORG, backfill, basic_cost, basic_usage, manifests, no_records, records,
                           totals_by_key, usage_by_key)
from meterline_anthropic_admin.testing import crow, urow
from meterline.records import UNASSIGNED_ID, validate


def test_done_check_A04_one_usage_record_per_day_key_and_model(make_api, fake_clock, data_dir):
    assert backfill(data_dir, make_api(basic_usage(), basic_cost()), fake_clock) == 0
    got = usage_by_key(data_dir)
    assert set(got) == {("apikey_a", D1, "model-a"), ("apikey_a", D1, "model-b"), ("apikey_b", D1, "model-a"),
                        (UNASSIGNED_ID, D1, "model-a"), ("apikey_a", D2, "model-a")}
    rec = got[("apikey_a", D1, "model-a")]
    assert (rec.provider, rec.provider_account_id, rec.scope, rec.grain, rec.product) == ("anthropic", ORG, "", "day", "api")
    assert rec.actor_kind == "api_key" and rec.period_start == rec.period_end
    # four disjoint input and output buckets, and the sums the format checks
    assert (rec.tokens_input_uncached, rec.tokens_cache_read, rec.tokens_cache_write_5m, rec.tokens_cache_write_1h) == (1000, 600, 300, 100)
    assert rec.tokens_cache_write == 400 == rec.tokens_cache_write_5m + rec.tokens_cache_write_1h
    assert rec.tokens_input_total == 2000 == rec.tokens_input_uncached + rec.tokens_cache_read + rec.tokens_cache_write
    assert rec.tokens_output == 50
    assert rec.extras == {"web_search_requests": 2}
    assert (rec.cost, rec.cost_list, rec.cost_basis, rec.currency, rec.requests) == (None, None, None, "", None)
    for each in records(data_dir, "usage") + records(data_dir, "org_totals"):
        validate(each)
    # the org total of a model is the sum of its usage records
    total = totals_by_key(data_dir)[("api", D1, "model-a")]
    assert total.tokens_input_uncached == 1450 and total.tokens_cache_read == 700 and total.tokens_output == 95
    assert total.tokens_cache_write_5m == 300 and total.tokens_cache_write_1h == 100 and total.tokens_input_total == 2550


def test_done_check_A04_the_service_tiers_of_one_key_and_model_are_summed_and_priority_is_named(make_api, fake_clock, data_dir):
    rows = [urow(D1, "apikey_a", "model-a", uncached=100, out=10), urow(D1, "apikey_a", "model-a", tier="priority", uncached=30, out=5)]
    assert backfill(data_dir, make_api(rows), fake_clock) == 0
    rec = usage_by_key(data_dir)[("apikey_a", D1, "model-a")]
    assert (rec.tokens_input_uncached, rec.tokens_output) == (130, 15)
    assert rec.extras["priority_tokens"] == 35  # Priority Tier has no cost line, so its tokens are named


def test_done_check_A05_a_null_api_key_is_unassigned_console_usage(make_api, fake_clock, data_dir):
    assert backfill(data_dir, make_api(basic_usage(), basic_cost()), fake_clock) == 0
    rec = usage_by_key(data_dir)[(UNASSIGNED_ID, D1, "model-a")]
    # spec A05 says actor_id "console"; meterline's validation requires "(unassigned)" for actor_kind unassigned
    assert rec.actor_kind == "unassigned" and rec.actor_id == UNASSIGNED_ID
    assert (rec.tokens_input_uncached, rec.tokens_output) == (50, 5)


def test_done_check_A06_cost_in_cents_becomes_a_decimal_in_dollars(make_api, fake_clock, data_dir):
    rows = [urow(D1, "apikey_a", "model-a", uncached=10)]
    assert backfill(data_dir, make_api(rows, [crow(D1, "model-a", "uncached_input_tokens", "123.78912")]), fake_clock) == 0
    total = totals_by_key(data_dir)[("api", D1, "model-a")]
    assert total.cost == Decimal("1.2378912") and type(total.cost) is Decimal
    assert (total.currency, total.cost_basis) == ("USD", "billed")
    assert '"cost": "1.2378912"' in "".join(p.read_text(encoding="utf-8") for p in (data_dir / "records" / "org_totals").glob("*.jsonl"))


def test_done_check_A06_token_cost_rows_of_a_model_and_day_are_summed(make_api, fake_clock, data_dir):
    assert backfill(data_dir, make_api(basic_usage(), basic_cost()), fake_clock) == 0
    totals = totals_by_key(data_dir)
    assert totals[("api", D1, "model-a")].cost == Decimal("1.3428912")  # 123.78912 + 10.5 cents
    assert totals[("api", D2, "model-a")].cost == Decimal("0.40")
    # a model with usage and no cost row has no cost: not reported, not zero
    nothing = totals[("api", D1, "model-b")]
    assert (nothing.cost, nothing.cost_basis, nothing.currency) == (None, None, "") and nothing.tokens_output == 20
    # no usage record carries a cost: the API does not report it per key
    assert all(r.cost is None for r in records(data_dir, "usage"))


def test_done_check_A07_other_cost_types_are_their_own_products(make_api, fake_clock, data_dir):
    cost = basic_cost() + [crow(D1, None, None, "7.5", cost_type="code_execution"), crow(D1, None, None, "1", cost_type="web_search")]
    assert backfill(data_dir, make_api(basic_usage(), cost), fake_clock) == 0
    totals = totals_by_key(data_dir)
    web = totals[("api-web-search", D1, "")]
    assert web.cost == Decimal("0.06") and web.tokens_input_total is None and web.cost_basis == "billed"  # 5 + 1 cents
    code = totals[("api-code-execution", D1, "")]
    assert code.cost == Decimal("0.075") and code.currency == "USD"
    assert ("api-web-search", D2, "") not in totals
    assert {k[0] for k in totals} == {"api", "api-web-search", "api-code-execution"}


def test_done_check_A08_a_key_in_two_workspaces_fails_the_batch_and_names_the_key(make_api, fake_clock, data_dir, capsys):
    rows = basic_usage() + [urow(D1, "apikey_a", "model-a", ws="wrkspc_b", uncached=5)]
    assert backfill(data_dir, make_api(rows, basic_cost()), fake_clock) == 2
    assert no_records(data_dir)
    assert "apikey_a" in capsys.readouterr().err
    failed = manifests(data_dir)[-1]
    assert failed["status"] == "failed" and any("apikey_a" in f for f in failed["failures"])
    assert not (data_dir / "state.json").exists() or json.loads((data_dir / "state.json").read_text())["days_loaded"] == []


def test_done_check_A09_a_missing_token_field_fails_the_batch_it_is_never_zero(make_api, fake_clock, data_dir, capsys):
    api = make_api(basic_usage(), basic_cost())
    api.drop_usage_fields = ["output_tokens"]
    assert backfill(data_dir, api, fake_clock) == 2
    assert no_records(data_dir)
    assert "output_tokens" in capsys.readouterr().err


def test_done_check_A09_an_unknown_cost_type_or_currency_fails_the_batch(make_api, fake_clock, data_dir, tmp_path, capsys):
    odd_type = basic_cost() + [crow(D1, None, None, "1", cost_type="teleportation")]
    assert backfill(data_dir, make_api(basic_usage(), odd_type), fake_clock) == 2
    assert "cost_type" in capsys.readouterr().err
    api = make_api(basic_usage(), basic_cost())
    api.extra_cost_fields = {"currency": "EUR"}
    assert backfill(tmp_path / "eur", api, fake_clock) == 2
    assert "currency" in capsys.readouterr().err
    assert no_records(data_dir) and no_records(tmp_path / "eur")


def test_done_check_A09_a_bad_amount_fails_the_batch(make_api, fake_clock, data_dir, capsys):
    api = make_api(basic_usage(), basic_cost())
    api.extra_cost_fields = {"amount": "twelve"}
    assert backfill(data_dir, api, fake_clock) == 2
    assert "amount" in capsys.readouterr().err and no_records(data_dir)


def test_done_check_A09_a_token_cost_row_without_a_model_fails_the_batch(make_api, fake_clock, data_dir, capsys):
    assert backfill(data_dir, make_api(basic_usage(), [crow(D1, None, "output_tokens", "3")]), fake_clock) == 2
    assert "model" in capsys.readouterr().err and no_records(data_dir)
