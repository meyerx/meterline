"""Load the published gold folder into DuckDB, one database file per audience, on a laptop.

    python examples/06-warehouse-duckdb/load_warehouse.py GOLD_ROOT --out WAREHOUSE_DIR [--connections FILE] [--check]

GOLD_ROOT is the job's gold root; the loader reads ``GOLD_ROOT/published`` only (the folder the ``published`` link points at),
never ``builds/``, ``frozen/``, silver, ``records/`` or ``delivery/``. DuckDB has no roles, so one database file per audience
stands in for one schema per audience in a real warehouse: ``db_central.duckdb``, ``db_exec.duckdb`` and one ``db_manager_<key>.duckdb``
per manager, each holding a schema of its own audience with that audience's tables and nothing else. With ``--connections`` the
loader also writes ``db_restricted.duckdb``, which holds the table mapping a warehouse connection to an audience and a key; the file
has three columns, ``connection``, ``audience`` and ``key``, and never a name or an email column. Person folders are not loaded
here (example 07 reads them in place). Every load writes each file afresh and removes the files of an earlier build that this
build does not write, so no table, checkpoint or copy of an earlier build stays. A real warehouse needs the same rules as grants;
the Snowflake excerpt on the scenario C page shows them.

Exit codes: 0 done, 1 ``--check`` found a file that holds more than its audience's schema, 2 the input is wrong (``published/``
is absent or holds silver, ``records/`` or ``delivery/``, a link leaves its audience's folder, the output sits inside the gold
root, or the connections file has another header or a value it must not hold). Standard library and ``duckdb`` only.
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path

import duckdb

REFUSED_NAMES = {"silver", "records", "delivery"}
KEY = re.compile(r"^[A-Za-z0-9_-]+$")
TABLE = re.compile(r"^[a-z][a-z0-9_]*$")
CONNECTION_COLUMNS = ["connection", "audience", "key"]
CONNECTION_AUDIENCES = {"exec", "manager", "person"}
OWN_FILES = re.compile(r"^db_(central|exec|restricted|manager_[A-Za-z0-9_-]+)\.duckdb(\.wal|\.new)?$")
PRIVATE_UMASK = 0o077  # files 0600, folders 0700


class InputError(Exception):
    """The input is wrong: exit 2, the message says what to look at."""


def refused_part(path):
    """The name of silver, records or delivery in a path or in where it really leads, or None."""
    for candidate in (Path(path), Path(path).resolve()):
        for part in candidate.parts:
            if part in REFUSED_NAMES:
                return part
    return None


def inside(path, base):
    return path == base or base in path.parents


def published_folder(gold_root):
    """The real folder that ``GOLD_ROOT/published`` points at; InputError when it is absent or holds what must stay out."""
    part = refused_part(gold_root)
    if part:
        raise InputError("%s: the path names %s/, which this loader never reads" % (gold_root, part))
    link = Path(gold_root) / "published"
    if not link.is_dir():
        raise InputError("%s: published/ is absent" % gold_root)
    real = link.resolve()
    for name in sorted(REFUSED_NAMES):
        if (real / name).exists():
            raise InputError("%s: the published folder holds %s/, which this loader never reads" % (real.name, name))
    return real


def audience_folder_tables(folder):
    """[(table name, file)] of one audience folder: CSV and Parquet files, Parquet preferred; InputError on a link or odd name."""
    folder = Path(folder)
    found = {}
    for entry in sorted(folder.iterdir()):
        if entry.suffix not in (".csv", ".parquet") or entry.is_dir():
            continue
        if entry.is_symlink() or not inside(entry.resolve(), folder.resolve()):
            raise InputError("%s: %s is a link or leaves its folder" % (folder.name, entry.name))
        if not TABLE.match(entry.stem):
            raise InputError("%s: %s is not a table name this loader accepts" % (folder.name, entry.name))
        if entry.stem not in found or entry.suffix == ".parquet":
            found[entry.stem] = entry
    return sorted(found.items())


def audience_tables(published, audience):
    """[(table name, file)] of an audience: ``("central",)``, ``("exec",)`` or ``("manager", key)``."""
    published = Path(published)
    kind = audience[0]
    folder = published / kind if len(audience) == 1 else published / kind / audience[1]
    if not folder.is_dir():
        raise InputError("%s: no folder for the audience %s" % (published.name, "/".join(audience)))
    return audience_folder_tables(folder)


def schema_of(audience):
    return "_".join(audience)


def audiences_of(published):
    published = Path(published)
    found = [("central",), ("exec",)]
    manager = published / "manager"
    if manager.is_dir():
        for entry in sorted(manager.iterdir()):
            if not entry.is_dir() or not KEY.match(entry.name):
                raise InputError("manager/%s is not a folder with a plain key as its name" % entry.name)
            found.append(("manager", entry.name))
    return found


def file_of(audience):
    return "db_%s.duckdb" % schema_of(audience)


def write_database(target, schema, tables, statements=()):
    """Write ``target`` afresh: a new file next to it, one schema, the tables, a checkpoint, then it replaces the old file."""
    target = Path(target)
    fresh = target.with_name(target.name + ".new")
    for leftover in (fresh, fresh.with_name(fresh.name + ".wal")):
        leftover.unlink(missing_ok=True)
    con = duckdb.connect(str(fresh))
    try:
        con.execute('CREATE SCHEMA "%s"' % schema)
        counts = {}
        for name, path in tables:
            source = "read_parquet(?)" if path.suffix == ".parquet" else "read_csv(?, header = true, sample_size = -1)"
            con.execute('CREATE TABLE "%s"."%s" AS SELECT * FROM %s' % (schema, name, source), [str(path)])
            counts[name] = con.execute('SELECT count(*) FROM "%s"."%s"' % (schema, name)).fetchone()[0]
        for statement, params in statements:
            con.execute(statement, params)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    fresh.with_name(fresh.name + ".wal").unlink(missing_ok=True)
    os.replace(fresh, target)
    os.chmod(target, 0o600)
    return counts


def read_connections(path, published):
    """The rows of the connections file; InputError when the header is not exactly the three columns, a value is not allowed or a key is unknown."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != CONNECTION_COLUMNS:
            raise InputError("%s: the header must be exactly %s (no name or email column), not %s" % (Path(path).name, ",".join(CONNECTION_COLUMNS), header))
        rows = []
        for number, row in enumerate(reader, start=2):
            if len(row) != 3:
                raise InputError("%s line %d: expected 3 values" % (Path(path).name, number))
            connection, audience, key = (c.strip() for c in row)
            if not connection or "@" in connection:
                raise InputError("%s line %d: the connection must be a plain id and not an email address" % (Path(path).name, number))
            if audience not in CONNECTION_AUDIENCES:
                raise InputError("%s line %d: the audience must be one of %s" % (Path(path).name, number, ", ".join(sorted(CONNECTION_AUDIENCES))))
            if audience == "exec" and key:
                raise InputError("%s line %d: an exec connection has no key" % (Path(path).name, number))
            if audience != "exec" and not (key and KEY.match(key) and (Path(published) / audience / key).is_dir()):
                raise InputError("%s line %d: no %s folder of that key in the published folder" % (Path(path).name, number, audience))
            rows.append((connection, audience, key))
    return rows


