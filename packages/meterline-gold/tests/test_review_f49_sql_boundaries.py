"""F-49: two comparisons of the gold SQL that no test pinned.

``test_valid_from_boundary_*``: a price list entry whose ``valid_from`` is exactly the day of a usage record applies to
that day (``p.valid_from <= c.period_start`` in 20_person_day.sql, and the same comparison for credit prices and for the
seats entry in force on a window's first day).
``test_alert_threshold_boundary_*``: a position exactly at ``fraction * cap`` (or ``fraction * envelope``) fires the alert
(``p.pos >= threshold`` in 85_alerts.sql), while a stop needs a position strictly above the cap (80_scenarios.sql).

Each test was shown failing against the mutated SQL (``valid_from < period_start``, ``pos > threshold``) before it was kept.
"""

from decimal import Decimal

import pytest
import yaml

from gold_helpers import KEY, copy_fixture_input, edit_policy, read_dicts

DAY_TOKENS = "2026-05-04"   # two anthropic rows, model-x-light, cost_ref 0.297 each
DAY_CREDITS = "2026-05-05"  # one github_copilot row of 40 credits, cost_ref 0.40


def _edit_yaml(path, edit):
    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(obj)
    path.write_text(yaml.safe_dump(obj, sort_keys=True, default_flow_style=False), encoding="utf-8")


def _build(inp, out):
    import meterline_gold

    return meterline_gold.build(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                                invoice=inp["invoice"], key=KEY, out=out)


def _rows(out, name, **where):
    return [r for r in read_dicts(out / "central" / (name + ".csv"))
            if all(r[k] == v for k, v in where.items())]


def _doubled(per_million):
    return {k: str(Decimal(v) * 2) for k, v in per_million.items()}


@pytest.mark.parametrize("valid_from", [DAY_TOKENS, "2026-05-05"])
def test_valid_from_boundary_token_price_applies_on_its_first_day(tmp_path, fixture_out, valid_from):
    """A second price for model-x-light, twice the first, valid from ``valid_from``: it prices the rows of that day
    and not the day before."""
    inp = copy_fixture_input(tmp_path / "in")

    def add_price(obj):
        first = next(t for t in obj["token_prices"] if t["model"] == "model-x-light")
        obj["token_prices"].append(dict(first, valid_from=valid_from, per_million=_doubled(first["per_million"])))

    _edit_yaml(inp["prices"], add_price)
    out = _build(inp, tmp_path / "gold")
    before = {(r["day"], r["person_key"]): Decimal(r["cost_ref"])
              for r in _rows(fixture_out, "person_day", provider="anthropic", model="model-x-light")}
    after = {(r["day"], r["person_key"]): r["cost_ref"]
             for r in _rows(out, "person_day", provider="anthropic", model="model-x-light")}
    assert before and set(before) == set(after)
    on_the_day = [k for k in before if k[0] == valid_from]
    earlier = [k for k in before if k[0] < valid_from]
    assert on_the_day, "no model-x-light row on " + valid_from
    for key in on_the_day:
        assert after[key] != "" and Decimal(after[key]) == before[key] * 2, key
    for key in earlier:
        assert Decimal(after[key]) == before[key], key


@pytest.mark.parametrize("valid_from", [DAY_CREDITS, "2026-05-06"])
def test_valid_from_boundary_credit_price_applies_on_its_first_day(tmp_path, fixture_out, valid_from):
    inp = copy_fixture_input(tmp_path / "in")

    def add_price(obj):
        obj["unit_prices"].append({"provider": "github_copilot", "unit": "ai_credit", "valid_from": valid_from,
                                   "price": "0.02"})

    _edit_yaml(inp["prices"], add_price)
    out = _build(inp, tmp_path / "gold")
    before = {(r["day"], r["person_key"]): Decimal(r["cost_ref"])
              for r in _rows(fixture_out, "person_day", provider="github_copilot", product="copilot")}
    after = {(r["day"], r["person_key"]): Decimal(r["cost_ref"])
             for r in _rows(out, "person_day", provider="github_copilot", product="copilot")}
    on_the_day = [k for k in before if k[0] == DAY_CREDITS]
    assert on_the_day
    factor = 2 if valid_from == DAY_CREDITS else 1
    for key in on_the_day:
        assert after[key] == before[key] * factor, key


@pytest.mark.parametrize("valid_from,price", [("2026-05-01", "43.00"), ("2026-05-02", "21.50")])
def test_valid_from_boundary_seat_price_applies_from_the_first_day_of_the_window(tmp_path, valid_from, price):
    """The seats entry in force on the window's first day: an entry valid from 2026-05-01 is in force for May."""
    inp = copy_fixture_input(tmp_path / "in")

    def add_seat_price(obj):
        obj["seats"].append({"provider": "anthropic", "provider_account_id": None, "valid_from": valid_from, "per_month": "43.00"})

    _edit_yaml(inp["prices"], add_seat_price)
    out = _build(inp, tmp_path / "gold")
    (row,) = _rows(out, "seats_month", provider="anthropic")
    assert row["seat_price"] == price


