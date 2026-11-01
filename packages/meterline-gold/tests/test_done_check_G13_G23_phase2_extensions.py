"""Phase 2 extensions of G13 (key order) and G23 (assertions fire) for the datasets and assertions phase 2 adds.

Spec section 14: G13 covers "every dataset present in the phase" and G23 "each of A01-A09". The phase 1 files of
those checks stay as they are; these tests carry the phase 2 part.
"""

import csv

import pytest

from gold_helpers import KEY, fixture_inputs, read_csv

PHASE2_KEYS = {
    "groups_month": ("month", "cell_id"),
    "comparison_groups": ("month", "compare_cell"),
    "comparison": ("month",),
}


def _order_problems(path, key):
    header, rows = read_csv(path)
    idx = [header.index(c) for c in key]
    prev, problems = None, []
    for n, row in enumerate(rows):
        cur = tuple(row[i] for i in idx)
        if prev is not None and not prev < cur:
            problems.append((n, prev, cur))
        prev = cur
    return problems


def _phase2_files(out):
    files = [out / "central" / "groups_month.csv", out / "exec" / "groups_month.csv",
             out / "central" / "comparison_groups.csv"]
    files += sorted((out / "manager").glob("*/groups_month.csv"))
    files += sorted((out / "person").glob("*/comparison.csv"))
    return files


def test_done_check_G13_phase2_datasets_sorted_by_key(fixture_out, sim_builds, tmp_path):
    from meterline_gold import write

    for name, key in PHASE2_KEYS.items():
        assert tuple(write.DATASET_KEYS[name]) == key, name
    checked = 0
    for out in (fixture_out, sim_builds["tiny"][0]):
        for path in _phase2_files(out):
            name = "comparison" if path.name == "comparison.csv" else path.stem
            assert _order_problems(path, PHASE2_KEYS[name]) == [], path
            checked += 1
    assert checked > 30
    # negative control: groups_month with its rows reversed is flagged
    src = sim_builds["tiny"][0] / "central" / "groups_month.csv"
    header, rows = read_csv(src)
    assert len(rows) > 1
    bad = tmp_path / "groups_month.csv"
    with open(bad, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(header)
        w.writerows(reversed(rows))
    assert _order_problems(bad, PHASE2_KEYS["groups_month"]) != []


def _prepared():
    from meterline_gold import run

    inp = fixture_inputs()
    return run.prepare(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY,
    )


CORRUPTIONS = {
    # the org cell no longer equals the sum of the person_month rows
    "A04": "UPDATE cell_figures SET cost_ref = cost_ref + 1 WHERE cell_id = 'org:'",
    # a person of the sibling cell joins u03: u03 and u02 now overlap without one holding the other
    "A05": "INSERT INTO cell_members SELECT month, 'unit:u03', person_key FROM cell_members "
           "WHERE cell_id = 'unit:u02' ORDER BY person_key LIMIT 1",
    # u03 has 4 persons (< k) and is published
    "A06": "UPDATE cell_state SET suppressed = false, reason = NULL WHERE cell_id = 'unit:u03'",
    # the exec copy of a groups_month row differs from the central row
    "A07": "UPDATE exec_groups_month SET cost_ref = cost_ref + 1 WHERE cell_id = 'org:'",
}


def test_done_check_G23_phase2_assertions_hold_on_a_clean_build():
    from meterline_gold import run

    con = _prepared()
    assert {"A04", "A05", "A06", "A07"} <= {n for n, _ in run.ASSERTIONS}
    assert run.failures(con) == []


@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_done_check_G23_phase2_assertion_fires(name):
    from meterline_gold import run

    con = _prepared()
    con.execute(CORRUPTIONS[name])
    failed = [n for n, _ in run.failures(con)]
    assert name in failed