def remove_stale_files(out_dir, written):
    """Remove the files of an earlier build that this build did not write (only files with this loader's own names)."""
    for entry in sorted(Path(out_dir).iterdir()):
        if OWN_FILES.match(entry.name) and entry.name not in written:
            entry.unlink()


def check_files(out_dir, expected):
    """Open every written file read-only: it holds the schema of its audience and no other, and the schema of another manager is not there."""
    problems = []
    managers = [s for s in expected.values() if s.startswith("manager_")]
    for name, schema in sorted(expected.items()):
        path = Path(out_dir) / name
        if not path.is_file():
            problems.append("%s is missing" % name)
            continue
        con = duckdb.connect(str(path), read_only=True)
        try:
            schemas = sorted(r[0] for r in con.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('main', 'information_schema', 'pg_catalog')").fetchall())
            if schemas != [schema]:
                problems.append("%s holds the schemas %s, not only %s" % (name, schemas, schema))
            for other in managers:
                if other != schema and con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = ?", [other]).fetchone()[0]:
                    problems.append("%s holds tables of %s" % (name, other))
        finally:
            con.close()
    return problems


def run(args):
    published = published_folder(args.gold_root)
    out_dir = Path(args.out).resolve()
    if inside(out_dir, Path(args.gold_root).resolve()):
        raise InputError("%s: the output sits inside the gold root; write it elsewhere so that nothing under published/ or builds/ holds it" % args.out)
    audiences = audiences_of(published)
    plan = [(a, audience_tables(published, a)) for a in audiences]  # all inputs read and checked before a file is written
    connections = read_connections(args.connections, published) if args.connections else None
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = {}
    for audience, tables in plan:
        schema = schema_of(audience)
        write_database(out_dir / file_of(audience), schema, tables)
        expected[file_of(audience)] = schema
    if connections is not None:
        statements = [('CREATE TABLE restricted.connection_keys (connection VARCHAR, audience VARCHAR, "key" VARCHAR)', [])]
        statements += [("INSERT INTO restricted.connection_keys VALUES (?, ?, ?)", list(row)) for row in connections]
        write_database(out_dir / "db_restricted.duckdb", "restricted", [], statements)
        expected["db_restricted.duckdb"] = "restricted"
    remove_stale_files(out_dir, set(expected))
    print("wrote %d files into %s: central, exec, %d manager%s%s" % (
        len(expected), out_dir.name, len(audiences) - 2, "" if len(audiences) == 3 else "s", ", restricted" if connections is not None else ""))
    return expected


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gold_root")
    ap.add_argument("--out", required=True, help="the folder that receives the database files")
    ap.add_argument("--connections", help="a CSV file with the columns connection, audience, key; written to db_restricted.duckdb")
    ap.add_argument("--check", action="store_true", help="after the load, open each file read-only and check that it holds its own schema only")
    ap.add_argument("--check-only", action="store_true", help="the check alone, on the files already in --out")
    args = ap.parse_args(argv)
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        if args.check_only:
            published = published_folder(args.gold_root)
            expected = {file_of(a): schema_of(a) for a in audiences_of(published)}
            if args.connections or (Path(args.out) / "db_restricted.duckdb").exists():
                expected["db_restricted.duckdb"] = "restricted"
        else:
            expected = run(args)
        if args.check or args.check_only:
            problems = check_files(Path(args.out), expected)
            if problems:
                for problem in problems:
                    print("problem: " + problem, file=sys.stderr)
                return 1
            print("ok: %d files, each holding its own schema only" % len(expected))
    except InputError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    finally:
        os.umask(old_umask)
    return 0


if __name__ == "__main__":
    sys.exit(main())