# ---- the alert threshold ------------------------------------------------------------------------------------


def _person_with_cost_below(fixture_out, limit):
    rows = [r for r in read_dicts(fixture_out / "central" / "person_month.csv") if r["cost_ref"]]
    cost = max(Decimal(r["cost_ref"]) for r in rows if Decimal(r["cost_ref"]) < limit)
    return cost, sorted(r["person_key"] for r in rows if Decimal(r["cost_ref"]) == cost)


def _day_position_reaches(fixture_out, person_key, target):
    by_day = {}
    for r in read_dicts(fixture_out / "central" / "person_day.csv"):
        if r["person_key"] == person_key and r["cost_ref"]:
            by_day[r["day"]] = by_day.get(r["day"], Decimal(0)) + Decimal(r["cost_ref"])
    total = Decimal(0)
    for day in sorted(by_day):
        total += by_day[day]
        if total >= target:
            return day
    raise AssertionError("the person never reaches %s" % target)


def test_alert_threshold_boundary_a_position_exactly_at_the_cap_fires_the_alert_but_does_not_stop(tmp_path, fixture_out):
    cost, keys = _person_with_cost_below(fixture_out, Decimal(100))
    assert cost > 0 and keys
    inp = copy_fixture_input(tmp_path / "in")

    def add_scenario(policy):
        policy["scenarios"].append({
            "id": "edge_persons", "label": "Cap exactly at a month's cost", "unit_envelopes": None,
            "person_caps": {"default": str(cost), "by": None, "values": {}},
            "alert_fractions": ["1.0"], "top_up_amount": None})

    edit_policy(inp["policy"], add_scenario)
    out = _build(inp, tmp_path / "gold")
    events = {r["subject_key"]: r for r in _rows(out, "alert_events", scenario_id="edge_persons")}
    for key in keys:
        assert key in events, "no alert at a position exactly at the cap for " + key
        event = events[key]
        assert event["event_type"] == "scenario_alert" and event["subject_kind"] == "person"
        assert Decimal(event["value"]) == cost and Decimal(event["threshold"]) == cost
        assert event["day"] == _day_position_reaches(fixture_out, key, cost)
        (row,) = _rows(out, "scenario_persons", scenario_id="edge_persons", person_key=key)
        assert row["stopped_on"] == "" and Decimal(row["excess"]) == 0, "a position at the cap is not beyond it"


def test_alert_threshold_boundary_a_position_exactly_at_the_envelope_fires_the_alert_but_does_not_stop(tmp_path,
                                                                                                     fixture_out):
    position = Decimal(next(r["position"] for r in _rows(fixture_out, "scenario_units", unit_id="u02")))
    inp = copy_fixture_input(tmp_path / "in")

    def add_scenario(policy):
        policy["scenarios"].append({
            "id": "edge_units", "label": "Envelope exactly at a month's position", "person_caps": None,
            "unit_envelopes": {"values": {"alpha": str(position)}},
            "alert_fractions": ["1.0"], "top_up_amount": None})

    edit_policy(inp["policy"], add_scenario)
    out = _build(inp, tmp_path / "gold")
    (event,) = _rows(out, "alert_events", scenario_id="edge_units")
    assert event["subject_kind"] == "unit" and event["subject_key"] == "u02"
    assert Decimal(event["value"]) == position and Decimal(event["threshold"]) == position
    (row,) = _rows(out, "scenario_units", scenario_id="edge_units")
    assert row["stopped_on"] == "" and Decimal(row["excess"]) == 0


def test_alert_threshold_boundary_one_cent_over_the_position_does_not_fire(tmp_path, fixture_out):
    """Control: the same scenario with a cap a hair above the position gives no alert, so the tests above fail for
    the comparison and not because the scenario always fires."""
    cost, keys = _person_with_cost_below(fixture_out, Decimal(100))
    inp = copy_fixture_input(tmp_path / "in")

    def add_scenario(policy):
        policy["scenarios"].append({
            "id": "edge_persons", "label": "Cap just above a month's cost", "unit_envelopes": None,
            "person_caps": {"default": str(cost + Decimal("0.000001")), "by": None, "values": {}},
            "alert_fractions": ["1.0"], "top_up_amount": None})

    edit_policy(inp["policy"], add_scenario)
    out = _build(inp, tmp_path / "gold")
    fired = {r["subject_key"] for r in _rows(out, "alert_events", scenario_id="edge_persons")}
    assert not (fired & set(keys))
