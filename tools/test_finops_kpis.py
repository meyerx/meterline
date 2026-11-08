"""B7: the FinOps KPI cookbook is tested.

Every query of examples/01-sim-to-kpis/kpis.sql runs on a build of the ``tiny`` preset (seed 7); spend by provider and
month equals the simulator's ground truth for the same basis; no query reads delivery/ or manifest.json; the cookbook
includes the SQL word for word and says, for each KPI, the question, the folder and dataset, what not to add and how
hidden cells show.

Improvements 1 C adds three queries (cache savings, one unallocated share, spend by team) and the column traps: they
run on the ``tiny`` and ``vertical`` builds, cache savings equals a hand computation from ``person_day`` and the price
list, and the dataset reference page carries the two trap notes.

Run: python3 -m pytest -q tools/test_finops_kpis.py
"""
import importlib.util
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import readme_block  # noqa: E402

pytest.importorskip("duckdb")
meterline_gold = pytest.importorskip("meterline_gold")
meterline_sim = pytest.importorskip("meterline_sim")

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "examples" / "01-sim-to-kpis" / "kpis.sql"
DOC = ROOT / "docs" / "how-to" / "finops-kpis.md"
KEY = b"a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
EXPECTED = ["spend_by_provider_month", "cash_view", "spend_by_model", "spend_by_unit", "spend_by_team", "cost_per_active_person",
            "seat_utilisation", "idle_seat_cost", "cache_share_of_input", "cache_savings", "unreconciled_share",
            "one_unallocated_share", "month_over_month_growth"]
NEW = ["cache_savings", "one_unallocated_share", "spend_by_team"]
PRICES = Path(meterline_gold.__file__).resolve().parent / "examples" / "prices.example.yaml"


def _load_kpis():
    spec = importlib.util.spec_from_file_location("kpis_example", ROOT / "examples" / "01-sim-to-kpis" / "kpis.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kpis = _load_kpis()


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("b7_sim"))
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    out = tmp_path_factory.mktemp("b7_gold") / "gold"
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY, out=out)
    import json
    truth = json.loads((Path(run) / "ground_truth.json").read_text(encoding="utf-8"))
    return out, truth


@pytest.fixture(scope="module")
def vertical(tmp_path_factory):
    run = meterline_sim.generate(preset="vertical", seed=7, months=6, out=tmp_path_factory.mktemp("c_sim"))
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    out = tmp_path_factory.mktemp("c_gold") / "gold"
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY, out=out)
    return out


def _rows(gold, audience, name):
    import duckdb
    q = kpis.read_queries()[name]
    con = duckdb.connect(":memory:")
    try:
        kpis.load_views(con, gold, audience)
        kpis.load_prices(con, PRICES)
        cursor = con.execute(q["sql"])
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in cursor.fetchall()]
    finally:
        con.close()


def test_the_queries_are_the_kpis_of_the_cookbook():
    assert list(kpis.read_queries()) == EXPECTED


@pytest.mark.parametrize("name", EXPECTED)
def test_done_check_B7_every_query_runs_on_the_tiny_build(tiny, name):
    gold, _ = tiny
    q = kpis.read_queries()[name]
    assert _rows(gold, q["audience"], name), name


def _truth(truth):
    return {(t["provider"], t["cost_basis"] or "", t["month"]): Decimal(t["cost"])
            for t in truth["totals"] if t["kind"] == "usage" and t["grain"] == "day" and t["cost"] is not None}


def _final_provider_months(gold):
    """{(provider, month)} of central/costs_month whose is_final is true, read straight from the file."""
    import csv

    with open(Path(gold) / "central" / "costs_month.csv", encoding="utf-8", newline="") as handle:
        return {(r["provider"], r["month"]) for r in csv.DictReader(handle) if r["is_final"] == "true"}


def _without_the_final_filter(name):
    """The query with the line that leaves out the month in progress deleted: how a reader asks for that month."""
    sql = kpis.read_queries()[name]["sql"]
    kept = [line for line in sql.splitlines() if "-- the month in progress is left out" not in line and "HAVING bool_and(is_final)" not in line]
    assert len(kept) == len(sql.splitlines()) - 1, name
    return "\n".join(kept)


def _rows_of(gold, audience, sql):
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        kpis.load_views(con, gold, audience)
        kpis.load_prices(con, PRICES)
        cursor = con.execute(sql)
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in cursor.fetchall()]
    finally:
        con.close()


