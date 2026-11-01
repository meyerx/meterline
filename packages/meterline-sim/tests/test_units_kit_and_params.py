"""Unit tests beside the done-checks: the draw kit, parameters, calendar, pricing and the two product decisions."""

import json
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest

import meterline_sim
from meterline_sim import draws
from meterline_sim.behaviour import Calendar, split_cache
from meterline_sim.events import month_ranges, period_end
from meterline_sim.params import ParamError, load_defaults, resolve_params, scaled
from meterline_sim.prices import amount, load_price_table
from sim_helpers import (
    BASE_PARAMS, COPILOT_ENABLED, COPILOT_OFF, SRC_DIR, copilot_actor_id, gen, load_records, merged, pull_batch_ids,
    read_csv, read_json,
)


def test_the_package_under_test_is_this_checkouts_source():
    # a venv's editable install can point at another checkout; the conftest pins this one, in
    # this process and in the subprocesses of S01
    from pathlib import Path

    assert Path(meterline_sim.__file__).resolve().is_relative_to(SRC_DIR.parent)
    out = subprocess.run([sys.executable, "-c", "import meterline_sim; print(meterline_sim.__file__)"],
                         check=True, capture_output=True, text=True).stdout.strip()
    assert Path(out).resolve().is_relative_to(SRC_DIR.parent)


def test_stream_derivation_and_kit_golden_values():
    # recorded from the reference implementation: a change here changes every output byte
    assert draws.derive_stream(7, "org").random() == 0.533314127252138
    r = draws.derive_stream(7, "kit")
    assert draws.normal(r) == -0.13168546355268568
    assert draws.lognorm(r, "10", "0.5") == 19.811581398517536
    assert draws.count(r, "6", "0.6", 1) == 10
    assert draws.stoch_round(r, Decimal("2.4")) == 2
    assert draws.pick(r, ["a", "b", "c"], ["1", "2", "0"]) == "b"
    assert draws.shuffle(r, list(range(6))) == [2, 3, 0, 4, 5, 1]
    assert draws.uniform_int(r, 3, 9) == 4


def test_pick_never_returns_a_zero_weight_item():
    r = draws.derive_stream(1, "pick")
    for _ in range(500):
        assert draws.pick(r, ["a", "b", "c"], ["0", "1", "0"]) == "b"


def test_stoch_round_is_unbiased_and_draws_once():
    r = draws.derive_stream(2, "round")
    values = [draws.stoch_round(r, Decimal("2.25")) for _ in range(4000)]
    assert set(values) == {2, 3}
    assert abs(sum(values) / len(values) - 2.25) < 0.05


def test_fractional_numbers_are_rejected_in_parameters():
    with pytest.raises(ParamError):
        meterline_sim.params.parse_json('{"pull": {"lag_days": 3.5}}', "x")
    with pytest.raises(ParamError):
        meterline_sim.generate("tiny", 1, months=1, out="unused", params={"pull": {"lag_days": 3.5}})


def test_unknown_parameter_is_an_error_but_a_new_service_account_is_not():
    with pytest.raises(ParamError):
        resolve_params({}, {"behaviour": {"nope": {}}})
    account = {"model": "model-b-light", "req_median": "10", "req_sigma": "0.3", "in_median": "100",
               "out_median": "10", "f_read": "0", "f_write": "0", "failure_rate": "0"}
    out = resolve_params({}, {"behaviour": {"service_accounts": {"sa09": account}}})
    assert "sa09" in out["behaviour"]["service_accounts"]
    with pytest.raises(ParamError):
        resolve_params({}, {"behaviour": {"service_accounts": {"sa10": {"model": "x"}}}})


def test_merge_replaces_lists_and_scalars_and_merges_objects():
    out = resolve_params({"providers": {"litellm": {"accounts": ["sa01"]}}},
                         {"providers": {"litellm": {"accounts": ["sa02"], "proxy_name": "other"}}})
    assert out["providers"]["litellm"]["accounts"] == ["sa02"]
    assert out["providers"]["litellm"]["proxy_name"] == "other"
    assert out["providers"]["anthropic"]["active_windows"] == [7, 30]


def test_defaults_name_zero_as_the_unpriced_cost():
    assert load_defaults()["providers"]["litellm"]["unpriced_cost"] == "zero"


