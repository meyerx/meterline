"""G09 and G10 (a, b, d, e, f): gold reconciles to silver and to the simulator's ground truth."""

import calendar
import csv
import json
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest
import yaml

from gold_helpers import (
    KEY, MEASURES, person_day_groups, person_key, read_csv, read_dicts, silver_usage_groups,
)

PRESETS = ["tiny", "vertical"]
CENT = Decimal("0.01")


@pytest.mark.parametrize("preset", PRESETS)
def test_done_check_G09_silver_reconciles(sim_runs, sim_builds, preset, tmp_path):
    gold, _ = sim_builds[preset]
    silver = silver_usage_groups(sim_runs[preset].silver)
    gold_groups = person_day_groups(gold / "central" / "person_day.csv")
    assert silver, "no silver usage groups"
    assert set(silver) == set(gold_groups)
    for key, expected in silver.items():
        assert gold_groups[key] == expected, key
    # negative control (see `test_done_check_stronger_controls.py`): the gold-side comparison above, run on a copy of gold's person_day.csv in which
    # one row's requests are one higher, fails on that group
    header, rows = read_csv(gold / "central" / "person_day.csv")
    column = header.index("requests")
    row = next(r for r in rows if r[column] != "")
    row[column] = str(int(row[column]) + 1)
    broken_path = tmp_path / "person_day.csv"
    with open(broken_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    broken_groups = person_day_groups(broken_path)
    assert set(silver) == set(broken_groups)
    differing = [key for key, expected in silver.items() if broken_groups[key] != expected]
    assert len(differing) == 1


def _rounded(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_EVEN)


def _totals_problems(gold_groups, rows):
    """(provider, basis, month, what) for every ground-truth usage total that gold's groups do not equal."""
    problems = []
    for t in rows:
        group = (t["provider"], t["cost_basis"] or "", t["month"])
        g = gold_groups[group]
        if _rounded(g["cost"]) != _rounded(t["cost"] or 0):
            problems.append(group + ("cost",))
        # the ground-truth totals carry no 5m/1h cache-write split (sim spec 11.3)
        for field in MEASURES:
            if field in ("tokens_cache_write_5m", "tokens_cache_write_1h"):
                continue
            if g[field] != Decimal(t[field] or 0):
                problems.append(group + (field,))
        if g["ai_credit"] != Decimal((t["quantities"] or {}).get("ai_credit", 0)):
            problems.append(group + ("ai_credit",))
    return problems


@pytest.mark.parametrize("preset", PRESETS)
def test_done_check_G10a_ground_truth_totals(sim_runs, sim_builds, preset):
    gold, _ = sim_builds[preset]
    truth = sim_runs[preset].truth
    gold_groups = person_day_groups(gold / "central" / "person_day.csv")
    rows = [t for t in truth["totals"] if t["kind"] == "usage" and t["grain"] == "day"]
    assert rows
    assert {(t["provider"], t["cost_basis"] or "", t["month"]) for t in rows} == set(gold_groups)
    assert _totals_problems(gold_groups, rows) == []
    # negative control (see `test_done_check_stronger_controls.py`): the same comparison on gold's groups with one cost a cent higher and one
    # request count one higher names exactly those two, on the field that changed
    first, second = sorted(gold_groups)[0], sorted(gold_groups)[-1]
    broken = {key: dict(value) for key, value in gold_groups.items()}
    broken[first]["cost"] = broken[first]["cost"] + Decimal("0.01")
    broken[second]["requests"] = broken[second]["requests"] + 1
    assert sorted(_totals_problems(broken, rows)) == sorted([first + ("cost",), second + ("requests",)])


def _per_million(entry):
    return {k: (None if v is None else Decimal(v)) for k, v in entry.items()}


@pytest.mark.parametrize("preset", PRESETS)
def test_done_check_G10b_cost_ref_against_cost_source_around_the_price_change(
    sim_runs, sim_builds, example_files, preset
):
    meterline_sim = pytest.importorskip("meterline_sim")

    gold, _ = sim_builds[preset]
    truth = sim_runs[preset].truth
    # precondition: the example prices equal the simulator's prices before the change
    sim_prices = json.loads(
        (Path(meterline_sim.__file__).parent / "data" / "prices.json").read_text(encoding="utf-8")
    )
    example = yaml.safe_load(example_files[1].read_text(encoding="utf-8"))
    example_by = {(t["provider"], t["model"]): t["per_million"] for t in example["token_prices"]}
    for entry in sim_prices["token_prices"]:
        if entry["per_million"] is None:
            continue
        got = example_by.get((entry["provider"], entry["model"])) or example_by.get(("*", entry["model"]))
        assert got is not None, entry
        assert _per_million(got) == _per_million(entry["per_million"]), entry
    changes = [e for e in truth["events"] if e["type"] == "price_change" and e["details"]["applied"]]
    assert changes, "the run has no applied price change"
    change = changes[0]
    day = change["dates"][0]
    model = change["details"]["model"]
    assert _per_million(change["details"]["prices_before"]) == _per_million(
        example_by[(change["details"]["provider"], model)]
    )

    rows = [r for r in read_dicts(gold / "central" / "person_day.csv") if r["provider"] == "anthropic"]
    before = [r for r in rows if r["day"] < day and r["cost_source"] != ""]
    assert before
    for r in before:
        assert r["cost_ref"] != "", r
        tol = Decimal("0.00000002") * int(r["source_records"])
        assert abs(Decimal(r["cost_ref"]) - Decimal(r["cost_source"])) <= tol, r
    after = [
        r for r in rows
        if r["day"] >= day and r["model"] == model and r["cost_ref"] != "" and Decimal(r["cost_ref"]) > 0
    ]
    assert after
    for r in after:
        tol = Decimal("0.00000002") * int(r["source_records"])
        assert abs(Decimal(r["cost_ref"]) - Decimal(r["cost_source"])) > tol, r


def test_done_check_G10d_corrected_month_has_revised_final_rows(sim_runs, sim_builds):
    gold, _ = sim_builds["tiny"]
    truth = sim_runs["tiny"].truth
    events = [e for e in truth["events"] if e["type"] == "late_correction" and e["details"]["applied"]]
    assert events
    month = events[0]["dates"][0][:7]
    provider = events[0]["details"]["provider"]
    assert provider == "github_copilot"
    rows = [
        r for r in read_dicts(gold / "central" / "data_quality.csv")
        if r["metric"] == "final_rows_revised" and r["provider"] == provider and r["month"] == month
    ]
    assert rows and sum(int(r["value"]) for r in rows) > 0
    # negative control: a month with no correction shows none
    clean = [
        r for r in read_dicts(gold / "central" / "data_quality.csv")
        if r["metric"] == "final_rows_revised" and r["provider"] == "anthropic"
    ]
    assert all(int(r["value"]) == 0 for r in clean)


def _month_end(month, data_through):
    year, mon = int(month[:4]), int(month[5:7])
    last = date(year, mon, calendar.monthrange(year, mon)[1])
    return min(last, date.fromisoformat(data_through))


@pytest.mark.parametrize("preset", PRESETS)
def test_done_check_G10e_joiner_first_month_employed_days(sim_runs, sim_builds, preset):
    gold, _ = sim_builds[preset]
    truth = sim_runs[preset].truth
    manifest = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))
    leavers = {e["actors"][0] for e in truth["events"] if e["type"] == "leaver" and e["details"]["applied"]}
    months = {}
    for r in read_dicts(gold / "central" / "person_month.csv"):
        months.setdefault(r["person_key"], []).append(r)
    joiners = [e for e in truth["events"] if e["type"] == "joiner" and e["details"]["applied"]]
    assert joiners, "the run has no joiner"
    checked = 0
    for e in joiners:
        pid = e["actors"][0]
        if pid in leavers:
            continue
        start = date.fromisoformat(e["dates"][0])
        rows = sorted(months[person_key(pid)], key=lambda r: r["month"])
        first = rows[0]
        assert first["month"] == start.isoformat()[:7]
        end = _month_end(first["month"], manifest["data_through"])
        assert int(first["employed_days"]) == (end - start).days + 1, (pid, first)
        checked += 1
    assert checked


@pytest.mark.parametrize("preset", PRESETS)
def test_done_check_G10f_no_person_month_after_a_leaver_left(sim_runs, sim_builds, preset):
    gold, _ = sim_builds[preset]
    truth = sim_runs[preset].truth
    months = {}
    for r in read_dicts(gold / "central" / "person_month.csv"):
        months.setdefault(r["person_key"], []).append(r["month"])
    leavers = [e for e in truth["events"] if e["type"] == "leaver" and e["details"]["applied"]]
    assert leavers, "the run has no leaver"
    for e in leavers:
        pid = e["actors"][0]
        last_month = e["dates"][0][:7]
        got = months.get(person_key(pid), [])
        assert last_month in got
        assert all(m <= last_month for m in got), (pid, got)
