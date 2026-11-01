"""Improvements 2 E4: one reference unallocated share, the role-to-audience table, the idle seat cost, the loaders.

- The cookbook names exactly one **reference unallocated share**; the descriptions of the ``data_quality`` metric
  ``unallocated_share`` and of ``reconciliation_month.unreconciled_share`` say they are not it and link that KPI.
- ``idle_seat_cost`` runs on the ``tiny`` and ``vertical`` builds and equals ``seats_month`` arithmetic from the CSV:
  ``idle_seats`` times ``seat_price`` for the rows whose seats come from the provider's assignments, nothing else. A row
  whose seats are counted from ``identities.csv`` (a Console account fed by the Admin connector, with a provider-wide
  seat price) gives no row.
- An app imports the loaders of ``kpis.py`` and runs a query through them.
- The gold README and AGENTS.md carry the role-to-audience table.

Run: python3 -m pytest -q tools/test_finops_kpis_round2.py
"""
import csv
import importlib.util
import io
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import readme_block  # noqa: E402

pytest.importorskip("duckdb")
meterline_gold = pytest.importorskip("meterline_gold")
meterline_sim = pytest.importorskip("meterline_sim")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "01-sim-to-kpis"
SQL = EXAMPLE / "kpis.sql"
DOC = ROOT / "docs" / "how-to" / "finops-kpis.md"
KEY = b"a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
BASE = Path(meterline_gold.__file__).resolve().parent / "examples"
PRICES = BASE / "prices.example.yaml"
ANCHOR = "#reference-unallocated-share"
LINK = "../how-to/finops-kpis.md" + ANCHOR
PHRASE = "reference unallocated share"


def _load_kpis():
    spec = importlib.util.spec_from_file_location("kpis_example_round2", EXAMPLE / "kpis.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kpis = _load_kpis()


def _build(preset, tmp_path_factory, tag):
    run = meterline_sim.generate(preset=preset, seed=7, months=6, out=tmp_path_factory.mktemp(tag + "_sim"))
    out = tmp_path_factory.mktemp(tag + "_gold") / "gold"
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=BASE / "policy.example.yaml", prices=PRICES,
                         key=KEY, out=out)
    return out


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return _build("tiny", tmp_path_factory, "e4t")


@pytest.fixture(scope="module")
def vertical(tmp_path_factory):
    return _build("vertical", tmp_path_factory, "e4v")


def _rows(gold, audience, name, sql=None):
    import duckdb
    con = duckdb.connect(":memory:")
    try:
        kpis.load_views(con, gold, audience)
        kpis.load_prices(con, PRICES)
        cursor = con.execute(sql or kpis.read_queries()[name]["sql"])
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in cursor.fetchall()]
    finally:
        con.close()


# ---- one reference unallocated share -----------------------------------------------------------------------------


def _sections(text):
    parts = re.split(r"^### ", text, flags=re.M)[1:]
    return {p.split("\n", 1)[0]: p for p in parts}


def test_done_check_E4_the_cookbook_names_exactly_one_reference_unallocated_share():
    titles = {name: q["title"] for name, q in kpis.read_queries().items()}
    named = [n for n, t in titles.items() if PHRASE in t.lower()]
    assert named == ["one_unallocated_share"]  # the query keeps its name; only the title says "reference"
    sections = _sections(DOC.read_text(encoding="utf-8"))
    headings = [h for h in sections if PHRASE in h.lower()]
    assert headings == [titles["one_unallocated_share"]]
    mine = sections[headings[0]]
    assert re.search(r"the one to use and report", mine)  # the question or text says it is the one to use
    # no other KPI claims to be it: they may mention it, but only to say they are not
    for heading, body in sections.items():
        if heading != headings[0] and PHRASE in body.lower():
            assert re.search(r"not the %s" % PHRASE, body.lower()), heading


def test_the_reconciliation_kpi_says_it_is_not_the_reference_share():
    sections = _sections(DOC.read_text(encoding="utf-8"))
    body = sections[kpis.read_queries()["unreconciled_share"]["title"]]
    assert "not the " + PHRASE in body.lower() and ANCHOR in body


def _described(dataset, column):
    from meterline_gold import dataset_docs
    return dataset_docs.OVERRIDES.get((dataset, column)) or dataset_docs.COLUMNS[column]


