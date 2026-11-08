"""R-2 of the integrate part 2 spec: example 06 loads the published gold folder into DuckDB, one database file per audience.

DuckDB has no roles, so one file per audience stands in for one schema per audience. The checks below build a gold folder with
managers, give manager B's rows a sentinel value, load it, and then look for the sentinel everywhere manager A can reach: the
tables of A's file, a query for B's schema (a catalog error), every value A's queries return, and the bytes of A's file. They also
check that a second load leaves no row of the first build in any file, that the mapping table exists in the restricted file only
and has no name or email column, that the exec file holds no person key, and that the loader refuses silver, ``records/`` and
``delivery/`` (exit 2). The negative controls plant a defect and the same check must catch it: B's rows loaded into A's file, and
an earlier build's file left in place.

Run: python3 -m pytest -q tools/test_example_06_warehouse.py
"""
import csv
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
meterline_gold = pytest.importorskip("meterline_gold")
meterline_sim = pytest.importorskip("meterline_sim")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "06-warehouse-duckdb"
sys.path.insert(0, str(EXAMPLE_DIR))
import load_warehouse as lw  # noqa: E402

KEY = "a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
SENTINEL_B = "SENTINEL-OF-MANAGER-B-7f3a91c2"
SENTINEL_A = "SENTINEL-OF-MANAGER-A-51d0e8b4"
ONLY_IN_BUILD_1 = "ONLY-IN-BUILD-ONE-c40b7e19"
ONLY_IN_BUILD_2 = "ONLY-IN-BUILD-TWO-9be2a6d5"


# ---- a gold folder with a sentinel in each manager's rows ----

def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def plant(path, column, value):
    """Append one row to a gold CSV file with ``value`` in ``column`` and nothing else filled in."""
    header, rows = read_rows(path)
    row = [""] * len(header)
    row[header.index(column)] = value
    with open(path, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerow(row)


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    """A gold root whose ``published`` link points at a built folder; manager A and B each carry a sentinel in their rows."""
    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("w_sim"))
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    root = tmp_path_factory.mktemp("w_gold")
    folder = root / "published-1"
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY.encode(), out=folder)
    shutil.rmtree(folder / "delivery")  # the job's publish step leaves no delivery/ in published-*
    a, b = sorted(p.name for p in (folder / "manager").iterdir())[:2]
    plant(folder / "manager" / a / "groups_month.csv", "cell_key", SENTINEL_A)
    plant(folder / "manager" / b / "groups_month.csv", "cell_key", SENTINEL_B)
    plant(folder / "manager" / b / "named_exceptions.csv", "condition", SENTINEL_B)
    (root / "published").symlink_to("published-1")
    return root, a, b


def copy_root(root, tmp_path, name):
    """A copy of a gold root (links kept), so a test can change a build without touching the module's."""
    target = tmp_path / name
    shutil.copytree(root, target, symlinks=True)
    return target


def load(gold_root, out, *extra):
    return lw.main([str(gold_root), "--out", str(out), *extra])


@pytest.fixture(scope="module")
def warehouse(published, tmp_path_factory):
    root, a, b = published
    out = tmp_path_factory.mktemp("w_out") / "warehouse"
    assert load(root, out) == 0
    return out


# ---- what a reader of A's file can reach ----

def connect(path):
    return duckdb.connect(str(path), read_only=True)


