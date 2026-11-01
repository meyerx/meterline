"""S10 organisation totals >= the sum of people, S11 organisation files are consistent."""

import importlib
from collections import defaultdict
from decimal import ROUND_FLOOR, Decimal

import meterline_sim  # noqa: F401
from sim_helpers import (
    COPILOT_ENABLED, copy_run, d, jsonl_files, latest_by_key, load_records, mutate_csv, mutate_jsonl, pull_batch_ids,
    read_csv, read_json, revised_keys,
)

vmod = importlib.import_module("meterline_sim.verify")

PROFILES = ("dormant", "curious", "office_worker", "developer", "power_user")


def _s10_anthropic(usage, totals, failures):
    sums = defaultdict(lambda: defaultdict(Decimal))
    for rec in usage.values():
        if rec["provider"] != "anthropic":
            continue
        for f in ("cost", "cost_list", "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write",
                  "tokens_input_total", "tokens_output", "requests"):
            if rec[f] is not None:
                sums[rec["period_start"]][f] += Decimal(rec[f])
    for rec in totals.values():
        if rec["provider"] != "anthropic":
            continue
        for f, value in sums[rec["period_start"]].items():
            if rec[f] is None or Decimal(rec[f]) < value:
                failures.append("anthropic %s %s" % (rec["period_start"], f))
            elif Decimal(rec[f]) != value:  # "They are equal in v1"
                failures.append("anthropic %s %s is not equal in v1" % (rec["period_start"], f))
    litellm_usage = {(r["period_start"], r["model"]): r for r in usage.values() if r["provider"] == "litellm"}
    for rec in totals.values():
        if rec["provider"] == "litellm":
            u = litellm_usage[(rec["period_start"], rec["model"])]
            for f in ("cost", "tokens_input_total", "tokens_output", "requests"):
                if rec[f] != u[f]:
                    failures.append("litellm %s %s" % (rec["period_start"], f))


def _s10_copilot(usage, totals, failures):
    # Copilot: per day the organization's gross is at least the sum of the persons' daily credits
    org_gross, persons = defaultdict(Decimal), defaultdict(Decimal)
    for rec in totals.values():
        if rec["provider"] == "github_copilot":
            org_gross[rec["period_start"]] += Decimal(rec["quantities"].get("ai_credit", "0"))
    for rec in usage.values():
        if rec["provider"] == "github_copilot" and rec["product"] == "copilot" and rec["grain"] == "day":
            persons[rec["period_start"]] += Decimal(rec["quantities"].get("ai_credit", "0"))
    for day, value in sorted(persons.items()):
        if org_gross[day] < value:
            failures.append("copilot %s: organization credits %s below the sum of people %s" % (day, org_gross[day], value))


def s10_direct(folder):
    failures = []
    usage = latest_by_key(folder, "usage")
    totals = latest_by_key(folder, "org_totals")
    _s10_anthropic(usage, totals, failures)
    for rec in latest_by_key(folder, "seats").values():
        if rec["daily_active"] > rec["seats"]:
            failures.append("daily_active above seats " + rec["period_start"])
    _s10_copilot(usage, totals, failures)
    # Copilot seats: a snapshot counts the assignments of its day's seat list
    listed = defaultdict(int)
    for rec in latest_by_key(folder, "seat_assignments").values():
        if rec["provider"] == "github_copilot":
            listed[rec["period_start"]] += 1
    for rec in latest_by_key(folder, "seats").values():
        if rec["provider"] == "github_copilot" and rec["seats"] != listed.get(rec["period_start"], 0):
            failures.append("copilot %s: snapshot seats %s, seat list %s" % (
                rec["period_start"], rec["seats"], listed.get(rec["period_start"], 0)))
    return failures


def test_done_check_S10_org_totals_cover_people(tiny_run, vertical_run):
    for folder in (tiny_run, vertical_run):
        assert s10_direct(folder) == []
        assert vmod.v7_org_totals(folder) == []