def test_period_months_and_ranges():
    assert period_end(date(2026, 4, 1), 6) == date(2026, 9, 30)
    assert period_end(date(2026, 1, 31), 1) == date(2026, 2, 27)  # day clamped to the month, minus one
    ranges = month_ranges(date(2026, 4, 10), date(2026, 6, 20))
    assert ranges == [(date(2026, 4, 10), date(2026, 4, 30)), (date(2026, 5, 1), date(2026, 5, 31)),
                      (date(2026, 6, 1), date(2026, 6, 20))]


def test_holidays_wrap_the_new_year_and_multiply():
    cal = Calendar([{"name": "a", "from": "12-24", "to": "01-01", "factor": "0.2"},
                    {"name": "b", "from": "12-30", "to": "12-31", "factor": "0.5"}])
    assert cal.holiday_factor(date(2026, 12, 25)) == Decimal("0.2")
    assert cal.holiday_factor(date(2027, 1, 1)) == Decimal("0.2")
    assert cal.holiday_factor(date(2026, 12, 31)) == Decimal("0.10")
    assert cal.holiday_factor(date(2026, 6, 1)) == Decimal(1)


def test_scale_rounds_half_up_and_keeps_one():
    assert scaled(3, Decimal("0.5")) == 2
    assert scaled(1, Decimal("0.1")) == 1
    assert scaled(4, Decimal("2")) == 8


def test_cache_split_and_row_amount():
    assert split_cache(1000, Decimal("0.4"), Decimal("0.1"), Decimal("0")) == (500, 400, 100, 0)
    assert split_cache(10, Decimal("0.95"), Decimal("0.1"), Decimal("0")) == (0, 10, 0, 0)
    table = load_price_table()
    version = table.version("anthropic", "model-a-standard", date(2026, 5, 1))
    # 1,000,000 uncached input tokens at 2.50 per million
    assert amount(version, 1000000, 0, 0, 0, 0) == Decimal("2.50000000")


def test_selfhosted_model_costs_zero_by_default_and_null_on_request(tmp_path):
    zero = gen(tmp_path / "z", "tiny", 3, 1)
    rows = [r for _b, r in load_records(zero, "usage") if r.provider == "litellm" and r.model == "selfhosted-c-small"]
    assert rows and all(str(r.cost) == "0.00000000" or r.cost == 0 for r in rows)
    assert {(r.currency, r.cost_basis) for r in rows} == {("USD", "estimate")}

    null = gen(tmp_path / "n", "tiny", 3, 1, {"providers": {"litellm": {"unpriced_cost": "null"}}})
    rows = [r for _b, r in load_records(null, "usage") if r.provider == "litellm" and r.model == "selfhosted-c-small"]
    assert rows and all(r.cost is None and r.currency == "" and r.cost_basis is None for r in rows)
    assert meterline_sim.verify(null) == []


def test_copilot_is_enabled_by_default_and_writes_its_batches(tmp_path):
    # phase 1 refused a run with Copilot enabled; phase 2 renders it, and the default is enabled
    assert load_defaults()["providers"]["github_copilot"]["enabled"] is True
    folder = meterline_sim.generate("tiny", 1, months=2, out=str(tmp_path / "default"), params={})
    gt = read_json(folder, "ground_truth.json")
    assert {p["provider"] for p in gt["pulls"]} == {"anthropic", "github_copilot", "litellm"}
    assert {p["purpose"] for p in gt["pulls"] if p["provider"] == "github_copilot"} >= {"main", "seat_list"}
    assert meterline_sim.verify(folder) == []
    # disabled: no Copilot record, pull, identity or seat
    off = gen(tmp_path / "off", "tiny", 1, 2, COPILOT_OFF)
    gt_off = read_json(off, "ground_truth.json")
    assert {p["provider"] for p in gt_off["pulls"]} == {"anthropic", "litellm"}
    assert all(r["provider"] != "github_copilot" for r in read_csv(off, "org/identities.csv"))
    assert all(s["provider"] != "github_copilot" for p in gt_off["people"] for s in p["seats"])
    assert [c for c in gt_off["contracts"] if c["provider"] == "github_copilot"] == []
    assert gt_off["totals_before_correction"] == []


def test_ground_truth_carries_the_resolved_parameters(tmp_path):
    folder = gen(tmp_path, "tiny", 3, 1, {"events": {"runaway_agent": {"enabled": False}}})
    gt = read_json(folder, "ground_truth.json")
    assert gt["parameters"]["events"]["runaway_agent"]["enabled"] is False
    assert gt["parameters"]["providers"]["github_copilot"]["enabled"] is COPILOT_ENABLED
    assert all(e["type"] != "runaway_agent" for e in gt["events"])