@pytest.mark.parametrize("dataset,column", [("reconciliation_month", "unreconciled_share"), ("data_quality", "metric")])
def test_done_check_E4_the_two_descriptions_say_they_are_not_the_reference_share_and_link_it(dataset, column):
    text = _described(dataset, column).lower()
    assert "not the " + PHRASE in text and "one_unallocated_share" in text, (dataset, column)
    # the reference page and the schema carry it, with the link to the cookbook KPI
    page = (ROOT / "docs" / "reference" / "gold-datasets.md").read_text(encoding="utf-8")
    section = page.split("## `%s`" % dataset, 1)[1].split("\n## ", 1)[0]
    row = next(l for l in section.splitlines() if l.startswith("| `%s` |" % column))
    assert "not the " + PHRASE in row.lower() and LINK in row, row
    schema = json.loads((ROOT / "docs" / "schemas" / ("gold-dataset-%s.schema.json" % dataset)).read_text(encoding="utf-8"))
    assert "not the " + PHRASE in schema["properties"][column]["description"].lower()


def test_the_cookbook_anchor_exists():
    # negative control for the link above: the anchor is the GitHub slug of a heading of the cookbook
    headings = re.findall(r"^### (.+)$", DOC.read_text(encoding="utf-8"), flags=re.M)
    slugs = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in headings}
    assert ANCHOR[1:] in slugs


# ---- the idle seat cost -------------------------------------------------------------------------------------------


def test_idle_seat_cost_is_the_last_but_one_kpi_of_the_cookbook_order():
    order = list(kpis.read_queries())
    assert "idle_seat_cost" in order and order.index("idle_seat_cost") == order.index("seat_utilisation") + 1
    q = kpis.read_queries()["idle_seat_cost"]
    assert q["title"] == "Idle seat cost" and q["dataset"] == "seats_month"


def _hand_idle_cost(gold, audience):
    """idle_seats times seat_price from the CSV, only for rows whose seats come from assignments and have both."""
    expected = {}
    with open(Path(gold) / audience / "seats_month.csv", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["seat_source"] != "assignments":
                continue
            cost = None
            if row["idle_seats"] != "" and row["seat_price"] != "":
                cost = Decimal(row["idle_seats"]) * Decimal(row["seat_price"])
            expected[(row["month"], row["provider"], row["provider_account_id"])] = cost
    return expected


@pytest.mark.parametrize("audience", ["exec", "central"])
@pytest.mark.parametrize("which", ["tiny", "vertical"])
def test_done_check_E4_idle_seat_cost_equals_the_seats_month_arithmetic(tiny, vertical, which, audience):
    gold = tiny if which == "tiny" else vertical
    expected = _hand_idle_cost(gold, audience)
    rows = _rows(gold, audience, "idle_seat_cost")
    got = {(r["month"], r["provider"], r["provider_account_id"]): r["idle_seat_cost"] for r in rows}
    assert expected and set(got) == set(expected)  # one row per assignment-fed seat row; no identities row
    for key, cost in expected.items():
        assert got[key] == (None if cost is None else cost.quantize(Decimal("0.000000000001"))), key
    assert {r["provider"] for r in rows} == {"github_copilot"}  # the anthropic seats come from identities
    if (which, audience) == ("vertical", "exec"):
        assert any(v is not None for v in got.values())


def test_a_withheld_idle_count_gives_no_cost_on_tiny_exec(tiny):
    rows = _rows(tiny, "exec", "idle_seat_cost")
    assert rows and all(r["idle_seat_cost"] is None and r["withheld"] for r in rows)  # not zero


def test_a_wrong_expected_cost_is_caught(vertical):
    # negative control: one cent more on every cost differs from the query
    expected = {k: v + Decimal("0.01") for k, v in _hand_idle_cost(vertical, "central").items() if v is not None}
    rows = _rows(vertical, "central", "idle_seat_cost")
    got = {(r["month"], r["provider"], r["provider_account_id"]): r["idle_seat_cost"] for r in rows}
    assert expected and any(got[k] != v.quantize(Decimal("0.000000000001")) for k, v in expected.items())


@pytest.fixture(scope="module")
def admin_gold(tmp_path_factory):
    """Gold built from the Admin connector's fake, with the example price list (a provider-wide anthropic seat line)
    and the simulator's organisation files, whose identities name the anthropic provider: examples/03-daily-job's way."""
    admin = pytest.importorskip("meterline_anthropic_admin")
    fake = pytest.importorskip("meterline_anthropic_admin.testing")
    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("e4a_sim"))
    data_dir = tmp_path_factory.mktemp("e4a_data") / "admin"
    with pytest.MonkeyPatch.context() as env:
        env.setenv("ANTHROPIC_ADMIN_KEY", fake.SAMPLE_KEY)
        result = admin.run("backfill", data_dir=data_dir, transport=fake.FakeAPI().transport,
                           now=lambda: datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc), since=date(2026, 9, 1),
                           provider_account_id="my-org")
    assert result.ok, result
    out = tmp_path_factory.mktemp("e4a_gold") / "gold"
    meterline_gold.build(silver=data_dir, org=Path(run) / "org", policy=BASE / "policy.example.yaml", prices=PRICES,
                         key=KEY, out=out)
    return out


