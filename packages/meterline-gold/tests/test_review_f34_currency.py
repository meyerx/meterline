"""F-34: no sum crosses currencies, a currency is an ISO 4217 code, and the dashboard names the currency and UTC.

Gold refuses a mixed input with a named error (exit 2) instead of summing it: the silver ``cost`` columns are
summed per (provider, org, month), and the reconciliation subtracts the reference cost from them, so one currency
for the whole build is the only input on which those sums mean anything.
"""

import json
import re
from pathlib import Path

import pytest

from gold_helpers import KEY, build_cli, copy_fixture_input, edit_policy, fixture_inputs, read_dicts


def _rewrite_silver(inp, kinds, edit):
    """Apply ``edit(obj)`` to every record of the given silver kinds in a copy of the fixture."""
    for kind in kinds:
        for path in sorted((Path(inp["silver"]) / "records" / kind).glob("*.jsonl")):
            lines = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    obj = json.loads(line)
                    edit(obj)
                    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                lines.append(line)
            path.write_text("".join(l + "\n" for l in lines), encoding="utf-8")


def _build(inp, out):
    import meterline_gold

    return meterline_gold.build(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY, out=out)


def _set_currency(code):
    def edit(obj):
        if obj.get("cost") is not None or obj.get("cost_list") is not None:
            obj["currency"] = code
    return edit


def test_currency_not_mixed_two_currencies_in_silver_are_refused(tmp_path):
    from meterline_gold import InputError

    inp = copy_fixture_input(tmp_path / "in")
    seen = []

    def one_euro_record(obj):
        # one cost-bearing Anthropic record in EUR; every other record stays USD
        if obj["provider"] == "anthropic" and obj.get("cost") is not None and not seen:
            obj["currency"] = "EUR"
            seen.append(obj["period_start"])

    _rewrite_silver(inp, ["usage"], one_euro_record)
    assert seen, "the fixture has no cost-bearing Anthropic usage record to change"
    with pytest.raises(InputError) as raised:
        _build(inp, tmp_path / "gold")
    message = str(raised.value)
    assert "EUR" in message and "USD" in message and "currenc" in message
    assert not (tmp_path / "gold").exists() and not (tmp_path / "gold.tmp").exists()


def test_currency_not_mixed_cli_exits_2_and_names_the_currencies(tmp_path, monkeypatch, capsys):
    inp = copy_fixture_input(tmp_path / "in")
    _rewrite_silver(inp, ["org_totals"], lambda o: o.update(currency="EUR") if o.get("cost") is not None
                    and o["provider"] == "anthropic" else None)
    code = build_cli(inp, tmp_path / "gold", monkeypatch)
    err = capsys.readouterr().err
    assert code == 2
    assert "EUR" in err and "USD" in err
    assert not (tmp_path / "gold").exists()


def test_currency_not_mixed_silver_in_another_currency_than_the_reference_is_refused(tmp_path):
    """One currency in silver that is not the policy's: the reference-versus-source gap would subtract across."""
    from meterline_gold import InputError

    inp = copy_fixture_input(tmp_path / "in")
    _rewrite_silver(inp, ["usage", "org_totals"], _set_currency("EUR"))
    with pytest.raises(InputError) as raised:
        _build(inp, tmp_path / "gold")
    message = str(raised.value)
    assert "EUR" in message and "USD" in message and "reference" in message


def test_currency_not_mixed_one_consistent_currency_builds_and_is_written(tmp_path):
    """Positive control: nothing is tied to USD. Policy, price list, invoice and silver all in EUR build."""
    inp = copy_fixture_input(tmp_path / "in")
    edit_policy(inp["policy"], lambda p: p.update(currency="EUR"))
    prices = Path(inp["prices"])
    prices.write_text(prices.read_text(encoding="utf-8").replace("currency: USD", "currency: EUR"), encoding="utf-8")
    invoice = Path(inp["invoice"])
    invoice.write_text(invoice.read_text(encoding="utf-8").replace(",USD,", ",EUR,"), encoding="utf-8")
    _rewrite_silver(inp, ["usage", "org_totals"], _set_currency("EUR"))
    out = _build(inp, tmp_path / "gold")
    for name in ("costs_month", "reconciliation_month", "seats_month"):
        currencies = {r["currency"] for r in read_dicts(out / "central" / (name + ".csv"))}
        assert currencies == {"EUR"}, name
    assert {r["cost_source_currency"] for r in read_dicts(out / "central" / "person_day.csv")
            if r["cost_source"]} == {"EUR"}


