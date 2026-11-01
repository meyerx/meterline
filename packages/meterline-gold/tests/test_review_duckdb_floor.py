"""Change-list finding 38: gold needs duckdb 1.5.3 or later.

Under duckdb 1.5.0, 1.5.1 and 1.5.2 a multi-threaded build on Linux sometimes (about 1 in 150 runs of the
person_day SQL) creates ``price_day`` empty or short, so token-priced rows of that build are reported as
unpriced with a reference cost of 0.00. The tables loaded before it are identical; the same query run
again on the same connection gives the 15 expected rows. No such result appeared in 3,000 runs on each
of 1.5.3 to 1.5.6, nor on 1.5.0 with join_filter_pushdown disabled: duckdb issue #22075, fixed in 1.5.3
by duckdb pull request #22218. The minimum-versions CI job installs the declared lower bound, so the
bound must exclude the affected releases; this test keeps it there.
"""

import re
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _version(text):
    return tuple(int(part) for part in text.split("."))


def test_duckdb_lower_bound_excludes_the_releases_that_lose_prices():
    match = re.search(r'"duckdb>=([0-9.]+),<2"', PYPROJECT.read_text(encoding="utf-8"))
    assert match, "gold declares duckdb as duckdb>=X,<2"
    assert _version(match.group(1)) >= (1, 5, 3), "duckdb %s can create price_day empty" % match.group(1)