def schemas_of(path):
    con = connect(path)
    try:
        return sorted(r[0] for r in con.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('main', 'information_schema', 'pg_catalog')").fetchall())
    finally:
        con.close()


def tables_of(path):
    con = connect(path)
    try:
        return sorted("%s.%s" % r for r in con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema', 'pg_catalog')").fetchall())
    finally:
        con.close()


def every_value(path):
    """Every value a reader of the file can select, as text, from every table."""
    con = connect(path)
    values = set()
    try:
        for table in tables_of(path):
            schema, name = table.split(".", 1)
            cursor = con.execute('SELECT * FROM "%s"."%s"' % (schema, name))
            for row in cursor.fetchall():
                values.update(str(v) for v in row if v is not None)
    finally:
        con.close()
    return values


def isolation_problems(out, own, other, sentinel, own_sentinel=None):
    """What lets the reader of manager ``own``'s file reach manager ``other``'s rows (empty when nothing does)."""
    path = out / ("db_manager_%s.duckdb" % own)
    bad = []
    if schemas_of(path) != ["manager_" + own]:
        bad.append("schemas %s" % schemas_of(path))
    con = connect(path)
    try:
        try:
            con.execute('SELECT * FROM "manager_%s"."groups_month"' % other).fetchall()
            bad.append("B's table can be queried")
        except duckdb.CatalogException:
            pass
    finally:
        con.close()
    if any(sentinel in v for v in every_value(path)):
        bad.append("the sentinel is among the values")
    if sentinel.encode() in path.read_bytes():
        bad.append("the sentinel is in the bytes")
    return bad


def test_manager_a_file_holds_a_schema_of_its_own_and_nothing_of_b(published, warehouse):
    root, a, b = published
    assert isolation_problems(warehouse, a, b, SENTINEL_B) == []
    assert isolation_problems(warehouse, b, a, SENTINEL_A) == []
    # the sentinel of A is there, so the scan is not blind
    assert any(SENTINEL_A in v for v in every_value(warehouse / ("db_manager_%s.duckdb" % a)))
    assert any(SENTINEL_B in v for v in every_value(warehouse / ("db_manager_%s.duckdb" % b)))


def test_every_manager_has_a_file_with_the_tables_of_its_own_folder_only(published, warehouse):
    root, a, b = published
    managers = sorted(p.name for p in (root / "published" / "manager").iterdir())
    assert sorted(p.name for p in warehouse.glob("db_manager_*.duckdb")) == ["db_manager_%s.duckdb" % m for m in managers]
    for manager in managers:
        wanted = sorted({"manager_%s.%s" % (manager, p.stem) for p in (root / "published" / "manager" / manager).glob("*.csv")})
        assert tables_of(warehouse / ("db_manager_%s.duckdb" % manager)) == wanted, manager


def test_negative_control_b_rows_loaded_into_a_file_are_caught(published, tmp_path, monkeypatch):
    root, a, b = published
    real = lw.write_database

    def faulty(target, schema, tables, statements=()):
        counts = real(target, schema, tables, statements)
        if schema == "manager_" + a:  # the defect: B's folder is loaded into A's file too, under B's schema
            b_tables = lw.audience_tables(root / "published-1", ("manager", b))
            con = duckdb.connect(str(target))
            con.execute('CREATE SCHEMA "manager_%s"' % b)
            for name, path in b_tables:
                con.execute('CREATE TABLE "manager_%s"."%s" AS SELECT * FROM read_csv(?, header = true)' % (b, name), [str(path)])
            con.close()
        return counts

    monkeypatch.setattr(lw, "write_database", faulty)
    out = tmp_path / "faulty"
    assert load(root, out) == 0
    problems = isolation_problems(out, a, b, SENTINEL_B)
    assert problems, "the isolation check let B's rows into A's file"
    assert any("B's table can be queried" in p for p in problems) and any("sentinel" in p for p in problems)


# ---- the central, exec and restricted files ----

def test_central_and_exec_files_hold_their_own_folders(published, warehouse):
    root, a, b = published
    for audience in ("central", "exec"):
        wanted = sorted({"%s.%s" % (audience, p.stem) for p in (root / "published" / audience).glob("*.csv")})
        assert tables_of(warehouse / ("db_%s.duckdb" % audience)) == wanted
    assert not any("manager_" in t for t in tables_of(warehouse / "db_central.duckdb"))


def test_the_exec_file_holds_no_person_key(published, warehouse):
    root, a, b = published
    con = connect(warehouse / "db_exec.duckdb")
    try:
        columns = [r[0].lower() for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'exec'").fetchall()]
    finally:
        con.close()
    assert columns and not [c for c in columns if "person_key" in c or "manager_key" in c or "actor_key" in c], columns
    _, people = read_rows(root / "published" / "central" / "people.csv")
    person_keys = {r[read_rows(root / "published" / "central" / "people.csv")[0].index("person_key")] for r in people}
    assert person_keys and not (person_keys & every_value(warehouse / "db_exec.duckdb"))
    blob = (warehouse / "db_exec.duckdb").read_bytes()
    assert not [k for k in person_keys if k.encode() in blob]


def connections_file(path, rows, header=("connection", "audience", "key")):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_the_mapping_table_has_no_name_or_email_column_and_lives_in_the_restricted_file_only(published, tmp_path):
    root, a, b = published
    mapping = connections_file(tmp_path / "connections.csv", [("conn-0001", "exec", ""), ("conn-0002", "manager", a), ("conn-0003", "manager", b)])
    out = tmp_path / "w"
    assert load(root, out, "--connections", str(mapping)) == 0
    assert tables_of(out / "db_restricted.duckdb") == ["restricted.connection_keys"]
    con = connect(out / "db_restricted.duckdb")
    try:
        columns = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'connection_keys'").fetchall()]
        assert columns == ["connection", "audience", "key"]
        assert con.execute('SELECT count(*) FROM restricted.connection_keys').fetchone()[0] == 3
    finally:
        con.close()
    for other in out.glob("*.duckdb"):
        if other.name != "db_restricted.duckdb":
            assert "connection_keys" not in " ".join(tables_of(other)), other.name
            assert b"conn-0002" not in other.read_bytes(), other.name


@pytest.mark.parametrize("header", [("connection", "audience", "key", "email"), ("connection", "audience", "key", "name"),
                                    ("connection", "audience", "key", "full_name"), ("login", "audience", "key")])
def test_a_mapping_file_with_a_name_or_email_column_or_another_header_is_refused(published, tmp_path, header, capsys):
    root, a, b = published
    mapping = connections_file(tmp_path / "connections.csv", [("conn-0002", "manager", a) + ("x",) * (len(header) - 3)], header=header)
    assert load(root, tmp_path / "w", "--connections", str(mapping)) == 2
    assert not (tmp_path / "w" / "db_restricted.duckdb").exists()


def test_a_mapping_that_names_an_unknown_key_or_holds_an_email_address_is_refused(published, tmp_path):
    root, a, b = published
    for rows in ([("conn-1", "manager", "pk_0000000000000000")], [("someone@example.com", "manager", a)], [("conn-1", "central", "")]):
        mapping = connections_file(tmp_path / "connections.csv", rows)
        assert load(root, tmp_path / "w", "--connections", str(mapping)) == 2, rows


# ---- each load replaces the audience's tables ----

def test_a_second_load_keeps_no_row_of_the_first_build_in_any_file(published, tmp_path):
    root, a, b = published
    build1 = copy_root(root, tmp_path, "build1")
    managers = sorted(p.name for p in (build1 / "published" / "manager").iterdir())
    gone = [m for m in managers if m not in (a, b)][0]  # a manager that exists in build 1 only
    plant(build1 / "published" / "manager" / a / "groups_month.csv", "cell_key", ONLY_IN_BUILD_1)
    plant(build1 / "published" / "manager" / gone / "groups_month.csv", "cell_key", ONLY_IN_BUILD_1)
    plant(build1 / "published" / "exec" / "pools_month.csv", "provider", ONLY_IN_BUILD_1)
    plant(build1 / "published" / "central" / "units.csv", "unit_id", ONLY_IN_BUILD_1)
    out = tmp_path / "w"
    assert load(build1, out) == 0
    assert any(ONLY_IN_BUILD_1.encode() in f.read_bytes() for f in out.glob("*.duckdb"))
    assert (out / ("db_manager_%s.duckdb" % gone)).exists()

    build2 = copy_root(root, tmp_path, "build2")
    shutil.rmtree(build2 / "published" / "manager" / gone)
    plant(build2 / "published" / "manager" / a / "groups_month.csv", "cell_key", ONLY_IN_BUILD_2)
    assert load(build2, out) == 0
    problems = left_from_build_1(out, ONLY_IN_BUILD_1, gone)
    assert problems == []
    assert any(ONLY_IN_BUILD_2.encode() in f.read_bytes() for f in out.glob("db_manager_*.duckdb"))


def left_from_build_1(out, marker, gone_manager):
    """Where an earlier build's row, file or table is still there after the next load."""
    bad = []
    for f in sorted(out.iterdir()):
        if marker.encode() in f.read_bytes():
            bad.append("%s holds the marker" % f.name)
        if f.suffix == ".duckdb" and any(marker in v for v in every_value(f)):
            bad.append("%s returns the marker" % f.name)
    if (out / ("db_manager_%s.duckdb" % gone_manager)).exists():
        bad.append("the file of a manager that is gone is still there")
    return bad


def test_negative_control_an_earlier_builds_file_left_in_place_is_caught(published, tmp_path, monkeypatch):
    root, a, b = published
    build1 = copy_root(root, tmp_path, "build1")
    managers = sorted(p.name for p in (build1 / "published" / "manager").iterdir())
    gone = [m for m in managers if m not in (a, b)][0]
    plant(build1 / "published" / "manager" / gone / "groups_month.csv", "cell_key", ONLY_IN_BUILD_1)
    out = tmp_path / "w"
    assert load(build1, out) == 0
    build2 = copy_root(root, tmp_path, "build2")
    shutil.rmtree(build2 / "published" / "manager" / gone)
    monkeypatch.setattr(lw, "remove_stale_files", lambda out_dir, written: None)  # the defect: nothing is cleaned up
    assert load(build2, out) == 0
    assert left_from_build_1(out, ONLY_IN_BUILD_1, gone), "the check missed a file left from the earlier build"


def test_a_load_writes_fresh_files_so_no_checkpoint_or_wal_of_an_earlier_build_stays(published, tmp_path):
    root, a, b = published
    out = tmp_path / "w"
    assert load(root, out) == 0
    first = {f.name: f.stat().st_ino for f in out.iterdir()}
    assert load(root, out) == 0
    names = sorted(f.name for f in out.iterdir())
    assert all(n.endswith(".duckdb") for n in names), names  # no .wal, no .new
    assert names == sorted(first)


# ---- what the loader reads and what it refuses ----

def test_the_loader_refuses_silver_records_and_delivery_with_exit_2(published, tmp_path, capsys):
    root, a, b = published
    silver = tmp_path / "data" / "silver"
    (silver / "records" / "anthropic").mkdir(parents=True)
    (silver / "records" / "anthropic" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    delivery = tmp_path / "gold" / "builds" / "2026-11-05" / "delivery"
    delivery.mkdir(parents=True)
    (delivery / "directory.csv").write_text("person_key,email\n", encoding="utf-8")
    for target in (silver, silver / "records", silver / "records" / "anthropic", delivery, tmp_path / "data", tmp_path / "gold"):
        assert load(target, tmp_path / "w") == 2, target
    assert not (tmp_path / "w").exists() or not list((tmp_path / "w").iterdir())


def test_a_published_folder_that_holds_delivery_silver_or_records_is_refused(published, tmp_path):
    root, a, b = published
    for name in ("delivery", "silver", "records"):
        copy = copy_root(root, tmp_path, "with_" + name)
        (copy / "published-1" / name).mkdir()
        assert load(copy, tmp_path / ("out_" + name)) == 2, name


def test_the_loader_exits_2_when_published_is_absent_or_the_output_is_inside_the_gold_root(published, tmp_path):
    root, a, b = published
    assert load(tmp_path, tmp_path / "w") == 2
    assert load(root, root / "published" / "warehouse") == 2
    assert load(root, root / "warehouse") == 2


def test_a_link_inside_an_audience_folder_that_leaves_it_is_refused(published, tmp_path):
    root, a, b = published
    copy = copy_root(root, tmp_path, "linked")
    link = copy / "published-1" / "manager" / a / "stolen.csv"
    link.symlink_to(os.path.relpath(copy / "published-1" / "manager" / b / "groups_month.csv", link.parent))
    assert load(copy, tmp_path / "w") == 2
    assert not list((tmp_path / "w").glob("*.duckdb")) if (tmp_path / "w").exists() else True


def test_the_files_are_private_to_their_owner(published, warehouse):
    assert stat.S_IMODE(warehouse.stat().st_mode) == 0o700
    for f in warehouse.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


def test_parquet_is_read_when_the_folder_has_it(published, tmp_path):
    root, a, b = published
    copy = copy_root(root, tmp_path, "parquet")
    folder = copy / "published-1" / "exec"
    con = duckdb.connect()
    target = str(folder / "pools_month.parquet").replace("'", "''")
    con.execute("COPY (SELECT * FROM read_csv(?, header = true)) TO '%s' (FORMAT PARQUET)" % target, [str(folder / "pools_month.csv")])
    con.close()
    (folder / "pools_month.csv").write_text("not,a,valid\n", encoding="utf-8")  # if the CSV were read, the table would be wrong
    assert load(copy, tmp_path / "w") == 0
    con = connect(tmp_path / "w" / "db_exec.duckdb")
    try:
        columns = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pools_month'").fetchall()]
    finally:
        con.close()
    assert "seats_billed" in columns


def test_check_passes_on_a_good_load_and_fails_on_a_file_that_holds_another_audiences_schema(published, tmp_path, capsys):
    root, a, b = published
    out = tmp_path / "w"
    assert load(root, out, "--check") == 0
    assert "ok:" in capsys.readouterr().out
    # plant: B's schema copied into A's file
    con = duckdb.connect(str(out / ("db_manager_%s.duckdb" % a)))
    con.execute('CREATE SCHEMA "manager_%s"' % b)
    con.execute('CREATE TABLE "manager_%s".groups_month AS SELECT 1 AS x' % b)
    con.close()
    assert lw.main([str(root), "--out", str(out), "--check-only"]) == 1
