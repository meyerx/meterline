"""S09 Copilot credits agree with billing (spec section 15, invariant V6; billing of 8.3)."""

import importlib
from collections import defaultdict
from decimal import Decimal

import meterline

import meterline_sim  # noqa: F401
from sim_helpers import (
    COPILOT_OFF, copy_run, credits_of, gen, latest_by_key, mutate_json, mutate_jsonl, pull_batch_ids, read_json,
    revised_keys,
)

vmod = importlib.import_module("meterline_sim.verify")

CREDIT = Decimal("0.01")


def _parts(folder):
    usage = latest_by_key(folder, "usage").values()
    totals = latest_by_key(folder, "org_totals").values()
    days = [r for r in usage if r["provider"] == "github_copilot" and r["product"] == "copilot" and r["grain"] == "day"]
    months = [r for r in usage if r["provider"] == "github_copilot" and r["grain"] == "month"]
    org = [r for r in totals if r["provider"] == "github_copilot"]
    return days, months, org


def _s09_credit_relations(days, months, org, failures):
    # 1. per day: the organization's gross over models = the sum of the persons' daily credits
    org_day, person_day = defaultdict(Decimal), defaultdict(Decimal)
    for r in org:
        org_day[r["period_start"]] += credits_of(r)
    for r in days:
        person_day[r["period_start"]] += credits_of(r)
    for day in sorted(set(org_day) | set(person_day)):
        if org_day[day] != person_day[day]:
            failures.append("1: %s: organization %s, persons %s" % (day, org_day[day], person_day[day]))

    # 2. per month and model: the organization's gross = the month records' gross
    org_month, rec_month = defaultdict(Decimal), defaultdict(Decimal)
    for r in org:
        org_month[(r["period_start"][:7], r["model"])] += credits_of(r)
    for r in months:
        rec_month[(r["period_start"][:7], r["model"])] += credits_of(r)
    for key in sorted(set(org_month) | set(rec_month)):
        if org_month[key] != rec_month[key]:
            failures.append("2: %s: organization %s, month records %s" % (key, org_month[key], rec_month[key]))

    # 3. per person and month: the month records' gross = their daily sum
    daily, monthly = defaultdict(Decimal), defaultdict(Decimal)
    for r in days:
        daily[(r["actor_id"], r["period_start"][:7])] += credits_of(r)
    for r in months:
        monthly[(r["actor_id"], r["period_start"][:7])] += credits_of(r)
    for key in sorted(set(daily) | set(monthly)):
        if daily[key] != monthly[key]:
            failures.append("3: %s: daily %s, month records %s" % (key, daily[key], monthly[key]))


def _s09_billing_relation(gt, org, failures):
    # 4. per month: discount = min(pool, gross), net amounts = overage_amount (contracts of the ground truth)
    contracts = {c["month"]: c for c in gt["contracts"] if c["provider"] == "github_copilot"}
    discount, net_amount, gross = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    for r in org:
        month = r["period_start"][:7]
        discount[month] += Decimal(r["extras"]["discount_quantity"])
        net_amount[month] += Decimal(r["extras"]["net_amount"])
        gross[month] += credits_of(r)
    for month in sorted(set(contracts) | set(gross)):
        c = contracts.get(month)
        if c is None:
            failures.append("4: %s has billing records and no contract row" % month)
            continue
        if Decimal(c["gross_credits"]) != gross[month]:
            failures.append("4: %s: contract gross %s, billing gross %s" % (month, c["gross_credits"], gross[month]))
        if discount[month] != min(Decimal(c["pool"]), Decimal(c["gross_credits"])):
            failures.append("4: %s: discount %s, min(pool, gross) %s" % (
                month, discount[month], min(Decimal(c["pool"]), Decimal(c["gross_credits"]))))
        if net_amount[month] != Decimal(c["overage_amount"]):
            failures.append("4: %s: net amounts %s, overage %s" % (month, net_amount[month], c["overage_amount"]))


