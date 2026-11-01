"""F-33: the one SQL statement gold builds from text (``load_table``, flagged by bandit as B608) only ever
receives table and column names and types from the code, never from data.

``load_table`` refuses anything that is not a plain lower-case identifier or one of the column types the
loader uses, before DuckDB sees it. Values (the CSV path and the null marker) stay bound parameters.
"""

import duckdb
import pytest

from meterline_gold.errors import BuildFailure
from meterline_gold.load import load_table

HOSTILE = [
    ("t; DROP TABLE x", [("a", "VARCHAR")]),
    ("t", [("a VARCHAR, b", "VARCHAR")]),
    ("t", [("a", "VARCHAR); CREATE TABLE pwned (x INT")]),
    ("t", [("a", "VARCHAR"), ("b'", "VARCHAR")]),
    ("T", [("a", "VARCHAR")]),
    ("t", [("a", "TEXT")]),
]


@pytest.mark.parametrize("name,columns", HOSTILE)
@pytest.mark.parametrize("rows", [[], [("1",)]], ids=["empty", "one row"])
def test_sql_identifiers_load_table_refuses_what_is_not_a_code_identifier(name, columns, rows, tmp_path):
    con = duckdb.connect()
    with pytest.raises(BuildFailure):
        load_table(con, name, columns, [r * len(columns) for r in rows], str(tmp_path))
    assert con.execute("SELECT count(*) FROM information_schema.tables").fetchone()[0] == 0


def test_sql_identifiers_load_table_accepts_the_types_the_loader_uses(tmp_path):
    con = duckdb.connect()
    columns = [("a_1", "VARCHAR"), ("b", "BIGINT"), ("c", "DATE"), ("d", "BOOLEAN"), ("e", "DECIMAL(38,12)")]
    load_table(con, "policy_t", columns, [("x", 1, "2026-05-01", True, "1.5")], str(tmp_path))
    assert con.execute("SELECT a_1, b, e FROM policy_t").fetchone() == ("x", 1, __import__("decimal").Decimal("1.5"))
