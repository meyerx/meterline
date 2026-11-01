"""Phase 2 done-checks G06 (fixture privacy and audiences) and G15 (audience isolation); from phase 3 the layout
list of G06 is the whole of ``expected/layout.txt`` (spec 14: "from phase 3, the whole list").

Spec section 14. Synthetic data only: the fixture of docs/gold-fixture/ and the tiny simulator preset.
"""

import csv
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import yaml

from gold_helpers import EXPECTED, compare_with_control, fixture_inputs, read_dicts


def marked_view(row):
    """The exec and manager form of a central groups_month row (spec 7.6.2, change-list finding 39): no
    ``suppressed`` or ``suppression_reason``, and ``hidden`` last."""
    if row is None:
        return None
    out = {k: v for k, v in row.items() if k not in ("suppressed", "suppression_reason")}
    out["hidden"] = row["suppressed"]
    return out


def _manager_folders(root):
    return sorted(p.name for p in (Path(root) / "manager").iterdir() if p.is_dir())


# ---- G06 -----------------------------------------------------------------------------------------


def test_done_check_G06_fixture_groups_are_byte_identical(fixture_out):
    expected_managers = sorted(p.name for p in (EXPECTED / "manager").iterdir() if p.is_dir())
    assert _manager_folders(fixture_out) == expected_managers
    compare_with_control(
        fixture_out / "central" / "groups_month.csv", EXPECTED / "central" / "groups_month.csv", "central groups"
    )
    compare_with_control(
        fixture_out / "exec" / "groups_month.csv", EXPECTED / "exec" / "groups_month.csv", "exec groups"
    )
    compare_with_control(
        fixture_out / "central" / "comparison_groups.csv", EXPECTED / "central" / "comparison_groups.csv",
        "comparison groups",
    )
    for key in expected_managers:
        compare_with_control(
            fixture_out / "manager" / key / "groups_month.csv", EXPECTED / "manager" / key / "groups_month.csv",
            "manager groups " + key,
        )


def _band_problems(out, expected_rows):
    problems = []
    for row in expected_rows:
        path = Path(out) / "person" / row["person_key"] / "comparison.csv"
        if not path.is_file():
            problems.append((row["person_key"], "missing"))
            continue
        mine = [r for r in read_dicts(path) if r["month"] == row["month"]]
        if len(mine) != 1:
            problems.append((row["person_key"], "rows", len(mine)))
            continue
        if mine[0]["comparison_band"] != row["comparison_band"] or mine[0]["compare_cell"] != row["compare_cell"]:
            problems.append((row["person_key"], mine[0]["compare_cell"], mine[0]["comparison_band"]))
    return problems


def test_done_check_G06_fixture_person_comparison_bands(fixture_out):
    expected = read_dicts(EXPECTED / "person_comparison.csv")
    assert len(expected) == 21
    assert _band_problems(fixture_out, expected) == []
    # negative control: one expected band changed is reported
    changed = [dict(r) for r in expected]
    changed[0]["comparison_band"] = "above_p50" if changed[0]["comparison_band"] != "above_p50" else "up_to_p50"
    assert _band_problems(fixture_out, changed) != []


def test_done_check_G06_fixture_layout_is_the_whole_list(fixture_out):
    wanted = sorted((EXPECTED / "layout.txt").read_text(encoding="utf-8").split())
    actual = sorted(p.relative_to(fixture_out).as_posix() for p in fixture_out.rglob("*") if p.is_file())
    assert actual == wanted
    # the phase 3 files are part of it
    assert "central/alert_events.csv" in actual and "exec/scenario_summary.csv" in actual
    # negative control: a list with one more path differs
    assert actual != sorted(wanted + ["central/zz.csv"])


def test_done_check_G06_manifest_counts_suppression(fixture_out):
    manifest = json.loads((fixture_out / "manifest.json").read_text(encoding="utf-8"))
    # org, u01, u02, u03 in one month; u03 by k, u02 by secondary (the review found "k" held the policy's k)
    assert manifest["suppression"] == {"cells": 4, "k": 1, "differencing": 0, "secondary": 1}


# ---- G15 -----------------------------------------------------------------------------------------

FIGURE_COLUMNS = (
    "persons", "active_persons", "active_days", "cost_ref", "p50_cost_ref", "p75_cost_ref", "p90_cost_ref",
    "p99_cost_ref", "top_decile_share", "is_final",
)