def s09_failures(folder):
    """The five relations of S09 (numbered 1-5) and the invariants 8.3 states for the extras (6)."""
    days, months, org = _parts(folder)
    gt = read_json(folder, "ground_truth.json")
    failures = []

    _s09_credit_relations(days, months, org, failures)
    _s09_billing_relation(gt, org, failures)

    # 5. every Copilot cost_list is gross x 0.01
    for r in days + months + org:
        if r["cost_list"] is None or Decimal(r["cost_list"]) != credits_of(r) * CREDIT:
            failures.append("5: %s %s %s: cost_list %s, credits %s" % (
                r["period_start"], r["actor_id"] if "actor_id" in r else r["model"], r["grain"], r["cost_list"],
                credits_of(r)))

    # 6. billing records carry the values of 8.3: net = gross - discount, amount = net x 0.01, price 0.01
    for r in org:
        ex = r["extras"]
        if (Decimal(ex["net_quantity"]) != credits_of(r) - Decimal(ex["discount_quantity"])
                or Decimal(ex["net_amount"]) != Decimal(ex["net_quantity"]) * CREDIT
                or Decimal(r["cost"]) != Decimal(ex["net_amount"]) or Decimal(ex["price_per_unit"]) != CREDIT):
            failures.append("6: %s %s: extras do not follow 8.3" % (r["period_start"], r["model"]))
    return failures


def has_overage(folder):
    """The existence clause of S09 (tiny run only, not part of V6)."""
    return any(Decimal(c["overage_amount"]) > 0 for c in read_json(folder, "ground_truth.json")["contracts"]
               if c["provider"] == "github_copilot")


def _starts(failures, number):
    return any(f.startswith("%d:" % number) for f in failures)


def test_done_check_S09_credits_agree_with_billing(tiny_run, vertical_run):
    assert s09_failures(tiny_run) == []
    assert vmod.v6_copilot(tiny_run) == []
    assert s09_failures(vertical_run) == []
    assert vmod.v6_copilot(vertical_run) == []


def test_done_check_S09_existence_clause_the_tiny_run_has_a_month_with_overage(tiny_run):
    assert has_overage(tiny_run)
    days, months, org = _parts(tiny_run)
    assert days and months and org


def test_done_check_S09_billing_follows_8_3_on_the_tiny_run(tiny_run):
    gt = read_json(tiny_run, "ground_truth.json")
    included = Decimal(gt["parameters"]["providers"]["github_copilot"]["included_credits_per_seat"])
    assert included == Decimal("100")  # the tiny preset's low allowance, so that overage happens
    for c in (c for c in gt["contracts"] if c["provider"] == "github_copilot"):
        assert Decimal(c["pool"]) == c["seats_billed"] * included
        assert Decimal(c["seat_fees"]) == c["seats_billed"] * Decimal("23.00")
        assert Decimal(c["discount_credits"]) == min(Decimal(c["pool"]), Decimal(c["gross_credits"]))
        assert c["budget_crossed_on"] is None  # budget_usd is null in the bundled table
    days, months, org = _parts(tiny_run)
    # person discounts are a separate invented rule: each person's allowance, used in model order (8.3)
    by_person = defaultdict(list)
    for r in months:
        by_person[(r["actor_id"], r["period_start"][:7])].append(r)
    for key, recs in sorted(by_person.items()):
        left = included
        for r in sorted(recs, key=lambda x: x["model"]):
            gross = credits_of(r)
            used = min(gross, left)
            left -= used
            assert Decimal(r["extras"]["discount_quantity"]) == used
            assert Decimal(r["extras"]["net_quantity"]) == gross - used
            assert Decimal(r["cost"]) == (gross - used) * CREDIT
            assert r["cost_basis"] == "consumption"
    assert {r["cost_basis"] for r in org} == {"billed"}


# ---- negative controls: each mutation must make S09 fail for the stated reason --------------------------


def _copilot_file(folder, kind):
    return folder / "records" / kind / (pull_batch_ids(folder, "github_copilot", "main")[0] + ".jsonl")


def _lower_first(folder, kind, lines, match):
    revised = revised_keys(folder, kind)
    fields = meterline.KINDS[kind].KEY_FIELDS
    for line in lines:
        if (match(line) and tuple(line[f] for f in fields) not in revised
                and Decimal(line["quantities"]["ai_credit"]) > 0):
            line["quantities"]["ai_credit"] = str(Decimal(line["quantities"]["ai_credit"]) - 1)
            return lines
    raise AssertionError("nothing to lower")