def test_runaway_is_not_applied_without_anthropic_and_every_invariant_still_holds(tmp_path):
    folder = gen(tmp_path, "tiny", 7, 6, {"providers": {"anthropic": {"enabled": False}}})
    gt = read_json(folder, "ground_truth.json")
    runaway = [e for e in gt["events"] if e["type"] == "runaway_agent"]
    assert runaway and runaway[0]["details"]["applied"] is False
    assert meterline_sim.verify(folder) == []


def test_late_correction_is_applied_with_copilot_and_recorded_as_not_applied_without_it(tmp_path):
    on = gen(tmp_path / "on", "tiny", 7, 2)
    event = next(e for e in read_json(on, "ground_truth.json")["events"] if e["type"] == "late_correction")
    assert event["details"]["applied"] is True and event["dates"] and event["actors"]
    assert event["details"]["correction_batch_id"] in pull_batch_ids(on, "github_copilot", "correction")
    # 3.6: an event that needs a disabled provider is recorded with applied false and writes no batch
    off = gen(tmp_path / "off", "tiny", 7, 2, COPILOT_OFF)
    event = next(e for e in read_json(off, "ground_truth.json")["events"] if e["type"] == "late_correction")
    assert event["details"]["applied"] is False and event["dates"] == [] and event["actors"] == []
    assert event["details"]["correction_batch_id"] is None and event["details"]["rows"] is None
    assert pull_batch_ids(off, "github_copilot", "correction") == []


def test_late_correction_with_nothing_to_correct_is_recorded_as_not_applied(tmp_path):
    # a factor of 1 changes no credits: nothing to act on, so no correction batch is written (7.4)
    folder = gen(tmp_path, "tiny", 7, 2, {"events": {"late_correction": {"factor": "1.00"}}})
    event = next(e for e in read_json(folder, "ground_truth.json")["events"] if e["type"] == "late_correction")
    assert event["details"]["applied"] is False
    assert pull_batch_ids(folder, "github_copilot", "correction") == []
    assert meterline_sim.verify(folder) == []


def test_invalid_inputs_exit_2_not_a_traceback(tmp_path):
    from meterline_sim import cli

    def code(params, name):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(merged(BASE_PARAMS, params)), encoding="utf-8")
        return cli.main(["generate", "--preset", "tiny", "--seed", "1", "--months", "2",
                         "--out", str(tmp_path / name), "--params", str(path)])

    assert code({"events": {"reorganisation": {"month_index": "1"}}}, "a") == 2
    assert code({"events": {"reorganisation": {"month_index": 0}}}, "b") == 2
    assert code({"locations": {"weights": ["1", "-1", "1"]}}, "c") == 2
    from pathlib import Path

    tiny = Path(meterline_sim.__file__).parent / "data" / "presets" / "tiny.json"
    preset = json.loads(tiny.read_text(encoding="utf-8"))
    preset["profiles"] = {"counts": {"dormant": 0, "curious": 0, "office_worker": 0, "developer": 0, "power_user": 0}}
    path = tmp_path / "zero.json"
    path.write_text(json.dumps(preset), encoding="utf-8")
    params = tmp_path / "p.json"
    params.write_text(json.dumps(BASE_PARAMS), encoding="utf-8")
    assert cli.main(["generate", "--preset", str(path), "--seed", "1", "--months", "2",
                     "--out", str(tmp_path / "z"), "--params", str(params)]) == 2


def test_json_outputs_use_lf_line_ends(tmp_path):
    folder = gen(tmp_path, "tiny", 7, 1)
    for name in ("ground_truth.json", "manifest.json"):
        assert b"\r" not in (folder / name).read_bytes()


# ---- phase 2: Copilot billing arithmetic (8.3), pure functions --------------------------------------------


def _day(n):
    return date(2026, 4, n)


