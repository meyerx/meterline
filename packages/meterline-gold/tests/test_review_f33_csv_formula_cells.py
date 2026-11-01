"""F-33: gold writes text from the organisation files as it is, a leading = + - @ included, and says so.

Gold's CSV files are data for programs (the dashboard, a notebook, a warehouse load). Rewriting a value that
starts with a formula character would change the data those readers get, so gold does not do it; instead the
README tells a reader to import the files as text rather than open them in a spreadsheet. This test pins both
halves: the value comes out unchanged, and the warning is there.
"""

import csv
import shutil
from pathlib import Path

import meterline_gold

from gold_helpers import KEY, REPO, fixture_inputs, read_dicts

FORMULA_NAME = "=1+1"
FORMULA_GIVEN = "@SUM(1)"


def _rewrite(path, column, key_column, key, value):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fields = list(rows[0].keys())
    for row in rows:
        if row[key_column] == key:
            row[column] = value
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_csv_formula_cells_are_written_as_they_are_and_the_readme_warns(tmp_path):
    inp = fixture_inputs()
    org = tmp_path / "org"
    shutil.copytree(inp["org"], org)
    _rewrite(org / "units.csv", "name", "unit_id", "u02", FORMULA_NAME)
    _rewrite(org / "directory.csv", "given_name", "person_id", "p02", FORMULA_GIVEN)
    out = tmp_path / "gold"
    meterline_gold.build(silver=inp["silver"], org=org, policy=inp["policy"], prices=inp["prices"],
                         invoice=inp["invoice"], key=KEY, out=out)
    assert FORMULA_NAME in {r["name"] for r in read_dicts(out / "central" / "units.csv")}
    assert FORMULA_GIVEN in {r["given_name"] for r in read_dicts(out / "delivery" / "directory.csv")}

    readme = (REPO / "packages" / "meterline-gold" / "README.md").read_text(encoding="utf-8")
    assert "formula" in readme and "import" in readme.lower() and "as text" in readme