def test_done_check_S09_negative_control_1_day_sums_differ(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    mutate_jsonl(_copilot_file(broken, "org_totals"),
                 lambda lines: _lower_first(broken, "org_totals", lines, lambda x: True))
    assert _starts(s09_failures(broken), 1)
    assert any(f.startswith("copilot") for f in vmod.v6_copilot(broken))


def test_done_check_S09_negative_control_2_and_3_month_records_differ(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    mutate_jsonl(_copilot_file(broken, "usage"),
                 lambda lines: _lower_first(broken, "usage", lines, lambda x: x["grain"] == "month"))
    failures = s09_failures(broken)
    assert _starts(failures, 2) and _starts(failures, 3)
    assert any(f.startswith("copilot") for f in vmod.v6_copilot(broken))


def test_done_check_S09_negative_control_4_pool_and_overage_differ(tiny_run, tmp_path):
    pool = copy_run(tiny_run, tmp_path / "pool")

    def more_pool(gt):
        row = next(c for c in gt["contracts"] if c["provider"] == "github_copilot"
                   and Decimal(c["gross_credits"]) > Decimal(c["pool"]) > 0)
        row["pool"] = str(Decimal(row["pool"]) - 1)

    mutate_json(pool / "ground_truth.json", more_pool)
    assert _starts(s09_failures(pool), 4)
    assert vmod.v6_copilot(pool) != []

    overage = copy_run(tiny_run, tmp_path / "overage")

    def cheaper(gt):
        row = next(c for c in gt["contracts"] if c["provider"] == "github_copilot" and Decimal(c["overage_amount"]) > 0)
        row["overage_amount"] = str(Decimal(row["overage_amount"]) - Decimal("0.01"))

    mutate_json(overage / "ground_truth.json", cheaper)
    assert _starts(s09_failures(overage), 4)
    assert vmod.v6_copilot(overage) != []


def test_done_check_S09_negative_control_5_cost_list_is_not_gross_times_a_cent(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)

    revised = revised_keys(broken, "usage")
    fields = meterline.KINDS["usage"].KEY_FIELDS

    def skew(lines):
        for line in lines:
            if (line["product"] == "copilot" and line["grain"] == "day" and line["cost_list"] is not None
                    and tuple(line[f] for f in fields) not in revised):
                line["cost_list"] = str(Decimal(line["cost_list"]) + Decimal("0.01"))
                return lines
        raise AssertionError("nothing to skew")

    mutate_jsonl(_copilot_file(broken, "usage"), skew)
    assert _starts(s09_failures(broken), 5)
    assert vmod.v6_copilot(broken) != []


def test_done_check_S09_negative_control_existence_clause_can_fail(tmp_path):
    # a pool far above the usage: no month has overage, and the clause reports it
    roomy = gen(tmp_path / "roomy", "tiny", 7, 6,
                {"providers": {"github_copilot": {"included_credits_per_seat": "1000000"}}})
    assert s09_failures(roomy) == []  # the relations still hold
    assert has_overage(roomy) is False
    # and with Copilot off there is nothing to compare, and no overage either
    off = gen(tmp_path / "off", "tiny", 7, 6, COPILOT_OFF)
    assert vmod.v6_copilot(off) == [] and has_overage(off) is False


def test_done_check_S09_negative_control_missing_copilot_silver_is_reported(tiny_run, tmp_path):
    # the contracts of the ground truth still claim credits, and no usage or billing record shows them
    broken = copy_run(tiny_run, tmp_path)
    batches = pull_batch_ids(broken, "github_copilot", "main") + pull_batch_ids(broken, "github_copilot", "correction")
    for kind in ("usage", "org_totals"):
        for batch in batches:
            path = broken / "records" / kind / (batch + ".jsonl")
            if path.is_file():
                path.unlink()
    assert _starts(s09_failures(broken), 4)
    assert any(f.startswith("copilot billing") for f in vmod.v6_copilot(broken))
