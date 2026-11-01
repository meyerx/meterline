"""F-37: gold validates the silver it reads, notices a person who has no person_month row, and checks every key.

``test_silver_validated_on_read``: a record that breaks a rule of the record format (spec 3.7 of meterline) stops the
build with exit 2 and a message, instead of reaching person_day.csv.
``test_person_missing_from_people_flagged``: an identity that points at a person absent from people.csv, and a unit whose
parent is not a unit, are counted in data_quality and in the build summary instead of dropping cost without a trace.
``test_unique_keys_all_datasets``: assertion A01 covers every dataset key of the writer.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from gold_helpers import KEY, build_cli, copy_fixture_input, edit_csv, person_key, read_dicts


def _build(inp, out):
    import meterline_gold

    return meterline_gold.build(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY, out=out)


def _edit_first(inp, kind, wanted, change):
    """Change the first silver record of ``kind`` for which ``wanted(obj)`` holds; returns the file it was in."""
    for path in sorted((Path(inp["silver"]) / "records" / kind).glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() and wanted(json.loads(line)):
                obj = json.loads(line)
                change(obj)
                lines[i] = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                path.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
                return path
    raise AssertionError("no %s record in the fixture matches" % kind)


def _all_buckets(o):
    return all(o.get(f) is not None for f in ("tokens_input_uncached", "tokens_cache_read", "tokens_cache_write",
                                              "tokens_input_total"))


# (case id, kind, record filter, change, words the message must hold)
CORRUPTIONS = [
    ("negative_cost", "usage", lambda o: o.get("cost") is not None and o["grain"] == "day",
     lambda o: o.update(cost="-5.00"), "negative"),
    ("negative_tokens", "usage", lambda o: o.get("tokens_output") is not None and o["grain"] == "day",
     lambda o: o.update(tokens_output=-3), "negative"),
    ("unknown_actor_kind", "usage", lambda o: o["grain"] == "day",
     lambda o: o.update(actor_kind="robot"), "actor_kind"),
    ("unknown_cost_basis", "usage", lambda o: o.get("cost") is not None and o["grain"] == "day",
     lambda o: o.update(cost_basis="bogus"), "cost_basis"),
    ("input_total_differs_from_buckets", "usage", lambda o: _all_buckets(o) and o["grain"] == "day",
     lambda o: o.update(tokens_input_total=o["tokens_input_total"] + 1), "tokens_input_total"),
    ("grain_does_not_match_the_dates", "usage", lambda o: o["grain"] == "day",
     lambda o: o.update(period_end="2026-05-31"), "grain"),
    ("not_a_number_cost", "usage", lambda o: o.get("cost") is not None and o["grain"] == "day",
     lambda o: o.update(cost="NaN"), "non-finite"),
    ("org_total_negative_cost", "org_totals", lambda o: o.get("cost") is not None,
     lambda o: o.update(cost="-1.00"), "negative"),
    ("activity_unknown_actor_kind", "activity", lambda o: o["grain"] == "day",
     lambda o: o.update(actor_kind="robot"), "actor_kind"),
]


@pytest.mark.parametrize("case", [c[0] for c in CORRUPTIONS])
def test_silver_validated_on_read(tmp_path, case):
    from meterline_gold import InputError

    _, kind, wanted, change, words = next(c for c in CORRUPTIONS if c[0] == case)
    inp = copy_fixture_input(tmp_path / "in")
    _edit_first(inp, kind, wanted, change)
    with pytest.raises(InputError) as raised:
        _build(inp, tmp_path / "gold")
    assert words in str(raised.value), str(raised.value)
    assert not (tmp_path / "gold").exists() and not (tmp_path / "gold.tmp").exists()


def test_silver_validated_on_read_exit_code_2_with_the_file_named(tmp_path, monkeypatch, capsys):
    inp = copy_fixture_input(tmp_path / "in")
    path = _edit_first(inp, "usage", lambda o: o.get("cost") is not None and o["grain"] == "day",
                       lambda o: o.update(cost="-5.00"))
    assert build_cli(inp, tmp_path / "gold", monkeypatch) == 2
    err = capsys.readouterr().err
    assert path.name in err and "negative" in err


def test_silver_validated_on_read_the_unchanged_fixture_and_the_tiny_sim_still_build(fixture_out, sim_builds):
    """Positive control: the validator rejects nothing that the shipped inputs hold."""
    assert (fixture_out / "manifest.json").is_file()
    assert (sim_builds["tiny"][0] / "manifest.json").is_file()


# ---- a person or a unit parent that is missing --------------------------------------------------------------


def _largest_person(fixture_out):
    rows = read_dicts(fixture_out / "central" / "person_month.csv")
    rows = [r for r in rows if r["cost_ref"]]
    best = max(rows, key=lambda r: Decimal(r["cost_ref"]))
    return best["person_key"], Decimal(best["cost_ref"])


def _person_id_of(person_key_value):
    for n in range(1, 40):
        if person_key("p%02d" % n) == person_key_value:
            return "p%02d" % n
    raise AssertionError("no fixture person has the key " + person_key_value)


def _dq(out, metric):
    return [r for r in read_dicts(out / "central" / "data_quality.csv") if r["metric"] == metric]


def test_person_missing_from_people_flagged(tmp_path, fixture_out, monkeypatch, capsys):
    key, cost = _largest_person(fixture_out)
    person_id = _person_id_of(key)
    # control: the unchanged fixture flags nothing
    assert _dq(fixture_out, "rows_person_missing") == [] and _dq(fixture_out, "cost_ref_person_missing") == []
    clean = json.loads((fixture_out / "manifest.json").read_text(encoding="utf-8"))["input_flags"]
    assert clean == {"persons_missing": 0, "rows_person_missing": 0, "units_parent_unknown": 0}

    inp = copy_fixture_input(tmp_path / "in")
    edit_csv(Path(inp["org"]) / "people.csv", lambda header, rows: _drop_person(header, rows, person_id))
    assert build_cli(inp, tmp_path / "gold", monkeypatch) == 0
    out = capsys.readouterr().out

    rows = _dq(tmp_path / "gold", "rows_person_missing")
    money = _dq(tmp_path / "gold", "cost_ref_person_missing")
    assert rows and sum(int(r["value"]) for r in rows) >= 1
    assert sum(Decimal(r["value"]) for r in money) == cost
    flags = json.loads((tmp_path / "gold" / "manifest.json").read_text(encoding="utf-8"))["input_flags"]
    assert flags["persons_missing"] == 1
    assert flags["rows_person_missing"] == sum(int(r["value"]) for r in rows)
    # the build summary on stdout says it
    assert "1 person" in out and "people.csv" in out
    # the person has no person_month row, and the cost is explained rather than lost
    assert key not in {r["person_key"] for r in read_dicts(tmp_path / "gold" / "central" / "person_month.csv")}


def _drop_person(header, rows, person_id):
    kept = [r for r in rows if r[header.index("person_id")] != person_id]
    assert len(kept) == len(rows) - 1
    rows[:] = kept


def test_person_missing_from_people_flagged_an_identity_alone_is_counted(tmp_path, monkeypatch, capsys):
    """An identity row for a person absent from people.csv counts even when that person has no usage."""
    inp = copy_fixture_input(tmp_path / "in")
    edit_csv(Path(inp["org"]) / "identities.csv",
             lambda header, rows: rows.append(["p99", "anthropic", "email", "user", "user_unused", "",
                                               "nobody@example.com", "2026-05-01", ""]))
    assert build_cli(inp, tmp_path / "gold", monkeypatch) == 0
    flags = json.loads((tmp_path / "gold" / "manifest.json").read_text(encoding="utf-8"))["input_flags"]
    assert flags["persons_missing"] == 1 and flags["rows_person_missing"] == 0
    assert "1 person" in capsys.readouterr().out


def test_person_missing_from_people_flagged_unknown_unit_parent(tmp_path, monkeypatch, capsys):
    inp = copy_fixture_input(tmp_path / "in")

    def orphan_u03(header, rows):
        for r in rows:
            if r[header.index("unit_id")] == "u03":
                r[header.index("parent_id")] = "u99"

    edit_csv(Path(inp["org"]) / "units.csv", orphan_u03)
    assert build_cli(inp, tmp_path / "gold", monkeypatch) == 0
    rows = _dq(tmp_path / "gold", "units_parent_unknown")
    assert [(r["month"], r["provider"], r["provider_account_id"], r["value"]) for r in rows] == [("2026-05", "", "", "1")]
    flags = json.loads((tmp_path / "gold" / "manifest.json").read_text(encoding="utf-8"))["input_flags"]
    assert flags["units_parent_unknown"] == 1
    assert "parent" in capsys.readouterr().out


# ---- assertion A10: person_month cost is costs_month cost, or explained --------------------------------------


def _prepared(inp):
    from meterline_gold import run

    return run.prepare(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                       invoice=inp["invoice"], key=KEY)


def test_person_missing_from_people_flagged_a10_explains_the_missing_person_and_fires_on_a_lost_cost(tmp_path):
    from meterline_gold import run

    inp = copy_fixture_input(tmp_path / "in")
    con = _prepared(inp)
    assert "A10" in [n for n, _ in run.ASSERTIONS]
    assert run.failures(con) == []
    # a person_month row that vanishes with nobody saying so: the sums no longer agree
    con.execute("DELETE FROM person_month WHERE cost_ref = (SELECT max(cost_ref) FROM person_month)")
    assert "A10" in [n for n, _ in run.failures(con)]


def test_person_missing_from_people_flagged_a10_holds_when_the_person_is_flagged(tmp_path):
    from meterline_gold import run

    inp = copy_fixture_input(tmp_path / "in")
    edit_csv(Path(inp["org"]) / "people.csv", lambda header, rows: _drop_person(header, rows, "p10"))
    con = _prepared(inp)
    assert run.failures(con) == []


# ---- A01 over every dataset key ---------------------------------------------------------------------------


def test_unique_keys_all_datasets(tmp_path):
    """A duplicate row in any of the 20 datasets of write.DATASET_KEYS makes A01 fire for that dataset."""
    from meterline_gold import run, write

    inp = copy_fixture_input(tmp_path / "in")
    # dataset name -> the table that holds it in the connection
    tables = {name: name for name in write.DATASET_KEYS}
    tables["comparison"] = "person_comparison"
    assert len(tables) == 20
    missing = []
    con = _prepared(inp)
    for name, table in sorted(tables.items()):
        count = con.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
        if count == 0:
            missing.append(name + " (empty in the fixture)")
            continue
        con.execute("INSERT INTO %s SELECT * FROM %s WHERE rowid = (SELECT min(rowid) FROM %s)" % (table, table, table))
        fired = {row[0] for n, row in run.failures(con) if n == "A01"}
        if name not in fired:
            missing.append(name)
        con.execute("DELETE FROM %s WHERE rowid = (SELECT max(rowid) FROM %s)" % (table, table))
    assert missing == [], "A01 does not fire for: %s" % ", ".join(missing)