def test_done_check_E4_an_admin_fed_build_has_an_identities_seat_cost_and_no_idle_seat_cost_row(admin_gold):
    with open(admin_gold / "central" / "seats_month.csv", encoding="utf-8", newline="") as handle:
        seats = [r for r in csv.DictReader(handle) if r["provider"] == "anthropic"]
    assert seats and all(r["seat_source"] == "identities" and Decimal(r["seat_cost"]) > 0 for r in seats)
    for audience in ("exec", "central"):
        assert [r for r in _rows(admin_gold, audience, "idle_seat_cost") if r["provider"] == "anthropic"] == []


def test_the_assignments_filter_is_what_keeps_the_identities_row_out(admin_gold):
    # negative control: the same query without the filter returns the anthropic row, with a cost from the
    # provider-wide price
    sql = kpis.read_queries()["idle_seat_cost"]["sql"]
    assert "seat_source = 'assignments'" in sql
    unfiltered = sql.replace("WHERE seat_source = 'assignments'", "WHERE true").replace("AND seat_source = 'assignments'", "")
    assert unfiltered != sql
    rows = [r for r in _rows(admin_gold, "central", None, unfiltered) if r["provider"] == "anthropic"]
    assert rows and any(r["idle_seat_cost"] for r in rows)


def test_the_cookbook_section_of_the_idle_seat_cost_says_the_rules():
    section = _sections(DOC.read_text(encoding="utf-8"))["Idle seat cost"]
    for label in ("**Question.**", "**Folder and dataset.**", "**Do not add.**", "**Hidden cells.**"):
        assert label in section, label
    for word in ("assignments", "identities", "seat_price", "NULL", "idle_seats"):
        assert word in section, word
    query = readme_block.block_after(DOC.read_text(encoding="utf-8"), "Idle seat cost", "sql").strip()
    assert query == kpis.read_queries()["idle_seat_cost"]["sql"]


# ---- the loaders an app imports ---------------------------------------------------------------------------------


def test_done_check_E4_an_app_imports_the_loaders_and_runs_a_query(vertical):
    # an app-style use: import by path, open an audience folder, read a query; nothing is run on import
    module = _load_kpis()
    for name in ("read_queries", "schema_for", "load_views", "load_prices", "connect"):
        func = getattr(module, name)
        assert callable(func) and (func.__doc__ or "").strip(), name
    con = module.connect(vertical, "exec", prices=PRICES)
    try:
        assert {"seats_month", "costs_month", "groups_month"} <= {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        assert con.execute("SELECT count(*) FROM token_prices").fetchone()[0] > 0
        rows = con.execute(module.read_queries()["idle_seat_cost"]["sql"]).fetchall()
    finally:
        con.close()
    assert rows and all(r[1] == "github_copilot" for r in rows)


def test_connect_opens_nothing_the_audience_may_not_read(vertical):
    con = kpis.connect(vertical, "exec", prices=PRICES)
    try:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    finally:
        con.close()
    assert "directory" not in tables and "person_day" not in tables  # delivery/ and central/ files


def test_the_example_readme_tells_an_app_how_to_import_the_loaders():
    text = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^## .*(import|library|app)", text, flags=re.M | re.I)
    for word in ("connect", "load_views", "load_prices", "importlib"):
        assert word in text, word


# ---- the role-to-audience table -----------------------------------------------------------------------------------

ROLES = [("central team", "`central/`"), ("executive", "`exec/`"), ("manager", "`manager/<key>/`"),
         ("individual", "`person/<key>/`")]


@pytest.mark.parametrize("path", ["packages/meterline-gold/README.md", "AGENTS.md"])
def test_done_check_E4_the_role_table_has_the_four_roles_and_their_folders(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    header = next((l for l in text.splitlines() if l.startswith("|") and "Role" in l and "Never receives" in l), None)
    assert header, path
    rows = [l for l in text.splitlines() if l.startswith("|")]
    for role, folder in ROLES:
        row = next((l for l in rows if role in l.lower().split("|")[1] and folder in l.split("|")[2]), None)
        assert row and l_has_never(row), (path, role)


def l_has_never(row):
    cells = row.split("|")
    return len(cells) >= 5 and len(cells[3].strip()) > 20  # a third cell that says what the role never receives