def test_done_check_B7_spend_by_provider_and_month_equals_the_ground_truth(tiny):
    gold, truth = tiny
    final = _final_provider_months(gold)
    expected = {k: v for k, v in _truth(truth).items() if (k[0], k[2]) in final}
    got = {(r["provider"], r["basis"] or "", r["month"]): r["spend_source"] for r in _rows(gold, "central", "spend_by_provider_month")}
    assert expected and got == expected


@pytest.fixture(scope="module")
def running(tmp_path_factory):
    """The tiny simulated data cut on the 15th of its last month and built: that month is the month in progress."""
    import json
    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("run_sim"))
    paths = sorted(Path(run).rglob("*.jsonl"))
    last = max(json.loads(line)["period_start"] for p in paths for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
    silver = tmp_path_factory.mktemp("run_silver")
    for p in paths:
        target = silver / p.relative_to(run)
        target.parent.mkdir(parents=True, exist_ok=True)
        kept = [line for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and json.loads(line)["period_start"] <= last[:8] + "15"]
        target.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    out = tmp_path_factory.mktemp("run_gold") / "gold"
    meterline_gold.build(silver=silver, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY, out=out)
    return out


def test_the_month_in_progress_is_left_out_unless_asked(tiny, running):
    gold, truth = running, None
    import csv
    with open(Path(gold) / "central" / "costs_month.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    running = max(r["month"] for r in rows)
    assert {r["is_final"] for r in rows if r["month"] == running} == {"false"}  # the latest simulated month is still running
    for name, audience in (("spend_by_provider_month", "central"), ("month_over_month_growth", "exec")):
        assert running not in {r["month"] for r in _rows(gold, audience, name)}, name
        assert running in {r["month"] for r in _rows_of(gold, audience, _without_the_final_filter(name))}, name
    # asked for: every provider-month of costs_month is there, the running one included
    asked = {(r["provider"], r["month"]) for r in _rows_of(gold, "central", _without_the_final_filter("spend_by_provider_month"))}
    assert asked == {(r["provider"], r["month"]) for r in rows}


def test_done_check_B7_exec_spend_matches_the_truth_and_withholds_the_rest(tiny):
    gold, truth = tiny
    expected = _truth(truth)
    rows = _rows(gold, "exec", "spend_by_provider_month")
    shown = [r for r in rows if not r["withheld"]]
    withheld = [r for r in rows if r["withheld"]]
    assert shown and withheld
    for r in shown:
        assert r["spend_source"] == expected[(r["provider"], r["basis"] or "", r["month"])]
    # a withheld row has no figure at all, not a partial sum or a zero
    assert all(r["spend_source"] is None and r["spend_ref"] is None and r["spend_ref_with_seats"] is None
               and r["routed_to_another_provider"] is None for r in withheld)


def test_a_wrong_expected_total_is_caught(tiny):
    # negative control: the same comparison against a truth one cent higher differs
    gold, truth = tiny
    expected = {k: v + Decimal("0.01") for k, v in _truth(truth).items()}
    got = {(r["provider"], r["basis"] or "", r["month"]): r["spend_source"]
           for r in _rows_of(gold, "central", _without_the_final_filter("spend_by_provider_month"))}
    assert got != expected


FORBIDDEN = re.compile(r"delivery|manifest", re.I)


def test_done_check_B7_no_query_reads_delivery_or_the_manifest(tiny):
    gold, _ = tiny
    assert not FORBIDDEN.search(SQL.read_text(encoding="utf-8"))
    import duckdb
    for audience in ("exec", "central"):
        con = duckdb.connect(":memory:")
        loaded = kpis.load_views(con, gold, audience)
        con.close()
        assert "directory" not in loaded and loaded


def test_the_forbidden_scan_catches_a_planted_read():
    # negative control
    assert FORBIDDEN.search("SELECT * FROM read_csv('gold/delivery/directory.csv')")
    assert FORBIDDEN.search("SELECT * FROM read_json('gold/manifest.json')")


def test_the_cookbook_includes_the_sql_word_for_word():
    text = DOC.read_text(encoding="utf-8")
    assert readme_block.sync_sql(text, SQL.read_text(encoding="utf-8")) == text, \
        "regenerate with: python tools/readme_block.py --sync-sql examples/01-sim-to-kpis/kpis.sql --readme docs/how-to/finops-kpis.md"
    for title, query in readme_block.sql_queries(SQL.read_text(encoding="utf-8")):
        assert readme_block.block_after(text, title, "sql").strip() == query


def test_the_cookbook_sync_catches_a_drifted_block():
    # negative control
    text = DOC.read_text(encoding="utf-8")
    drifted = text.replace("sum(usage_cost_ref)", "sum(usage_cost_source)", 1)
    assert drifted != text
    assert readme_block.sync_sql(drifted, SQL.read_text(encoding="utf-8")) != drifted


@pytest.mark.parametrize("title", [t for t, _ in readme_block.sql_queries(SQL.read_text(encoding="utf-8"))])
def test_each_kpi_section_says_question_folder_what_not_to_add_and_hidden_cells(title):
    text = DOC.read_text(encoding="utf-8")
    section = re.split(r"^### ", text.split("### " + title, 1)[1], flags=re.M)[0]
    for label in ("**Question.**", "**Folder and dataset.**", "**Do not add.**", "**Hidden cells.**"):
        assert label in section, (title, label)


def test_the_cookbook_has_the_cost_section_and_the_cache_savings_answer():
    text = DOC.read_text(encoding="utf-8")
    assert re.search(r"^## Billed, consumption and reference cost$", text, re.M)
    section = text.split("## Billed, consumption and reference cost", 1)[1]
    assert "Do:" in section and "Don't:" in section
    assert "cache savings" in text.lower() and "no cache price" in text.lower()  # gold has none: the query reads the price list


def test_the_dataset_reference_links_to_the_cost_section():
    page = (ROOT / "docs" / "reference" / "gold-datasets.md").read_text(encoding="utf-8")
    assert "../how-to/finops-kpis.md#billed-consumption-and-reference-cost" in page


# ---- Improvements 1 C: cache savings, one unallocated share, the team rule, the column traps -----------------------


@pytest.mark.parametrize("name", NEW)
def test_done_check_C_the_new_queries_run_on_the_tiny_and_vertical_builds(tiny, vertical, name):
    for gold in (tiny[0], vertical):
        assert _rows(gold, kpis.read_queries()[name]["audience"], name), (name, str(gold))


def _price_on(prices, provider, model, day):
    """The per-million prices valid on ``day``: the entry with the latest valid_from not after it."""
    found = [p for p in prices if p["provider"] == provider and p["model"] == model and str(p["valid_from"]) <= day]
    return max(found, key=lambda p: str(p["valid_from"]))["per_million"] if found else None


def _hand_cache_savings(gold):
    import csv

    import yaml
    prices = yaml.safe_load(PRICES.read_text(encoding="utf-8"))["token_prices"]
    priced_providers = {p["provider"] for p in prices}
    total = {}
    with open(Path(gold) / "central" / "person_day.csv", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["provider"] not in priced_providers:
                continue
            read = int(row["tokens_cache_read"] or 0)
            price = _price_on(prices, row["provider"], row["model"], row["day"])
            if price is None:
                continue
            key = (row["provider"], row["month"])
            saving = Decimal(read) * (Decimal(price["input_uncached"]) - Decimal(price["cache_read"])) / Decimal(1000000)
            total[key] = total.get(key, Decimal(0)) + saving
    return total


@pytest.mark.parametrize("which", ["tiny", "vertical"])
def test_done_check_C_cache_savings_equal_a_hand_computation(tiny, vertical, which):
    gold = tiny[0] if which == "tiny" else vertical
    expected = _hand_cache_savings(gold)
    rows = _rows(gold, "central", "cache_savings")
    got = {(r["provider"], r["month"]): r["savings_ref_estimate"] for r in rows}
    assert expected and set(got) == set(expected)
    for key, value in expected.items():
        assert got[key] == value.quantize(Decimal("0.000000000001")), key
    assert {"github_copilot"} & {r["provider"] for r in rows} == set()  # priced in credits: no cache price, said in the cookbook


def test_a_wrong_hand_computation_is_caught(tiny):
    # negative control: a price one cent too high changes the figure, so the equality above is not vacuous
    gold, _ = tiny
    expected = {k: v + Decimal("0.01") for k, v in _hand_cache_savings(gold).items()}
    got = {(r["provider"], r["month"]): r["savings_ref_estimate"] for r in _rows(gold, "central", "cache_savings")}
    assert any(got[k] != v.quantize(Decimal("0.000000000001")) for k, v in expected.items())


@pytest.mark.parametrize("which", ["tiny", "vertical"])
def test_done_check_C_the_unallocated_share_is_between_0_and_1_and_matches_its_parts(tiny, vertical, which):
    gold = tiny[0] if which == "tiny" else vertical
    rows = _rows(gold, "central", "one_unallocated_share")
    shown = [r for r in rows if not r["left_out"]]
    assert shown and any(r["left_out"] for r in rows)
    for r in shown:
        assert Decimal(0) <= r["unallocated_share"] <= Decimal(1), r
        parts = (r["cost_not_a_person"] or Decimal(0)) + r["unallocated_cost_source"]
        assert abs(r["unallocated_share"] * r["org_cost_source"] - parts) <= Decimal("0.00005") * r["org_cost_source"], r
    for r in rows:
        if r["left_out"]:
            assert r["unallocated_share"] is None and r["unallocated_cost_source"] is None, r  # never a made-up zero
    assert {r["provider"] for r in rows if r["left_out"]} == {"github_copilot"}  # its org total is billed, its actors consumption


def _person_month(gold):
    import csv
    with open(Path(gold) / "central" / "person_month.csv", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize("which", ["tiny", "vertical"])
def test_done_check_C_every_person_month_lands_on_exactly_one_team_row(tiny, vertical, which):
    gold = tiny[0] if which == "tiny" else vertical
    people = _person_month(gold)
    rows = _rows(gold, "central", "spend_by_team")
    assert sum(r["persons"] for r in rows) == len(people)
    for month in sorted({p["month"] for p in people}):
        want = sum(Decimal(p["cost_ref"]) for p in people if p["month"] == month)
        assert sum(r["spend_ref"] for r in rows if r["month"] == month) == want.quantize(Decimal("0.000000000001")), month


def test_people_who_sit_on_a_department_row_are_not_a_team_and_still_counted(vertical):
    rows = _rows(vertical, "central", "spend_by_team")
    off_team = [r for r in rows if not r["is_team"]]
    assert off_team and {r["unit_kind"] for r in off_team} >= {"department", "division"}
    assert all(r["unit_kind"] == "team" for r in rows if r["is_team"])
    # a department row rolls up into itself; a team rolls up into the nearest department above it
    assert all(r["department_id"] == r["unit_id"] for r in off_team if r["unit_kind"] == "department")
    assert any(r["is_team"] and r["department_id"] for r in rows)


def test_a_join_that_ignores_the_validity_range_double_counts(vertical):
    # negative control: units.csv has one row per validity period, so joining on unit_id alone repeats a person
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        kpis.load_views(con, vertical, "central")
        naive = con.execute("SELECT count(*) FROM person_month p JOIN units u ON u.unit_id = p.unit_id").fetchone()[0]
    finally:
        con.close()
    assert naive > len(_person_month(vertical))


def test_the_dataset_reference_carries_the_two_trap_notes():
    page = (ROOT / "docs" / "reference" / "gold-datasets.md").read_text(encoding="utf-8")
    assert page.count("../how-to/finops-kpis.md#column-traps") >= 2
    data_quality = page.split("## `data_quality`", 1)[1].split("\n## ", 1)[0]
    units = page.split("## `units`", 1)[1].split("\n## ", 1)[0]
    assert "metric" in data_quality and "column-traps" in data_quality
    assert "validity" in units.lower() and "column-traps" in units


def test_the_cookbook_has_the_column_traps_and_the_team_rule():
    text = DOC.read_text(encoding="utf-8")
    traps = text.split("## Column traps", 1)[1].split("\n## ", 1)[0]
    assert "`data_quality`" in traps and "`metric`" in traps and "`units`" in traps and "valid" in traps
    team = text.split("### Spend by team", 1)[1].split("\n### ", 1)[0]
    for word in ("team", "department", "division", "nearest"):
        assert word in team, word
    cache = text.split("### Cache savings (reference estimate)", 1)[1].split("\n### ", 1)[0]
    assert "reference estimate" in cache and "Copilot" in cache and "price list" in cache
    one = text.split("### Reference unallocated share", 1)[1].split("\n### ", 1)[0]
    assert "github_copilot" in one and "left_out" in one


# ---- v0.3 hardening A-2: the cookbook totals do not add a proxy's calls to the provider's own ---------------------------

FIXTURE_INPUT = ROOT / "docs" / "gold-fixture" / "input"
FIXTURE_KEY = b"meterline-gold-fixture-key-not-a-secret"


@pytest.fixture(scope="module")
def routed(tmp_path_factory):
    """The hand-built gold fixture with two of the proxy's three records routed to anthropic and every record final."""
    import json
    import shutil

    base = tmp_path_factory.mktemp("routed")
    shutil.copytree(FIXTURE_INPUT, base / "in")
    for path in sorted((base / "in" / "silver" / "records").glob("*/*.jsonl")):
        out, routed_so_far = [], 0
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "is_final" in record:
                record["is_final"] = True
            if path.parent.name == "usage" and record["provider"] == "litellm" and routed_so_far < 2:
                record["model_provider"] = "anthropic"
                routed_so_far += 1
            out.append(json.dumps(record, sort_keys=True))
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    inp = base / "in"
    out = base / "out"
    meterline_gold.build(silver=inp / "silver", org=inp / "org", policy=inp / "policy.yaml", prices=inp / "prices.yaml",
                         invoice=inp / "invoice.csv", key=FIXTURE_KEY, out=out)
    return out


def _person_day(gold):
    import csv

    with open(Path(gold) / "central" / "person_day.csv", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_spend_total_subtracts_the_part_a_proxy_routed_to_another_provider(routed):
    days = _person_day(routed)
    whole = sum(Decimal(r["cost_ref"] or 0) for r in days if r["provider"] == "litellm")
    part = sum(Decimal(r["cost_ref"] or 0) for r in days if r["provider"] == "litellm" and r["model_provider"] == "anthropic")
    assert Decimal(0) < part < whole
    rows = {r["provider"]: r for r in _rows(routed, "central", "spend_by_provider_month")}
    assert rows["litellm"]["spend_ref"] == whole - part
    assert rows["litellm"]["routed_to_another_provider"] == part
    assert rows["litellm"]["spend_ref_with_seats"] == whole - part  # the proxy has no seat fee
    assert rows["anthropic"]["routed_to_another_provider"] == 0
    both = sum(Decimal(r["cost_ref"] or 0) for r in days if r["provider"] in ("litellm", "anthropic"))
    assert rows["litellm"]["spend_ref"] + rows["anthropic"]["spend_ref"] == both - part  # the routed calls are counted once


def test_the_model_total_subtracts_it_too(routed):
    days = _person_day(routed)
    part = sum(Decimal(r["cost_ref"] or 0) for r in days if r["provider"] == "litellm" and r["model_provider"] == "anthropic")
    whole = sum(Decimal(r["cost_ref"] or 0) for r in days if r["provider"] == "litellm")
    rows = [r for r in _rows(routed, "central", "spend_by_model") if r["provider"] == "litellm"]
    assert sum(r["spend_ref"] for r in rows) == whole - part and sum(r["routed_to_another_provider"] for r in rows) == part


def test_the_growth_total_subtracts_it_and_a_central_total_is_the_sum_without_the_routed_part(routed):
    days = _person_day(routed)
    part = sum(Decimal(r["cost_ref"] or 0) for r in days if r["provider"] == "litellm" and r["model_provider"] == "anthropic")
    import csv

    with open(Path(routed) / "central" / "costs_month.csv", encoding="utf-8", newline="") as handle:
        costs = list(csv.DictReader(handle))
    everything = sum(Decimal(r["total_cost_ref"]) for r in costs)
    sql = kpis.read_queries()["month_over_month_growth"]["sql"]
    rows = _rows_of(routed, "central", sql)
    assert len(rows) == 1 and rows[0]["spend_ref_with_seats"] == everything - part


# ---- v0.3 hardening B-6: cash view, cost_center, no active Anthropic seat line --------------------------------------------


def test_the_cash_view_is_seat_cost_plus_overage_amount_per_provider_and_month(routed):
    import csv

    def read(name):
        with open(Path(routed) / "central" / (name + ".csv"), encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    seats = {(r["provider"], r["month"]): Decimal(r["seat_cost"] or 0) for r in read("costs_month")}
    overage = {(r["provider"], r["month"]): Decimal(r["overage_amount"]) for r in read("pools_month") if r["overage_amount"]}
    invoice = {(r["provider"], r["month"]): Decimal(r["invoice_usage"]) for r in read("costs_month") if r["invoice_usage"]}
    rows = {(r["provider"], r["month"]): r for r in _rows(routed, "central", "cash_view")}
    assert set(rows) == set(seats)
    assert any(overage.get(k) for k in seats), "the fixture should hold an overage amount"
    for key, row in rows.items():
        assert row["cash_seats_and_overage"] == seats[key] + overage.get(key, Decimal(0)), key
        assert row["seat_cost"] == (seats[key] or None) or row["seat_cost"] == seats[key]
        assert row["invoice_usage"] == invoice.get(key)  # where a source gives it, and only there
    assert any(row["invoice_usage"] is not None for row in rows.values())


def test_the_team_query_returns_the_cost_center(tiny):
    gold, _ = tiny
    rows = _rows(gold, "central", "spend_by_team")
    assert rows and all("cost_center" in r for r in rows)
    assert any(r["cost_center"] for r in rows)


def test_the_example_price_list_has_no_active_anthropic_seat_line():
    import yaml

    text = (PRICES).read_text(encoding="utf-8")
    seats = yaml.safe_load(text).get("seats") or []
    assert not [s for s in seats if s.get("provider") == "anthropic"]
    assert any(line.lstrip().startswith("#") and "anthropic" in line and "per_month" in line for line in text.splitlines()), (
        "the commented-out line should stay as an example")
