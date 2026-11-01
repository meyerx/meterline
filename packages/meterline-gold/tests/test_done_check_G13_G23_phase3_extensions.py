"""Phase 3 extensions of G13 (key order), G15 (audience isolation of the phase 3 files) and G23 (A06 step a).

Spec section 14: G13 covers "every dataset present in the phase" (phase 3 adds scenario_persons, scenario_units,
scenario_summary, alert_events and named_exceptions, in every folder that holds them); G23's phase 3 item is an A06
case in which two unsuppressed cells of one month differ by 1 to k - 1 persons (step a).
"""

import csv
import json
from decimal import Decimal

from gold_helpers import KEY, fixture_inputs, read_csv, read_dicts

PHASE3_KEYS = {
    "scenario_persons": ("scenario_id", "month", "person_key"),
    "scenario_units": ("scenario_id", "month", "unit_id"),
    "scenario_summary": ("scenario_id", "month"),
    "alert_events": ("day", "event_type", "scenario_id", "subject_kind", "subject_key", "threshold"),
    "named_exceptions": ("month", "rule_id", "person_key"),
}
DECIMAL_COLUMNS = {"threshold"}


def _typed(column, value):
    if column in DECIMAL_COLUMNS:
        return (0, Decimal(value)) if value != "" else (-1, Decimal(0))
    return value


def _order_problems(path, key):
    header, rows = read_csv(path)
    idx = [(c, header.index(c)) for c in key]
    prev, problems = None, []
    for n, row in enumerate(rows):
        cur = tuple(_typed(c, row[i]) for c, i in idx)
        if prev is not None and not prev < cur:
            problems.append((n, prev, cur))
        prev = cur
    return problems


def _phase3_files(out):
    files = [(out / "central" / (name + ".csv"), name) for name in PHASE3_KEYS]
    files.append((out / "exec" / "scenario_summary.csv", "scenario_summary"))
    files += [(p, "named_exceptions") for p in sorted((out / "manager").glob("*/named_exceptions.csv"))]
    return files


def test_done_check_G13_phase3_datasets_sorted_by_key(fixture_out, sim_builds, tmp_path):
    from meterline_gold import write

    for name, key in PHASE3_KEYS.items():
        assert tuple(write.DATASET_KEYS[name]) == key, name
    checked = 0
    for out in (fixture_out, sim_builds["tiny"][0]):
        for path, name in _phase3_files(out):
            assert _order_problems(path, PHASE3_KEYS[name]) == [], path
            checked += 1
    assert checked >= 16
    # negative control: alert_events with its rows reversed is flagged
    header, rows = read_csv(fixture_out / "central" / "alert_events.csv")
    assert len(rows) > 1
    bad = tmp_path / "alert_events.csv"
    with open(bad, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(header)
        w.writerows(reversed(rows))
    assert _order_problems(bad, PHASE3_KEYS["alert_events"]) != []


def _isolation_problems(out, k):
    problems = []
    central_summary = {(r["scenario_id"], r["month"]): r for r in read_dicts(out / "central" / "scenario_summary.csv")}
    exec_summary = read_dicts(out / "exec" / "scenario_summary.csv")
    if not exec_summary:
        problems.append("exec scenario_summary is empty")
    for r in exec_summary:
        c = central_summary.get((r["scenario_id"], r["month"]))
        if c is None:
            problems.append(("exec row without a central row", r))
            continue
        persons, stopped = int(c["persons"]), int(c["persons_stopped"])
        shown = persons >= k and stopped >= k and persons - stopped >= k
        if (r["persons_stopped"] != "") != shown or (shown and r["persons_stopped"] != c["persons_stopped"]):
            problems.append(("persons_stopped", r))
        if r["persons"] != c["persons"] or r["units"] != c["units"]:
            problems.append(("exec counts differ", r))
    central_rows = read_dicts(out / "central" / "named_exceptions.csv")
    for folder in sorted((out / "manager").iterdir()):
        for r in read_dicts(folder / "named_exceptions.csv"):
            if r["manager_key"] != folder.name:
                problems.append(("another manager's row", folder.name, r))
            if r["audience"] != "manager" or r["condition"] == "month_cost_ref_above":
                problems.append(("a rule a manager must not receive", folder.name, r))
            if r not in central_rows:
                problems.append(("not in central", folder.name, r))
    return problems


def test_done_check_G15_phase3_files_follow_the_audience_rules(fixture_out, sim_builds, tmp_path):
    policy_k = 5
    for out in (fixture_out, sim_builds["tiny"][0]):
        assert _isolation_problems(out, policy_k) == [], out
    # negative control: one manager row put in a foreign folder is flagged
    import shutil

    copy = tmp_path / "gold"
    shutil.copytree(fixture_out, copy)
    managers = sorted(p for p in (copy / "manager").iterdir())
    src = next(p for p in managers if len(read_dicts(p / "named_exceptions.csv")) > 0)
    dst = next(p for p in managers if p != src)
    text = (src / "named_exceptions.csv").read_text(encoding="utf-8").splitlines()
    with open(dst / "named_exceptions.csv", "a", encoding="utf-8", newline="") as handle:
        handle.write(text[1] + "\n")
    assert _isolation_problems(copy, policy_k) != []


def _prepared():
    from meterline_gold import run

    inp = fixture_inputs()
    return run.prepare(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY,
    )


def test_done_check_G23_phase3_A06_fires_on_a_differencing_pair():
    """Two unsuppressed cells of one month (org and u01) that differ by one person: A06 step a must fire."""
    from meterline_gold import run

    con = _prepared()
    assert run.failures(con) == []
    k = con.execute("SELECT k FROM policy_t").fetchone()[0]
    head = con.execute(
        "SELECT person_key FROM cell_direct WHERE month = '2026-05' AND cell_id = 'unit:u01'").fetchall()
    assert len(head) == 1
    # org loses one person that u01 keeps: the pair differs by exactly one person; the privacy loop itself
    # (step a) suppresses both, then the corruption puts them back as published
    con.execute(
        "UPDATE cells SET persons_list = list_filter(persons_list, x -> x <> ?) "
        "WHERE month = '2026-05' AND cell_id = 'org:'", [head[0][0]])
    states = run.run_privacy(con, k)
    assert states[("2026-05", "org:")] == "differencing" and states[("2026-05", "unit:u01")] == "differencing"
    con.execute("UPDATE cell_state SET suppressed = false, reason = NULL "
                "WHERE month = '2026-05' AND cell_id IN ('org:', 'unit:u01')")
    failed = [n for n, _ in run.failures(con)]
    assert "A06" in failed, failed
    # only the pair is wrong: the rows A06 reports are the two cells
    rows = [r for n, r in run.failures(con) if n == "A06"]
    assert rows and rows[0][1] in ("org:", "unit:u01") and rows[0][2] == "step a", rows