def test_done_check_S10_negative_control_low_total_fails(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    _kind, path = next((k, p) for k, p in jsonl_files(broken) if k == "org_totals" and "anthropic" in p.read_text())

    def lower(lines):
        for line in lines:
            if line["provider"] == "anthropic" and int(line["tokens_output"]) > 0:
                line["tokens_output"] = int(line["tokens_output"]) - 1
                return lines
        raise AssertionError("nothing to lower")

    mutate_jsonl(path, lower)
    assert s10_direct(broken) != []
    assert vmod.v7_org_totals(broken) != []


def test_done_check_S10_negative_control_low_copilot_total_and_seat_count_fail(tiny_run, tmp_path):
    low = copy_run(tiny_run, tmp_path / "low")
    main = pull_batch_ids(low, "github_copilot", "main")[0]
    revised = revised_keys(low, "org_totals")
    fields = ("provider", "provider_account_id", "scope", "grain", "period_start", "product", "model")

    def lower(lines):
        for line in lines:
            if (tuple(line[f] for f in fields) not in revised
                    and Decimal(line["quantities"]["ai_credit"]) > 0):
                line["quantities"]["ai_credit"] = str(Decimal(line["quantities"]["ai_credit"]) - 1)
                return lines
        raise AssertionError("nothing to lower")

    mutate_jsonl(low / "records" / "org_totals" / (main + ".jsonl"), lower)
    assert any(f.startswith("copilot") and "below the sum of people" in f for f in s10_direct(low))
    assert any("github_copilot" in f or "copilot" in f for f in vmod.v7_org_totals(low))

    seats = copy_run(tiny_run, tmp_path / "seats")

    def more(lines):
        lines[0]["seats"] = int(lines[0]["seats"]) + 3
        return lines

    mutate_jsonl(seats / "records" / "seats" / (main + ".jsonl"), more)
    assert any(f.startswith("copilot") and "seat list" in f for f in s10_direct(seats))


def targets(shares, n):
    q = {p: Decimal(shares[p]) * n for p in PROFILES}
    out = {p: int(q[p].to_integral_value(ROUND_FLOOR)) for p in PROFILES}
    left = n - sum(out.values())
    order = sorted(PROFILES, key=lambda p: (-(q[p] - out[p]), PROFILES.index(p)))
    for p in order[:left]:
        out[p] += 1
    return out


def _headcount_on(folder, day):
    return [r for r in read_csv(folder, "org/people.csv")
            if r["valid_from"] <= day and (r["valid_to"] == "" or r["valid_to"] >= day)]


def _start_profiles(folder):
    gt = read_json(folder, "ground_truth.json")
    start = gt["period"]["start"]
    counts = {p: 0 for p in PROFILES}
    joiners = {e["actors"][0] for e in gt["events"] if e["type"] == "joiner"}
    for person in gt["people"]:
        if person["start"] == start and person["person_id"] not in joiners:
            first = next(x for x in person["profiles"] if x["from"] <= start)
            counts[first["profile"]] += 1
    return counts


def test_done_check_S11_org_files_are_consistent(tiny_run, vertical_run):
    for folder in (tiny_run, vertical_run):
        assert vmod.v8_org_files(folder) == []


def test_done_check_S11_negative_control_cycle_and_orphan_fail(tiny_run, tmp_path):
    cyc = copy_run(tiny_run, tmp_path / "cycle")

    def loop(rows):
        victim = next(r for r in rows if r["manager_id"])
        victim["manager_id"] = victim["person_id"]
        return rows

    mutate_csv(cyc / "org" / "people.csv", loop)
    assert vmod.v8_org_files(cyc) != []

    orphan = copy_run(tiny_run, tmp_path / "orphan")

    def lost(rows):
        rows[-1]["unit_id"] = "u9999"
        return rows

    mutate_csv(orphan / "org" / "people.csv", lost)
    assert vmod.v8_org_files(orphan) != []


def test_done_check_S11_target_arithmetic_for_396_people():
    shares = {"dormant": "0.30", "curious": "0.30", "office_worker": "0.30", "developer": "0.08", "power_user": "0.02"}
    assert targets(shares, 396) == {
        "dormant": 119, "curious": 119, "office_worker": 119, "developer": 31, "power_user": 8,
    }


def test_done_check_S11_preset_specific_counts(tiny_run, vertical_run):
    tiny_counts = _start_profiles(tiny_run)
    assert all(tiny_counts[p] >= 1 for p in PROFILES)
    assert tiny_counts == {"dormant": 4, "curious": 4, "office_worker": 5, "developer": 5, "power_user": 2}

    start = read_json(vertical_run, "ground_truth.json")["period"]["start"]
    joiners = {e["actors"][0] for e in read_json(vertical_run, "ground_truth.json")["events"] if e["type"] == "joiner"}
    n = len([r for r in _headcount_on(vertical_run, start) if r["person_id"] not in joiners])
    assert 360 <= n <= 440
    shares = {"dormant": "0.30", "curious": "0.30", "office_worker": "0.30", "developer": "0.08", "power_user": "0.02"}
    assert _start_profiles(vertical_run) == targets(shares, n)

    units = [r for r in read_csv(vertical_run, "org/units.csv")
             if r["dimension"] == "line" and r["valid_from"] <= start and (r["valid_to"] == "" or r["valid_to"] >= start)]
    by_kind = defaultdict(int)
    for row in units:
        by_kind[row["kind"]] += 1
    assert (by_kind["division"], by_kind["department"], by_kind["team"]) == (6, 18, 45)
    if COPILOT_ENABLED:
        seats = [s for s in latest_by_key(vertical_run, "seats").values()
                 if s["provider"] == "github_copilot" and s["period_start"] == start]
        assert seats[0]["seats"] == 41


def test_done_check_S11_negative_control_the_tiny_run_does_not_have_41_copilot_seats(tiny_run):
    start = read_json(tiny_run, "ground_truth.json")["period"]["start"]
    seats = [s for s in latest_by_key(tiny_run, "seats").values()
             if s["provider"] == "github_copilot" and s["period_start"] == start]
    assert seats and seats[0]["seats"] != 41  # the vertical-run assertion is sensitive to the seat count
