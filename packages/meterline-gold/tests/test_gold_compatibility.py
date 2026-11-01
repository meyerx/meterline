"""A57: the gold output has a written compatibility rule, and a reader that follows it works.

docs/reference/gold-datasets.md, section Compatibility, says what is non-breaking (a new dataset, a new column
appended, a new value in an open list), what is breaking (a column renamed, removed or retyped, a dataset removed or its
grain changed) and what a reader does (select columns by name, ignore an unknown column, fail on a missing one, read
``schema_version`` first). The reader below is written from that rule alone; the tests run it on a committed fixture
dataset with one extra column, with the columns reordered, and with a required column removed.
"""

import csv
import io
import re
from pathlib import Path

import pytest

from meterline_gold import GOLD_SCHEMA_VERSION, write

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = ROOT / "docs" / "reference" / "gold-datasets.md"
README = ROOT / "packages" / "meterline-gold" / "README.md"
FIXTURE_FILE = ROOT / "docs" / "gold-fixture" / "expected" / "central" / "costs_month.csv"
DATASET = "costs_month"
REQUIRED = ["schema_version"] + [column for column, _kind in write.SCHEMAS[DATASET]]


class MissingColumn(Exception):
    """A column the reader needs is not in the file."""


def read_dataset(text, dataset, required):
    """A reader that follows the rule: the version first, columns by name, unknown columns ignored, a missing one an error."""
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    major = (body[0][header.index("schema_version")] if body else GOLD_SCHEMA_VERSION).split(".")[0]
    assert major == GOLD_SCHEMA_VERSION.split(".")[0], "a different major version: stop"
    missing = [name for name in required if name not in header]
    if missing:
        raise MissingColumn("%s: no column %s" % (dataset, ", ".join(missing)))
    where = {name: header.index(name) for name in required}
    return [{name: row[where[name]] for name in required} for row in body]


def _fixture():
    return FIXTURE_FILE.read_text(encoding="utf-8")


def _rewrite(text, header_edit, row_edit):
    rows = list(csv.reader(io.StringIO(text)))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header_edit(rows[0]))
    for row in rows[1:]:
        writer.writerow(row_edit(row))
    return out.getvalue()


def _section(path, heading):
    text = path.read_text(encoding="utf-8")
    start = text.index("\n## %s\n" % heading)
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


def test_gold_compatibility_section_states_the_rule():
    section = _section(REFERENCE, "Compatibility")
    for phrase in ("Non-breaking", "Breaking", "a new dataset", "a new column, appended", "a new value in an open list",
                   "renamed, removed or retyped", "its grain changed", "new major `GOLD_SCHEMA_VERSION`",
                   "selects columns by name", "ignores a column it does not know", "fails on a column it needs that is missing",
                   "`schema_version`"):
        assert phrase in section, phrase


def test_gold_compatibility_readme_points_to_the_section():
    assert "reference/gold-datasets.md#compatibility" in README.read_text(encoding="utf-8")


def test_gold_compatibility_the_fixture_header_is_what_the_reader_requires():
    header = _fixture().splitlines()[0].split(",")
    assert header == REQUIRED
    assert header[0] == "schema_version"


def test_gold_compatibility_reader_loads_a_dataset_with_one_extra_column():
    plain = read_dataset(_fixture(), DATASET, REQUIRED)
    extra = _rewrite(_fixture(), lambda h: h + ["added_later"], lambda r: r + ["x"])
    assert read_dataset(extra, DATASET, REQUIRED) == plain and len(plain) == 3


def test_gold_compatibility_reader_loads_a_dataset_with_an_extra_column_in_the_middle_and_the_columns_reordered():
    plain = read_dataset(_fixture(), DATASET, REQUIRED)
    order = list(reversed(range(len(REQUIRED))))
    reordered = _rewrite(_fixture(), lambda h: [h[i] for i in order] + ["added_later"], lambda r: [r[i] for i in order] + ["x"])
    assert reordered.splitlines()[0] != _fixture().splitlines()[0]
    assert read_dataset(reordered, DATASET, REQUIRED) == plain


def test_gold_compatibility_reader_fails_on_a_dataset_missing_a_required_column():
    cut = REQUIRED.index("total_cost_ref")
    short = _rewrite(_fixture(), lambda h: h[:cut] + h[cut + 1:], lambda r: r[:cut] + r[cut + 1:])
    with pytest.raises(MissingColumn, match="total_cost_ref"):
        read_dataset(short, DATASET, REQUIRED)


def test_gold_compatibility_reader_stops_on_another_major_version():
    other = _rewrite(_fixture(), lambda h: h, lambda r: ["9.0"] + r[1:])
    with pytest.raises(AssertionError, match="major version"):
        read_dataset(other, DATASET, REQUIRED)


def test_gold_compatibility_a_reader_by_position_would_break_on_the_same_files():
    """The negative control: why the rule says by name. A reader that takes column 8 reads the wrong figure once the columns move."""
    position = REQUIRED.index("seat_cost")
    plain = [r[position] for r in list(csv.reader(io.StringIO(_fixture())))[1:]]
    order = list(reversed(range(len(REQUIRED))))
    reordered = _rewrite(_fixture(), lambda h: [h[i] for i in order], lambda r: [r[i] for i in order])
    by_position = [r[position] for r in list(csv.reader(io.StringIO(reordered)))[1:]]
    assert by_position != plain
    assert [r["seat_cost"] for r in read_dataset(reordered, DATASET, REQUIRED)] == plain


def test_gold_compatibility_version_is_major_dot_minor():
    assert re.fullmatch(r"[0-9]+\.[0-9]+", GOLD_SCHEMA_VERSION)