def test_organization_pool_walks_days_in_order_and_models_sorted():
    from meterline_sim import billing

    gross = {(_day(1), "model-b-light"): Decimal("30"), (_day(1), "model-a-standard"): Decimal("20"),
             (_day(2), "model-a-standard"): Decimal("100")}
    out = billing.org_pool(gross, Decimal("60"))
    # day 1, models sorted: model-a-standard takes 20 (40 left), model-b-light takes 30 (10 left); day 2 takes 10
    assert out == {
        (_day(1), "model-a-standard"): (Decimal("20"), Decimal("0")),
        (_day(1), "model-b-light"): (Decimal("30"), Decimal("0")),
        (_day(2), "model-a-standard"): (Decimal("10"), Decimal("90")),
    }
    assert sum(v[0] for v in out.values()) == min(Decimal("60"), sum(gross.values()))


def test_organization_pool_edges():
    from meterline_sim import billing

    gross = {(_day(1), "m"): Decimal("12.5"), (_day(3), "m"): Decimal("7.5")}
    assert billing.org_pool(gross, Decimal("0")) == {
        (_day(1), "m"): (Decimal("0"), Decimal("12.5")), (_day(3), "m"): (Decimal("0"), Decimal("7.5"))}
    big = billing.org_pool(gross, Decimal("1000"))
    assert all(net == 0 for _discount, net in big.values())
    assert billing.org_pool({}, Decimal("10")) == {}


def test_person_allowance_uses_models_in_sorted_order():
    from meterline_sim import billing

    out = billing.person_allowance({"model-b-premium": Decimal("50"), "model-a-standard": Decimal("80")},
                                   Decimal("100"))
    assert out == {"model-a-standard": (Decimal("80"), Decimal("0")), "model-b-premium": (Decimal("20"), Decimal("30"))}


def test_budget_crossed_on_is_the_first_day_the_cumulative_overage_exceeds_it():
    from meterline_sim import billing

    daily = {_day(1): Decimal("4"), _day(2): Decimal("0"), _day(3): Decimal("6"), _day(4): Decimal("5")}
    assert billing.budget_crossed_on(daily, Decimal("10")) == _day(4)  # 4 + 6 = 10 does not exceed; 15 does
    assert billing.budget_crossed_on(daily, Decimal("9.99")) == _day(3)
    assert billing.budget_crossed_on(daily, Decimal("15")) is None
    assert billing.budget_crossed_on(daily, None) is None


def test_the_budget_is_recorded_and_never_enforced(tmp_path):
    table = json.loads((SRC_DIR / "data" / "prices.json").read_text(encoding="utf-8"))
    table["github_copilot"]["budget_usd"] = "5.00"
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    capped = gen(tmp_path / "capped", "tiny", 7, 3, {"price_table": str(path)})
    free = gen(tmp_path / "free", "tiny", 7, 3)
    crossed = [c["budget_crossed_on"] for c in read_json(capped, "ground_truth.json")["contracts"]
               if c["provider"] == "github_copilot"]
    assert crossed and all(day is not None for day in crossed)  # the tiny allowance is exceeded every month
    # recorded only: usage is not stopped, so every silver record equals the run without a budget
    for kind in ("usage", "activity", "org_totals", "seats"):
        a = sorted((tmp_path / "capped" / "tiny-s7-m3" / "records" / kind).glob("*.jsonl"))
        b = sorted((tmp_path / "free" / "tiny-s7-m3" / "records" / kind).glob("*.jsonl"))
        assert [p.name for p in a] == [p.name for p in b]
        assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]
    assert meterline_sim.verify(capped) == []


# ---- phase 2: Copilot seats, records and validation ---------------------------------------------------------


def test_copilot_seats_are_granted_by_unit_and_listed_every_day(tmp_path):
    folder = gen(tmp_path, "tiny", 7, 2)
    gt = read_json(folder, "ground_truth.json")
    start = gt["period"]["start"]
    # beta (9 people) is granted on the start day
    at_start = [p for p in gt["people"] if p["start"] == start
                and any(s["provider"] == "github_copilot" for s in p["seats"])]
    assert len(at_start) == 9
    first = {r.actor_id: r for _b, r in load_records(folder, "seat_assignments") if r.period_start.isoformat() == start}
    assert len(first) == 9
    for r in first.values():
        age = (date.fromisoformat(start) - r.assigned_at.date()).days
        assert 30 <= age <= 365  # seat_age_days, drawn once per holder present at the start
        assert r.assigned_at.time().isoformat() == "00:00:00"
    assert {r.plan for r in first.values()} == {"business"}
    assert {r.granted_by for r in first.values()} == {"beta"}
    assert all(r.pending_cancellation_date is None and r.last_active_at is None for r in first.values())
    assert all(r.actor_email is None and r.actor_login and r.last_authenticated_at is None for r in first.values())
    # a seat list is written for every day of the period, one record per holder
    days = {r.period_start for _b, r in load_records(folder, "seat_assignments")}
    first_day, last_day = date.fromisoformat(start), date.fromisoformat(gt["period"]["end"])
    assert days == {first_day + timedelta(days=i) for i in range((last_day - first_day).days + 1)}
    # identities: method manual, no email, an id of 100000 + n
    ids = [r for r in read_csv(folder, "org/identities.csv") if r["provider"] == "github_copilot"]
    assert ids and all(r["method"] == "manual" and r["actor_email"] == "" and r["actor_kind"] == "user" for r in ids)
    assert all(r["actor_id"] == str(100000 + int(r["person_id"][1:])) for r in ids)