@pytest.mark.parametrize("code", ["usd", "ZZZ", "EURO", "US", "123", " USD"])
def test_currency_not_mixed_silver_currency_must_be_an_iso_4217_code(tmp_path, code):
    from meterline_gold import InputError

    inp = copy_fixture_input(tmp_path / "in")
    _rewrite_silver(inp, ["usage", "org_totals"], _set_currency(code))
    with pytest.raises(InputError) as raised:
        _build(inp, tmp_path / "gold")
    assert "ISO 4217" in str(raised.value)


@pytest.mark.parametrize("code", ["usd", "ZZZ", "DOLLAR"])
def test_currency_not_mixed_policy_and_price_list_currency_must_be_an_iso_4217_code(tmp_path, code):
    """The policy and the price list agree with each other (so the existing equality check passes) but the code is bad."""
    from meterline_gold import InputError

    inp = copy_fixture_input(tmp_path / "in")
    edit_policy(inp["policy"], lambda p: p.update(currency=code))
    prices = Path(inp["prices"])
    prices.write_text(prices.read_text(encoding="utf-8").replace("currency: USD", "currency: " + code),
                      encoding="utf-8")
    with pytest.raises(InputError) as raised:
        _build(inp, tmp_path / "gold")
    assert "ISO 4217" in str(raised.value)


def test_currency_not_mixed_the_code_list_has_the_common_currencies_and_no_lower_case():
    from meterline_gold import iso4217

    for code in ("USD", "EUR", "CHF", "GBP", "JPY", "SEK", "NOK", "DKK", "CAD", "AUD", "INR", "BRL", "CNY"):
        assert iso4217.is_iso_4217(code), code
    for code in ("usd", "", "US", "USDD", "ZZZ", "XXX", None):
        assert not iso4217.is_iso_4217(code), code


def test_currency_not_mixed_the_example_files_use_a_listed_code():
    from meterline_gold import iso4217

    base = Path(fixture_inputs()["policy"]).parent
    assert re.search(r"^currency: (\w+)$", (base / "policy.yaml").read_text(encoding="utf-8"), re.M).group(1) \
        in iso4217.CODES


# ---- the dashboard: currency next to every money column, and the time basis -------------------------------


def _dashboard_text():
    import meterline_gold

    return (Path(meterline_gold.__file__).resolve().parent / "dashboard" / "index.html").read_text(encoding="utf-8")


def test_currency_not_mixed_dashboard_states_utc():
    text = _dashboard_text()
    assert "UTC" in text
    assert re.search(r"days and months[^\"']*UTC", text)


def test_currency_not_mixed_dashboard_money_columns_follow_the_money_columns_of_the_schemas():
    """The dashboard's list of money columns per file equals the columns the writer types as money."""
    from meterline_gold import write

    text = _dashboard_text()
    match = re.search(r"var MONEY = (\{.*?\n\});", text, re.S)
    assert match, "the dashboard has no MONEY table"
    listed = json.loads(match.group(1))
    schema_for = {"costs_month": "costs_month", "seats_month": "seats_month", "pools_month": "pools_month",
                  "reconciliation_month": "reconciliation_month", "person_month": "person_month",
                  "person_day": "person_day", "groups_month": "groups_month",
                  "comparison_groups": "comparison_groups", "comparison": "comparison",
                  "scenario_persons": "scenario_persons", "scenario_units": "scenario_units",
                  "scenario_summary": "scenario_summary", "alert_events": "alert_events"}
    assert set(listed) == set(schema_for)
    for name, schema_name in schema_for.items():
        expected = [c for c, kind in write.SCHEMAS[schema_name] if kind in ("money", "map_money")]
        assert sorted(listed[name]) == sorted(expected), name


def test_currency_not_mixed_dashboard_labels_money_headers_with_the_currency():
    text = _dashboard_text()
    # the currency comes from the `currency` column of a file of the folder; without one the header says so
    assert "currencyOf" in text
    assert "reference currency" in text
