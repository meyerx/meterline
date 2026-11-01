"""Runs the SQL files in name order over the loaded tables, then the assertion queries (spec 2, 9.5).

The SQL files are the engine: this module loads, executes, loops and fetches. It computes no dataset figure.
A statement marked ``-- dataset: <name>`` is a dataset query (fetched, never executed as setup); a statement marked
``-- assertion: <name>`` is an assertion query; a statement marked ``-- step: <name>`` belongs to the privacy loop
(``run_privacy``) and never runs as setup.
"""

import re
import tempfile
from pathlib import Path

import duckdb
import yaml  # noqa: F401  (parameter files are read by params.py)

from meterline_gold import load, params
from meterline_gold.errors import InputError

SQL_DIR = Path(__file__).resolve().parent / "sql"
_MARK = re.compile(r"^\s*--\s*(dataset|assertion|step):\s*(\S+)\s*$", re.M)
# the setup files that run before the privacy loop sort before this name, the ones after it run after the loop
_LOOP_AFTER = "70_privacy_step.sql"


def split_statements(text):
    """Statements of an SQL file split on ';' outside strings and comments (comments stay with their statement)."""
    out, buf = [], []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            buf.append(text[i:j])
            i = j
        elif text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(text[i:j])
            i = j
        elif text[i] == ";":
            out.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(text[i])
            i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def _has_sql(statement):
    return any(line.strip() and not line.strip().startswith("--") for line in statement.splitlines())


def _scan_sql_files():
    setup, datasets, assertions, steps = [], {}, [], {}
    for path in sorted(SQL_DIR.glob("*.sql")):
        for stmt in split_statements(path.read_text(encoding="utf-8")):
            if not _has_sql(stmt):
                continue
            mark = _MARK.search(stmt)
            if mark is None:
                setup.append((path.name, stmt))
            elif mark.group(1) == "dataset":
                datasets[mark.group(2)] = stmt
            elif mark.group(1) == "step":
                steps.setdefault(mark.group(2), []).append(stmt)
            else:
                assertions.append((mark.group(2), stmt))
    return setup, datasets, assertions, steps


_SETUP, _DATASETS, ASSERTIONS, _STEPS = _scan_sql_files()


def _table_exists(con, name):
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()[0] > 0


def _suppressed_count(con):
    return con.execute("SELECT count(*) FROM cell_state WHERE suppressed").fetchone()[0]


def run_privacy(con, k, *, _disable_block_rule=False):
    """Spec 7.4 on the table ``cells(month, cell_id, parent_cell_id, persons_list, receivers_list)``.

    Leaves ``cell_state(month, cell_id, suppressed, reason)`` and the working tables ``cell_members``,
    ``cell_recv``, ``cell_tree``, ``cell_size``, ``cell_closure`` and ``cell_direct`` in the connection, and
    returns ``{(month, cell_id): reason}`` for every suppressed cell. When the connection holds
    ``person_flags(month, person_key, active)`` it also decides the ``active_persons`` cells of 7.2 into
    ``cell_active_published(month, cell_id)``.
    """
    con.execute(
        "CREATE OR REPLACE TABLE privacy_params AS SELECT CAST(? AS BIGINT) AS k, CAST(? AS BOOLEAN) AS block_rule",
        [int(k), not _disable_block_rule],
    )
    for statement in _STEPS["privacy_init"]:
        con.execute(statement)
    while True:
        before = _suppressed_count(con)
        for statement in _STEPS["privacy"]:
            con.execute(statement)
        if _suppressed_count(con) == before:
            break
    if _table_exists(con, "person_flags"):
        for statement in _STEPS["active_init"]:
            con.execute(statement)
        while True:
            for statement in _STEPS["active"]:
                con.execute(statement)
            same = con.execute(
                "SELECT (SELECT count(*) FROM (SELECT month, cell_id FROM active_next EXCEPT "
                "SELECT month, cell_id FROM cell_active_published)) + (SELECT count(*) FROM "
                "(SELECT month, cell_id FROM cell_active_published EXCEPT SELECT month, cell_id FROM active_next))"
            ).fetchone()[0] == 0
            con.execute("CREATE OR REPLACE TABLE cell_active_published AS SELECT month, cell_id FROM active_next")
            if same:
                break
    rows = con.execute(
        "SELECT month, cell_id, reason FROM cell_state WHERE suppressed ORDER BY month, cell_id"
    ).fetchall()
    return {(m, c): r for m, c, r in rows}