def test_copilot_joiners_get_a_seat_from_their_start_date_when_their_team_is_granted(tmp_path):
    folder = gen(tmp_path, "tiny", 7, 6, {"providers": {"github_copilot": {"granted_units": ["alpha", "beta"]}}})
    gt = read_json(folder, "ground_truth.json")
    joiner = next(e for e in gt["events"] if e["type"] == "joiner" and e["details"]["applied"])
    person = next(p for p in gt["people"] if p["person_id"] == joiner["actors"][0])
    assert "github_copilot" in joiner["details"]["seats"]
    seat = next(s for s in person["seats"] if s["provider"] == "github_copilot")
    assert seat["from"] == joiner["dates"][0]
    actor = copilot_actor_id(folder, person["person_id"])
    mine = sorted((r for _b, r in load_records(folder, "seat_assignments") if r.actor_id == actor),
                  key=lambda r: r.period_start)
    assert mine and all(r.assigned_at.isoformat() == joiner["dates"][0] + "T00:00:00+00:00" for r in mine)
    assert mine[0].period_start.isoformat() == joiner["dates"][0]


def test_copilot_record_shapes_follow_4_2(tiny_run):
    gt = read_json(tiny_run, "ground_truth.json")
    main = pull_batch_ids(tiny_run, "github_copilot", "main")[0]
    recs = [r for b, r in load_records(tiny_run) if b == main]
    by_type = {}
    for r in recs:
        by_type.setdefault(type(r).__name__, []).append(r)
    provider_account_id = "org:example-tiny"
    copilot_ids = {r["actor_id"] for r in read_csv(tiny_run, "org/identities.csv") if r["provider"] == "github_copilot"}

    credits = [r for r in by_type["UsageRecord"] if r.product == "copilot" and r.grain == "day"]
    assert credits
    for r in credits:
        assert r.provider_account_id == provider_account_id and r.provider == "github_copilot" and r.actor_kind == "user"
        assert r.actor_id in copilot_ids and r.actor_login and r.model == ""
        assert r.actor_email is None and r.actor_deleted is None and r.model_provider is None
        assert r.data_refreshed_at is None and r.extras == {} and r.requests is None
        assert r.tokens_input_total is None and r.tokens_output is None and r.tokens_input_uncached is None
        assert r.cost == r.cost_list == r.quantities["ai_credit"] * Decimal("0.01")
        assert (r.currency, r.cost_basis, r.cost_only) == ("USD", "consumption", False)

    tokens = [r for r in by_type["UsageRecord"] if r.product in ("copilot_cli", "copilot_app")]
    assert tokens
    for r in tokens:
        assert r.model == "" and r.quantities == {}
        assert (r.cost, r.cost_list, r.currency, r.cost_basis) == (None, None, "", None)
        assert r.tokens_input_total and r.tokens_output and r.requests and r.requests % 2 == 0
        assert r.tokens_input_uncached is None and r.tokens_cache_read is None and r.tokens_cache_write is None
        assert r.tokens_cache_write_5m is None and r.tokens_cache_write_1h is None and r.tokens_reasoning is None

    features = {"code_completion", "chat_panel_ask_mode", "chat_panel_agent_mode", "copilot_cli", "copilot_app"}
    feature_records = [r for r in by_type["ActivityRecord"] if r.product in features]
    assert {r.product for r in feature_records} >= {"code_completion", "chat_panel_ask_mode"}
    for r in feature_records:
        assert set(r.counters) == {"lines_suggested_to_add", "lines_suggested_to_delete"}
        assert all(v is not None for v in (r.messages, r.suggestions_shown, r.suggestions_accepted,
                                           r.lines_added, r.lines_removed))
        assert (r.commits, r.pull_requests, r.edits_accepted, r.edits_rejected) == (None, None, None, None)
        assert r.actor_email is None and r.actor_login and r.data_refreshed_at is None
        if r.product in ("copilot_cli", "copilot_app"):
            assert r.sessions == (r.messages + 2) // 3 and r.requests == r.messages * 2
        else:
            assert r.sessions is None and r.requests is None
        if r.product == "code_completion":
            assert r.counters["lines_suggested_to_add"] == r.suggestions_shown * 3
            assert r.suggestions_shown >= 1 and r.suggestions_accepted <= r.suggestions_shown

    plain = [r for r in by_type["ActivityRecord"] if r.product == "copilot"]
    assert len(plain) == len(credits)  # one per Copilot-active day, like the credit records
    for r in plain:
        assert set(r.counters) == {"custom_agents_used", "mcp_servers_used", "plugins_used", "skills_used",
                                   "slash_commands_used"}
        assert all(getattr(r, f) is None for f in ("sessions", "messages", "requests", "suggestions_shown",
                                                   "suggestions_accepted", "lines_added", "lines_removed",
                                                   "edits_accepted", "edits_rejected", "commits", "pull_requests"))

    months = [r for r in by_type["UsageRecord"] if r.grain == "month"]
    assert months
    for r in months:
        assert r.period_start.day == 1 and (r.period_end + timedelta(days=1)).day == 1
        assert set(r.extras) == {"discount_quantity", "net_quantity", "net_amount", "price_per_unit"}
        assert r.model != "" and r.cost_basis == "consumption" and r.tokens_input_total is None
        assert r.cost_list == r.quantities["ai_credit"] * Decimal("0.01") and r.cost == r.extras["net_amount"]

    org = by_type["OrgTotal"]
    assert org and {r.cost_basis for r in org} == {"billed"}
    assert all(r.model and r.product == "copilot" and r.requests is None and r.tokens_input_total is None
               for r in org)

    snaps = by_type["SeatSnapshot"]
    days = (date.fromisoformat(gt["period"]["end"]) - date.fromisoformat(gt["period"]["start"])).days + 1
    assert len(snaps) == days
    for r in snaps:
        assert r.pending_invites == 0 and r.pending_cancellations is not None
        assert r.daily_active <= r.weekly_active <= r.monthly_active <= r.seats


