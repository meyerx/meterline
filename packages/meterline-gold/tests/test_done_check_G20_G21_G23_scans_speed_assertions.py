"""G20 (scans), G21 (speed) and G23 (the assertions fire)."""

import csv
import os
import shutil
import time
from pathlib import Path

import pytest

from gold_helpers import (
    KEY, dataset_queries, ends_with_order_by, fixture_inputs, read_csv, scan_forbidden_names, sql_scan_findings,
    split_statements, strip_sql,
)


def _pkg():
    import meterline_gold

    return Path(meterline_gold.__file__).resolve().parent


# ---- G20 -----------------------------------------------------------------------------------------


def test_done_check_G20_no_wall_clock_or_random_names(tmp_path):
    assert scan_forbidden_names(_pkg()) == []
    planted = tmp_path / "planted.py"
    planted.write_text("import datetime\nx = datetime.now()\n", encoding="utf-8")
    assert scan_forbidden_names(tmp_path) == [("planted.py", "datetime.now")]


def test_done_check_G20_sql_has_no_division_float_or_average(tmp_path):
    sql_dir = _pkg() / "sql"
    assert sorted(p.name for p in sql_dir.glob("*.sql"))
    assert sql_scan_findings(sql_dir) == []
    # the macros file is the one place a division may sit
    macros = (sql_dir / "00_macros.sql").read_text(encoding="utf-8")
    assert "/" in strip_sql(macros) or "//" in macros
    # negative controls: each planted violation is flagged
    for text, expected in (
        ("SELECT a / b FROM t;\n", "slash"),
        ("SELECT CAST(a AS DOUBLE) FROM t;\n", r"\bdouble\b"),
        ("SELECT AVG(a) FROM t;\n", r"\bavg\s*\("),
        ("SELECT quantile_cont(a, 0.5) FROM t;\n", "quantile_cont"),
        ("SELECT median(a) FROM t;\n", r"\bmedian\s*\("),
    ):
        d = tmp_path / ("neg_" + str(abs(hash(text))))
        d.mkdir()
        (d / "10_planted.sql").write_text(text, encoding="utf-8")
        assert expected in [pattern for _, pattern in sql_scan_findings(d)]
    d = tmp_path / "fine"
    d.mkdir()
    (d / "10_ok.sql").write_text("-- a / b in a comment\nSELECT 'a/b' AS s FROM t;\n", encoding="utf-8")
    assert sql_scan_findings(d) == []


def test_done_check_G20_every_dataset_query_ends_with_order_by(tmp_path):
    from meterline_gold import write

    found = dataset_queries(_pkg() / "sql")
    for name in write.DATASET_KEYS:
        assert found.get(name), "no query marked '-- dataset: %s'" % name
    for name, statements in found.items():
        for stmt in statements:
            assert ends_with_order_by(stmt), name
    # negative controls
    assert not ends_with_order_by("-- dataset: x\nSELECT a FROM t")
    assert not ends_with_order_by("-- dataset: x\nSELECT * FROM (SELECT a FROM t ORDER BY a) s")
    assert ends_with_order_by("-- dataset: x\nSELECT a FROM t ORDER BY a, b")
    assert len(split_statements("SELECT ';' ; SELECT 2;")) == 2


# ---- G21 -----------------------------------------------------------------------------------------


@pytest.mark.timing
@pytest.mark.skipif(bool(os.environ.get("CI")), reason="speed check: run in the done gate, not in CI")
def test_done_check_G21_vertical_sim_builds_in_under_60_seconds(sim_runs, example_files, tmp_path):
    import meterline_gold

    run = sim_runs["vertical"]
    policy, prices = example_files
    started = time.perf_counter()
    meterline_gold.build(
        silver=run.silver, org=run.org, policy=policy, prices=prices, key=KEY, out=tmp_path / "gold"
    )
    elapsed = time.perf_counter() - started
    print("G21: vertical sim, 6 months, built in %.1f s" % elapsed)
    assert elapsed < 60


# ---- G23 -----------------------------------------------------------------------------------------


def _prepared():
    from meterline_gold import run

    inp = fixture_inputs()
    return run.prepare(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY,
    )


def _first(table):
    return "rowid = (SELECT min(rowid) FROM %s)" % table


CORRUPTIONS = {
    "A01": "INSERT INTO person_day SELECT * FROM person_day WHERE " + _first("person_day"),
    "A02": "UPDATE person_day SET requests = coalesce(requests, 0) + 1 WHERE " + _first("person_day"),
    "A03": "UPDATE person_month SET cost_ref = cost_ref + 1 WHERE " + _first("person_month"),
    "A08": "UPDATE costs_month SET total_cost_ref = total_cost_ref + 1 WHERE " + _first("costs_month"),
}


def test_done_check_G23_assertions_hold_on_a_clean_build():
    from meterline_gold import run

    names = [n for n, _ in run.ASSERTIONS]
    assert {"A01", "A02", "A03", "A08"} <= set(names)
    con = _prepared()
    assert run.failures(con) == []


@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_done_check_G23_assertion_fires(name):
    from meterline_gold import run

    con = _prepared()
    con.execute(CORRUPTIONS[name])
    failed = [n for n, _ in run.failures(con)]
    assert name in failed


def test_done_check_G23_a09_fires_on_a_planted_identifier(fixture_out, tmp_path):
    from meterline_gold import write

    inp = fixture_inputs()
    assert write.a09_violations(fixture_out, inp["org"], inp["silver"]) == []
    copy = tmp_path / "copy"
    shutil.copytree(fixture_out, copy)
    path = copy / "central" / "person_day.csv"
    header, rows = read_csv(path)
    rows[0][header.index("product")] = "user_f01"  # an actor_id of the inputs
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    assert write.a09_violations(copy, inp["org"], inp["silver"]) != []