def _check_unit_cycles(con):
    """7.1 (v1.1): a parent cycle on someone's unit path is an input error naming units.csv."""
    if not _table_exists(con, "unit_cycles"):
        return
    row = con.execute("SELECT month, unit_id FROM unit_cycles ORDER BY month, unit_id LIMIT 1").fetchone()
    if row is not None:
        raise InputError(
            "units.csv: unit %s is its own ancestor in the unit tree of %s (a parent cycle)" % (row[1], row[0])
        )


class Context:
    """Everything a build holds after stage 0 and the SQL setup files."""

    def __init__(self):
        self.con = None
        self.policy = None
        self.prices = None
        self.hasher = None
        self.org = None
        self.silver = None
        self.org_dir = None
        self.silver_dir = None
        self.policy_path = None
        self.prices_path = None
        self.invoice_path = None


def _no_silver(silver_dir):
    return InputError(
        "%s: no silver records found under %s; --silver is the folder above records/"
        % (silver_dir, silver_dir / "records")
    )


def prepare_context(silver, org, policy, prices, invoice=None, key=None, threads=None):
    """Validate every input, load it into DuckDB and run the setup SQL. Raises InputError or BuildFailure."""
    hasher = load.Hasher(key if key is not None else load.key_from_environment())
    ctx = Context()
    ctx.hasher = hasher
    ctx.policy_path, ctx.prices_path = Path(policy), Path(prices)
    ctx.invoice_path = None if invoice is None else Path(invoice)
    ctx.org_dir, ctx.silver_dir = Path(org), Path(silver)
    ctx.policy = params.load_policy(ctx.policy_path)
    ctx.prices = params.load_prices(ctx.prices_path)
    if ctx.policy.currency != ctx.prices.currency:
        raise InputError(
            "currency differs: the policy has %s, the price list has %s" % (ctx.policy.currency, ctx.prices.currency)
        )
    invoice_rows = [] if ctx.invoice_path is None else load.read_invoice(ctx.invoice_path, ctx.policy.currency)
    if not ctx.org_dir.is_dir():
        raise InputError("%s: not a folder" % ctx.org_dir)
    if not ctx.silver_dir.is_dir():
        raise InputError("%s: not a folder" % ctx.silver_dir)
    ctx.org = load.read_org(ctx.org_dir, hasher, ctx.policy.dimensions[0], set(ctx.policy.people.attrs_keep))
    ctx.silver = load.read_silver(ctx.silver_dir)
    if not ctx.silver.files:
        raise _no_silver(ctx.silver_dir)
    load.check_currencies(ctx.silver, ctx.policy.currency)

    con = duckdb.connect(":memory:")
    if threads is not None:
        con.execute("SET threads TO %d" % int(threads))
    with tempfile.TemporaryDirectory() as tmp:
        load.load_all(con, ctx.policy, ctx.prices, ctx.org, ctx.silver, invoice_rows, hasher, tmp)
    privacy_done = False
    for name, statement in _SETUP:
        if not privacy_done and name > _LOOP_AFTER:
            _check_unit_cycles(con)
            run_privacy(con, ctx.policy.privacy.k)
            privacy_done = True
        con.execute(statement)
    if not privacy_done:
        _check_unit_cycles(con)
        run_privacy(con, ctx.policy.privacy.k)
    if con.execute("SELECT count(*) FROM months").fetchone()[0] == 0:
        raise _no_silver(ctx.silver_dir)
    ctx.con = con
    return ctx


def prepare(silver, org, policy, prices, invoice=None, key=None, threads=None):
    """The DuckDB connection of a prepared build, with every dataset table in place and no assertion run."""
    return prepare_context(silver, org, policy, prices, invoice=invoice, key=key, threads=threads).con


def failures(con):
    """(name, first offending row) of every assertion of ``ASSERTIONS`` that does not hold."""
    out = []
    for name, sql in ASSERTIONS:
        row = con.execute(sql).fetchone()
        if row is not None:
            out.append((name, row))
    return out


def fetch(con, name):
    """(column names, rows) of the dataset query marked ``-- dataset: <name>``."""
    cursor = con.execute(_DATASETS[name])
    return [d[0] for d in cursor.description], cursor.fetchall()