def test_unknown_plan_and_missing_credit_prices_exit_2(tmp_path):
    from meterline_sim import cli

    def code(params, name):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(params), encoding="utf-8")
        return cli.main(["generate", "--preset", "tiny", "--seed", "1", "--months", "2",
                         "--out", str(tmp_path / name), "--params", str(path)])

    assert code({}, "valid") == 0  # the same run with a known plan and complete prices is accepted
    assert code({"providers": {"github_copilot": {"plan": "enterprise"}}}, "plan") == 2
    # the same unknown plan is harmless while Copilot is disabled
    assert code({"providers": {"github_copilot": {"enabled": False, "plan": "enterprise"}}}, "plan_off") == 0
    table = json.loads((SRC_DIR / "data" / "prices.json").read_text(encoding="utf-8"))
    del table["github_copilot"]["credits_per_interaction"]["model-b-light"]
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    assert code({"price_table": str(path)}, "cpi") == 2
    assert code({"price_table": str(path), "providers": {"github_copilot": {"enabled": False}}}, "cpi_off") == 0


def test_negative_included_credits_and_negative_copilot_weights_exit_2(tmp_path):
    from meterline_sim import cli

    def code(params, name):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(params), encoding="utf-8")
        return cli.main(["generate", "--preset", "tiny", "--seed", "1", "--months", "1",
                         "--out", str(tmp_path / name), "--params", str(path)])

    assert code({"providers": {"github_copilot": {"included_credits_per_seat": "0"}}}, "zero") == 0
    assert code({"providers": {"github_copilot": {"included_credits_per_seat": "-100"}}}, "allowance") == 2
    weights = {"model-b-light": "-1", "model-a-standard": "2", "model-b-premium": "0"}
    assert code({"behaviour": {"copilot": {"developer": {"model_weights": weights}}}}, "weights") == 2
    assert code({"behaviour": {"copilot": {"developer": {"model_weights": dict(weights, **{"model-b-light": "0"})}}}},
                "weights_ok") == 0
