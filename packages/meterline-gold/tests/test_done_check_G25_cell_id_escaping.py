"""Phase 3 done-check G25: cell ids hold values escaped as %25, %7C and %3D (spec 7.1, 7.5, 4.3)."""

from gold_helpers import build_cli, copy_fixture_input, edit_csv, edit_policy, person_key, read_dicts


def _escaped_copy(root):
    inp = copy_fixture_input(root)

    def people(header, rows):
        i_id, i_fn, i_attrs = header.index("person_id"), header.index("function"), header.index("attrs")
        for row in rows:
            if row[i_id] == "p03":
                row[i_fn], row[i_attrs] = "s|attrs.location=a", '{"location":"b"}'
            elif row[i_id] == "p04":
                row[i_fn], row[i_attrs] = "s", '{"location":"a|attrs.location=b"}'
            elif row[i_id] == "p05":
                row[i_attrs] = '{"location":"50%"}'

    edit_csv(inp["org"] / "people.csv", people)
    edit_policy(inp["policy"], lambda p: p["privacy"].update({"compare_by": ["function", "attrs.location"]}))
    return inp


def _compare_cells(out):
    cells = {}
    for row in read_dicts(out / "central" / "person_month.csv"):
        key = row["person_key"]
        path = out / "person" / key / "comparison.csv"
        rows = [r for r in read_dicts(path) if r["month"] == "2026-05"]
        cells[key] = rows[0]["compare_cell"] if rows else None
    return cells


def test_done_check_G25_compare_cell_ids_are_escaped(tmp_path, monkeypatch):
    inp = _escaped_copy(tmp_path / "input")
    out = tmp_path / "gold"
    assert build_cli(inp, out, monkeypatch) == 0
    cells = _compare_cells(out)
    p03, p04, p05 = (person_key(p) for p in ("p03", "p04", "p05"))
    assert cells[p03] == "function=s%7Cattrs.location%3Da|attrs.location=b"
    assert cells[p04] == "function=s|attrs.location=a%7Cattrs.location%3Db"
    assert cells[p05].endswith("attrs.location=50%25")
    assert cells[p03] != cells[p04]

    # every other person's id equals the one of a build of the copy's policy over the unchanged org files
    plain = copy_fixture_input(tmp_path / "plain")
    edit_policy(plain["policy"], lambda p: p["privacy"].update({"compare_by": ["function", "attrs.location"]}))
    base = tmp_path / "gold_plain"
    assert build_cli(plain, base, monkeypatch) == 0
    plain_cells = _compare_cells(base)
    others = [k for k in cells if k not in (p03, p04, p05)]
    assert len(others) == len(cells) - 3 and others
    for key in others:
        assert cells[key] == plain_cells[key], key


def test_done_check_G25_grid_cell_ids_are_escaped(tmp_path, monkeypatch):
    inp = _escaped_copy(tmp_path / "input")
    edit_policy(
        inp["policy"],
        lambda p: p["privacy"].update({"tree": {"kind": "attributes", "attributes": ["function"]}}),
    )
    out = tmp_path / "gold"
    assert build_cli(inp, out, monkeypatch) == 0
    rows = read_dicts(out / "central" / "groups_month.csv")
    match = [r for r in rows if r["cell_id"] == "attr:function=s%7Cattrs.location%3Da"]
    assert len(match) == 1
    assert match[0]["cell_key"] == "function=s%7Cattrs.location%3Da"
    # negative control: no row holds the unescaped id
    assert not [r for r in rows if r["cell_id"] == "attr:function=s|attrs.location=a"]


def test_done_check_G25_attrs_keep_name_with_a_pipe_exits_2(tmp_path, monkeypatch):
    inp = copy_fixture_input(tmp_path / "input")
    edit_policy(inp["policy"], lambda p: p["people"].update({"attrs_keep": ["loc|x"]}))
    # compare_by names location, which is no longer kept; drop it so the only fault is the name
    edit_policy(inp["policy"], lambda p: p["privacy"].update({"compare_by": ["function", "level"]}))
    assert build_cli(inp, tmp_path / "gold", monkeypatch) == 2
    # control: a name of the allowed pattern builds
    ok = copy_fixture_input(tmp_path / "ok")
    edit_policy(ok["policy"], lambda p: p["people"].update({"attrs_keep": ["loc-x_1"]}))
    edit_policy(ok["policy"], lambda p: p["privacy"].update({"compare_by": ["function", "level"]}))
    assert build_cli(ok, tmp_path / "gold_ok", monkeypatch) == 0
