"""A-3 of the v0.3 hardening spec: seats_month, pools_month and costs_month say whether their month is final.

The same two columns person_month has: ``month_status`` (complete or running) and ``is_final``. The simulated data is cut in
the middle of its last month, so that month is the month in progress; the earlier months are closed."""
import json
from pathlib import Path

import pytest

from gold_helpers import KEY, filter_silver, read_dicts

DATASETS = ("seats_month", "pools_month", "costs_month")


@pytest.fixture(scope="module")
def cut_builds(sim_runs, example_files, tmp_path_factory):
    """{"tiny": (gold folder, seconds)}: the tiny simulated data cut on the 15th of its last month, then built."""
    import meterline_gold

    run = sim_runs["tiny"]
    last = max(json.loads(line)["period_start"] for path in Path(run.silver).rglob("*.jsonl")
               for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    base = tmp_path_factory.mktemp("running")
    silver = filter_silver(run.silver, base / "silver", last[:8] + "15")
    policy, prices = example_files
    out = base / "gold"
    meterline_gold.build(silver=silver, org=run.org, policy=policy, prices=prices, key=KEY, out=out)
    return {"tiny": (out, 0)}


def _rows(out, audience, name):
    return read_dicts(out / audience / (name + ".csv"))


@pytest.mark.parametrize("audience", ["central", "exec"])
@pytest.mark.parametrize("name", DATASETS)
def test_the_latest_month_is_running_and_not_final_the_closed_ones_are_complete(cut_builds, audience, name):
    out, _seconds = cut_builds["tiny"]
    rows = _rows(out, audience, name)
    months = sorted({r["month"] for r in rows})
    assert len(months) >= 3
    latest, closed = months[-1], months[:-1]
    for r in rows:
        assert r["month_status"] == ("running" if r["month"] == latest else "complete"), (name, r["month"])
        if r["month"] == latest:
            assert r["is_final"] == "false", (name, r["month"])
    assert any(r["is_final"] == "true" for r in rows if r["month"] in closed), name


@pytest.mark.parametrize("audience", ["central", "exec"])
def test_the_new_columns_come_after_every_existing_one_and_the_marker_stays_last(cut_builds, audience):
    out, _seconds = cut_builds["tiny"]
    tail = ["hidden"] if audience == "exec" else []  # an exec form is the central columns, then the hidden marker
    for name in ("seats_month", "costs_month"):
        header = list(_rows(out, audience, name)[0])
        assert header[len(header) - len(tail) - 2:len(header) - len(tail)] == ["month_status", "is_final"], (audience, name, header[-4:])
        assert header[len(header) - len(tail):] == tail
    pools = list(_rows(out, audience, "pools_month")[0])
    assert pools[len(pools) - len(tail) - 1] == "month_status" and "is_final" in pools[:-1 - len(tail)]  # is_final was there already
    assert pools[len(pools) - len(tail):] == tail


def test_month_status_means_what_person_month_says(cut_builds):
    out, _seconds = cut_builds["tiny"]
    person_month = {r["month"]: r["month_status"] for r in _rows(out, "central", "person_month")}
    for name in DATASETS:
        for r in _rows(out, "central", name):
            assert r["month_status"] == person_month[r["month"]], (name, r["month"])