def _last_day(month):
    year, mon = int(month[:4]), int(month[5:7])
    first_next = date(year + (mon == 12), mon % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _person_folder_problems(out, person_month, person_day):
    problems = []
    keys = {r["person_key"] for r in person_month}
    folders = sorted(p.name for p in (out / "person").iterdir() if p.is_dir())
    if folders != sorted(keys):
        problems.append(("person folders differ from person_month",))
    for key in folders:
        mine_month = [r for r in person_month if r["person_key"] == key]
        mine_day = [r for r in person_day if r["person_key"] == key]
        for name, central_rows in (("person_month", mine_month), ("person_day", mine_day)):
            rows = read_dicts(out / "person" / key / (name + ".csv"))
            if any(r["person_key"] != key for r in rows):
                problems.append(("foreign person row", key, name))
            if name == "person_month":
                # spec v1.3 (owner decision of 2026-10-16): a person folder carries no manager_key
                central_rows = [{c: v for c, v in r.items() if c != "manager_key"} for r in central_rows]
            if rows != central_rows:
                problems.append(("person rows differ from central", key, name))
        files = sorted(p.name for p in (out / "person" / key).iterdir())
        if files != ["comparison.csv", "person_day.csv", "person_month.csv"]:
            problems.append(("person folder files", key, files))
        for r in read_dicts(out / "person" / key / "comparison.csv"):
            home = [m for m in mine_month if m["month"] == r["month"]]
            if len(home) != 1 or home[0]["cost_ref"] != r["own_cost_ref"]:
                problems.append(("own_cost_ref", key, r["month"]))
    return problems


def _expected_manager_cells(out, by_cell, months, data_through, line_dimension):
    """The cells each manager may see, and the set of unit heads: cells of units they head on that month's E, and
    every unit cell below."""
    units = [u for u in read_dicts(out / "central" / "units.csv") if u["dimension"] == line_dimension]
    children = {}
    for (month, cell_id), r in by_cell.items():
        if r["parent_cell_id"]:
            children.setdefault((month, r["parent_cell_id"]), []).append(cell_id)

    def below(month, cell_id):
        found, stack = set(), [cell_id]
        while stack:
            cur = stack.pop()
            for child in children.get((month, cur), []):
                if child not in found:
                    found.add(child)
                    stack.append(child)
        return found

    expected_cells, managers = {}, set()
    for month in months:
        eval_day = min(_last_day(month), data_through).isoformat()
        for u in units:
            if u["valid_from"] <= eval_day and (not u["valid_to"] or u["valid_to"] >= eval_day) and u["head_key"]:
                managers.add(u["head_key"])
                cell = "unit:" + u["unit_id"]
                if (month, cell) in by_cell:
                    bucket = expected_cells.setdefault(u["head_key"], set())
                    bucket.add((month, cell))
                    bucket.update((month, c) for c in below(month, cell))
    return expected_cells, managers


def _manager_folder_problems(out, by_cell, expected_cells, managers):
    problems = []
    on_disk = sorted(p.name for p in (out / "manager").iterdir() if p.is_dir())
    if on_disk != sorted(managers):
        problems.append(("manager folders", on_disk, sorted(managers)))
    for key in on_disk:
        path = out / "manager" / key / "groups_month.csv"
        rows = read_dicts(path)
        got = {(r["month"], r["cell_id"]) for r in rows}
        if any(r["cell_id"].startswith("org:") for r in rows):
            problems.append(("org cell in a manager folder", key))
        if got != expected_cells.get(key, set()):
            problems.append(("manager cells", key))
        for r in rows:
            if marked_view(by_cell.get((r["month"], r["cell_id"]))) != r:
                problems.append(("manager row differs from central", key, r["cell_id"]))
    return problems, on_disk


def _exec_cell_problems(exec_rows, by_cell, exec_max_depth):
    problems = []
    for r in exec_rows:
        if int(r["depth"]) > exec_max_depth:
            problems.append(("exec row deeper than max_depth", r["cell_id"]))
        if marked_view(by_cell.get((r["month"], r["cell_id"]))) != r:
            problems.append(("exec row differs from central", r["cell_id"]))
    want = sorted(key for key, r in by_cell.items() if int(r["depth"]) <= exec_max_depth)
    if sorted((r["month"], r["cell_id"]) for r in exec_rows) != want:
        problems.append(("exec cells",))
    return problems


def _exec_seat_problems(out, k):
    problems = []
    seats = {(r["month"], r["provider"], r["provider_account_id"]): r for r in read_dicts(out / "central" / "seats_month.csv")}
    for r in read_dicts(out / "exec" / "seats_month.csv"):
        c = seats[(r["month"], r["provider"], r["provider_account_id"])]
        billed, active, idle = int(c["seats_billed"]), c["active_seats"], c["idle_seats"]
        on_eval = int(c["seats_on_eval_day"])
        if active != "" and r["active_seats"] != "" and not (int(active) >= k and billed - int(active) >= k):
            problems.append(("exec active_seats", r["month"], r["provider"]))
        if r["active_seats"] != "" and r["active_seats"] != active:
            problems.append(("exec active_seats value", r["month"], r["provider"]))
        if idle != "" and r["idle_seats"] != "" and not (int(idle) >= k and on_eval - int(idle) >= k):
            problems.append(("exec idle_seats", r["month"], r["provider"]))
    for r in read_dicts(out / "exec" / "seats_month.csv"):
        c = seats[(r["month"], r["provider"], r["provider_account_id"])]
        if r["idle_seats"] != "" and r["idle_seats"] != c["idle_seats"]:
            problems.append(("exec idle_seats value", r["month"], r["provider"]))
    return problems


def _exec_cost_pool_problems(out, k, person_day):
    problems = []
    users = {}
    for r in person_day:
        if r["identity_status"] == "resolved":
            users.setdefault((r["month"], r["provider"], r["provider_account_id"]), set()).add(r["person_key"])
    for r in read_dicts(out / "exec" / "costs_month.csv"):
        n = len(users.get((r["month"], r["provider"], r["provider_account_id"]), set()))
        if n < k and any(r[c] != "" for c in ("usage_cost_source", "usage_cost_ref", "total_cost_ref")):
            problems.append(("exec costs row under k users shows usage figures", r["month"], r["provider"]))
    hidden = {}
    for r in read_dicts(out / "exec" / "costs_month.csv"):
        hidden[(r["month"], r["provider"], r["provider_account_id"])] = r["usage_cost_ref"] == "" and r["total_cost_ref"] == ""
    for r in read_dicts(out / "exec" / "pools_month.csv"):
        gone = hidden.get((r["month"], r["provider"], r["provider_account_id"]))
        shown = any(r[c] != "" for c in ("gross_credits", "pool_used_credits", "overage_credits", "budget_state"))
        if gone and shown:
            problems.append(("exec pool of a hidden costs row shows figures", r["month"], r["provider"]))
    return problems


def _suppressed_row_problems(out, central, exec_rows, on_disk):
    """Suppressed rows carry no figure, in every folder."""
    problems = []
    for r in central:
        if r["suppressed"] == "true" and any(r[c] != "" for c in FIGURE_COLUMNS):
            problems.append(("suppressed row with a figure", "central", r["cell_id"]))
        if r["suppressed"] == "true" and r["suppression_reason"] not in ("k", "differencing", "secondary"):
            problems.append(("suppressed row without a reason", "central", r["cell_id"]))
        if r["suppressed"] == "false" and r["suppression_reason"] != "":
            problems.append(("published row with a reason", "central", r["cell_id"]))
    # outside central the row says hidden, never why (7.6.2)
    marked = [("exec", exec_rows)]
    for key in on_disk:
        marked.append((key, read_dicts(out / "manager" / key / "groups_month.csv")))
    for name, rows in marked:
        for r in rows:
            if "suppressed" in r or "suppression_reason" in r:
                problems.append(("a reason column outside central", name, r["cell_id"]))
            if r.get("hidden") == "true" and any(r[c] != "" for c in FIGURE_COLUMNS):
                problems.append(("hidden row with a figure", name, r["cell_id"]))
    return problems


def isolation_problems(out, k, exec_max_depth, line_dimension="line"):
    """Every break of audience isolation (spec 7.6, 7.6.1, G15) found in a gold folder."""
    out = Path(out)
    problems = []
    central = read_dicts(out / "central" / "groups_month.csv")
    by_cell = {(r["month"], r["cell_id"]): r for r in central}
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data_through = date.fromisoformat(manifest["data_through"])

    # person folders
    person_month = read_dicts(out / "central" / "person_month.csv")
    person_day = read_dicts(out / "central" / "person_day.csv")
    problems += _person_folder_problems(out, person_month, person_day)

    # manager folders: cells of units they head on that month's E, and every unit cell below
    months = sorted({r["month"] for r in central})
    expected_cells, managers = _expected_manager_cells(out, by_cell, months, data_through, line_dimension)
    managers |= {r["manager_key"] for r in person_month if r["manager_key"]}
    manager_problems, on_disk = _manager_folder_problems(out, by_cell, expected_cells, managers)
    problems += manager_problems

    # exec
    exec_rows = read_dicts(out / "exec" / "groups_month.csv")
    problems += _exec_cell_problems(exec_rows, by_cell, exec_max_depth)
    problems += _exec_seat_problems(out, k)
    problems += _exec_cost_pool_problems(out, k, person_day)

    # suppressed rows carry no figure, in every folder
    problems += _suppressed_row_problems(out, central, exec_rows, on_disk)
    return problems


def _write(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _rewrite(path, edit):
    from gold_helpers import read_csv

    header, rows = read_csv(path)
    edit(header, rows)
    _write(path, header, rows)


def test_done_check_G15_fixture_audience_isolation(fixture_out, tmp_path):
    assert isolation_problems(fixture_out, k=5, exec_max_depth=1) == []
    # assertion A07 and the phase 2 assertions hold on a fixture build
    from meterline_gold import run

    names = [n for n, _ in run.ASSERTIONS]
    assert {"A04", "A05", "A06", "A07"} <= set(names)
    inp = fixture_inputs()
    from gold_helpers import KEY

    con = run.prepare(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"], invoice=inp["invoice"],
        key=KEY,
    )
    assert run.failures(con) == []
    # negative controls: each planted violation is flagged
    pm = read_dicts(fixture_out / "central" / "person_month.csv")
    first, second = pm[0]["person_key"], pm[1]["person_key"]

    copy = tmp_path / "c1"
    shutil.copytree(fixture_out, copy)
    foreign = read_dicts(copy / "person" / second / "person_month.csv")[0]
    path = copy / "person" / first / "person_month.csv"
    header = list(foreign)
    _write(path, header, [[foreign[c] for c in header]])
    assert isolation_problems(copy, k=5, exec_max_depth=1) != []

    copy = tmp_path / "c2"
    shutil.copytree(fixture_out, copy)
    org_row = [r for r in read_dicts(copy / "central" / "groups_month.csv") if r["cell_id"] == "org:"][0]
    target = sorted((copy / "manager").iterdir())[0] / "groups_month.csv"
    header = list(org_row)
    _write(target, header, [[org_row[c] for c in header]] + [list(r.values()) for r in read_dicts(target)])
    assert isolation_problems(copy, k=5, exec_max_depth=1) != []

    copy = tmp_path / "c3"
    shutil.copytree(fixture_out, copy)
    deep = [r for r in read_dicts(copy / "central" / "groups_month.csv") if int(r["depth"]) == 2][0]
    target = copy / "exec" / "groups_month.csv"
    header = list(deep)
    _write(target, header, [list(r.values()) for r in read_dicts(target)] + [[deep[c] for c in header]])
    assert isolation_problems(copy, k=5, exec_max_depth=1) != []

    copy = tmp_path / "c4"
    shutil.copytree(fixture_out, copy)

    def plant(header, rows):
        i = header.index("suppressed")
        j = header.index("cost_ref")
        for r in rows:
            if r[i] == "true":
                r[j] = "1.23"
                return

    _rewrite(copy / "central" / "groups_month.csv", plant)
    assert isolation_problems(copy, k=5, exec_max_depth=1) != []


def test_done_check_G15_tiny_sim_audience_isolation(sim_builds):
    out, _ = sim_builds["tiny"]
    policy = yaml.safe_load(
        (Path(__import__("meterline_gold").__file__).resolve().parent / "examples" / "policy.example.yaml").read_text(
            encoding="utf-8"
        )
    )
    k = policy["privacy"]["k"]
    max_depth = policy.get("audiences", {}).get("exec", {}).get("max_depth", 1)
    assert isolation_problems(out, k=k, exec_max_depth=max_depth) == []
