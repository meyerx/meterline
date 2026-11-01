"""E3: every row of a shape README names its source, the date the source was read and a mark ([V] or [I]), every shape
file is named in a row, and no shape README calls a shape "current" (a shape goes stale; the date says how old it is).

The rules are written in the contract page, section "Shape files". Run: python3 -m pytest -q tools/test_shape_readmes.py
"""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHAPES = os.path.join(ROOT, "docs", "api-shapes")
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
MARK = re.compile(r"\[(?:V|I)\]")
CURRENT = re.compile(r"\bcurrent\b", re.I)
SHAPE_SUFFIXES = (".json", ".ndjson", ".csv")


def readmes():
    return [os.path.join(SHAPES, "README.md")] + sorted(glob.glob(os.path.join(SHAPES, "*", "README.md")))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def shape_tables(text):
    """(header cells, rows) of every table whose header has a Source column; a row is its list of cells."""
    found, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and re.fullmatch(r"[|\s:-]+", lines[i + 1].strip()):
            header, rows, i = cells(lines[i]), [], i + 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            if any("source" in h.lower() for h in header):
                found.append((header, rows))
        else:
            i += 1
    return found


def row_problems(header, row):
    """What a row of a shape table lacks: a source, a date, a mark."""
    lowered = [h.lower() for h in header]
    named = dict(zip(lowered, row, strict=False))
    source = next((v for k, v in named.items() if "source" in k), "")
    dated = next((v for k, v in named.items() if k.startswith("read") or "date" in k), "")
    mark = next((v for k, v in named.items() if "mark" in k), "")
    problems = []
    if not source.strip():
        problems.append("no source")
    if not DATE.search(source) and not DATE.search(dated):
        problems.append("no date read")
    if not MARK.search(mark):
        problems.append("no [V] or [I] mark")
    return problems


def readme_problems(text):
    tables = shape_tables(text)
    if not tables:
        return ["no table with a Source column"]
    found = []
    for header, rows in tables:
        if not any("mark" in h.lower() for h in header):
            found.append("the table has no Mark column")
        for row in rows:
            found += ["%s: %s" % (row[0], p) for p in row_problems(header, row)]
    found += ["says \"%s\"" % m.group(0) for m in CURRENT.finditer(text)]
    return found


def test_every_row_of_every_shape_readme_has_a_source_a_date_and_a_mark():
    assert len(readmes()) >= 5
    for path in readmes():
        assert readme_problems(read(path)) == [], os.path.relpath(path, ROOT)


def test_no_shape_readme_calls_a_shape_current():
    for path in readmes():
        assert not CURRENT.search(read(path)), "%s says current: say the date and the source instead" % os.path.relpath(path, ROOT)


def test_every_shape_file_is_named_in_a_row_of_the_readme_beside_it():
    for path in readmes():
        folder = os.path.dirname(path)
        rows = [r[0] for _, rs in shape_tables(read(path)) for r in rs]
        files = [f for f in sorted(os.listdir(folder)) if f.endswith(SHAPE_SUFFIXES)]
        assert files, folder
        for name in files:
            assert any("`%s`" % name in first for first in rows), "%s names no row for %s" % (os.path.relpath(path, ROOT), name)


# ---- negative controls: the checks above see a planted defect ----

GOOD = """# Shapes

| File | Endpoint | Source | Read on | Mark |
|---|---|---|---|---|
| `a.json` | `GET /a` | the provider reference, page A | 2026-10-10 | [V] |
"""


def test_a_good_table_has_no_problem():
    assert readme_problems(GOOD) == []


def test_a_row_without_a_source_is_reported():
    assert readme_problems(GOOD.replace("the provider reference, page A", " ")) == ["`a.json`: no source"]


def test_a_row_without_a_date_is_reported():
    assert readme_problems(GOOD.replace("2026-10-10", "later")) == ["`a.json`: no date read"]


def test_a_date_inside_the_source_cell_counts():
    assert readme_problems(GOOD.replace("page A", "page A, read on 2026-10-10").replace("| 2026-10-10 |", "|  |")) == []


def test_a_row_without_a_mark_is_reported():
    assert readme_problems(GOOD.replace("[V]", "yes")) == ["`a.json`: no [V] or [I] mark"]


def test_a_table_without_a_mark_column_is_reported():
    text = GOOD.replace(" Mark |", " Note |").replace("[V]", "fine")
    assert "the table has no Mark column" in readme_problems(text)


def test_the_word_current_is_reported_in_the_body_and_in_front_matter():
    assert readme_problems(GOOD + "\nThis is the current shape.\n") == ['says "current"']
    assert readme_problems("---\nstatus: current\n---\n" + GOOD) == ['says "current"']
    assert readme_problems(GOOD + "\nThe parameter include_current_utc_day is never sent.\n") == []


def test_a_readme_without_a_shape_table_is_reported():
    assert readme_problems("# Shapes\n\nNo table here.\n") == ["no table with a Source column"]
