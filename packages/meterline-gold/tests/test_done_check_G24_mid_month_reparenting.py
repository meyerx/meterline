"""Phase 3 done-check G24: a unit that changes parent in the middle of a month (spec 7.1, 9.5 A05, 14)."""

import pytest

from gold_helpers import KEY, build_cli, copy_fixture_input, edit_csv, person_key


def _unit_row(unit_id, valid_from, valid_to, parent_id):
    return [unit_id, valid_from, valid_to, "line", "alpha-x", "Alpha X", "team", parent_id, "sales", "CC-02", ""]


def _reparent_copy(root):
    inp = copy_fixture_input(root)

    def units(header, rows):
        rows.append(_unit_row("u04", "2026-05-01", "2026-05-15", "u02"))
        rows.append(_unit_row("u04", "2026-05-16", "", "u03"))

    def people(header, rows):
        i_id, i_unit, i_to = header.index("person_id"), header.index("unit_id"), header.index("valid_to")
        for row in rows:
            if row[i_id] in ("p03", "p04", "p05"):
                row[i_unit] = "u04"
            if row[i_id] == "p05":
                row[i_to] = "2026-05-10"

    edit_csv(inp["org"] / "units.csv", units)
    edit_csv(inp["org"] / "people.csv", people)
    return inp


def _prepare(inp):
    from meterline_gold import run

    return run.prepare(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY,
    )


def test_done_check_G24_unit_sits_under_its_last_parent_of_the_month(tmp_path, monkeypatch):
    inp = _reparent_copy(tmp_path / "input")
    assert build_cli(inp, tmp_path / "gold", monkeypatch) == 0
    from meterline_gold import run

    con = _prepare(inp)
    parent = con.execute(
        "SELECT parent_cell_id FROM cells WHERE month = '2026-05' AND cell_id = 'unit:u04'").fetchone()
    assert parent == ("unit:u03",)
    p05 = person_key("p05")
    in_u03 = con.execute(
        "SELECT list_contains(persons_list, ?) FROM cells WHERE month = '2026-05' AND cell_id = 'unit:u03'",
        [p05]).fetchone()[0]
    in_u02 = con.execute(
        "SELECT list_contains(persons_list, ?) FROM cells WHERE month = '2026-05' AND cell_id = 'unit:u02'",
        [p05]).fetchone()[0]
    assert in_u03 is True and in_u02 is False
    assert run.failures(con) == []

    # negative control: the parent recorded as u02 while the persons stay under u03 breaks A05 (subset of parent)
    con.execute("UPDATE cells SET parent_cell_id = 'unit:u02' WHERE month = '2026-05' AND cell_id = 'unit:u04'")
    run.run_privacy(con, 5)
    assert "A05" in [n for n, _ in run.failures(con)]


def test_done_check_G24_a_parent_cycle_exits_2_naming_units_csv(tmp_path, monkeypatch, capsys):
    inp = copy_fixture_input(tmp_path / "input")

    def units(header, rows):
        i_id, i_to = header.index("unit_id"), header.index("valid_to")
        for row in rows:
            if row[i_id] == "u02":
                row[i_to] = "2026-05-15"
        rows.append(["u02", "2026-05-16", "", "line", "alpha", "Alpha", "team", "u04", "sales", "CC-02", "p02"])
        rows.append(_unit_row("u04", "2026-05-01", "2026-05-15", "u02"))
        rows.append(_unit_row("u04", "2026-05-16", "", "u02"))

    def people(header, rows):
        i_id, i_unit = header.index("person_id"), header.index("unit_id")
        for row in rows:
            if row[i_id] in ("p03", "p04", "p05"):
                row[i_unit] = "u04"

    edit_csv(inp["org"] / "units.csv", units)
    edit_csv(inp["org"] / "people.csv", people)
    assert build_cli(inp, tmp_path / "gold", monkeypatch) == 2
    err = capsys.readouterr().err
    assert "units.csv" in err and ("u02" in err or "u04" in err)
    assert not (tmp_path / "gold").exists()
