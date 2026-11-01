"""Owner decision 2026-10-16 (data minimisation): a person folder carries no other person's key.

``person/<key>/person_month.csv`` used to end with the person's manager_key. The manager folders keep the person keys of
their ``named_exceptions`` (a manager needs to know who), and ``central/`` keeps ``manager_key``.
"""

import re
from pathlib import Path

from gold_helpers import read_csv

PK = re.compile(r"pk_[0-9a-f]{16}")


def _person_folders(out):
    return sorted(p for p in (Path(out) / "person").iterdir() if p.is_dir())


def _check(out):
    folders = _person_folders(out)
    assert folders
    for folder in folders:
        own = folder.name
        for path in sorted(folder.glob("*.csv")):
            header, _ = read_csv(path)
            assert "manager_key" not in header, "%s carries manager_key" % path.relative_to(out)
            others = sorted(set(PK.findall(path.read_text(encoding="utf-8"))) - {own})
            assert others == [], "%s holds another person's key: %s" % (path.relative_to(out), others)


def test_person_files_carry_no_other_key_fixture(fixture_out):
    _check(fixture_out)


def test_person_files_carry_no_other_key_tiny_sim(sim_builds):
    _check(sim_builds["tiny"][0])


def test_person_files_carry_no_other_key_central_and_managers_keep_theirs(fixture_out):
    header, rows = read_csv(fixture_out / "central" / "person_month.csv")
    assert "manager_key" in header and any(r[header.index("manager_key")] for r in rows)
    managers = sorted((fixture_out / "manager").iterdir())
    assert managers
    named = [read_csv(m / "named_exceptions.csv") for m in managers]
    assert any(rows for _, rows in named), "no manager folder lists a person in named_exceptions"
    for header, rows in named:
        assert "person_key" in header and "manager_key" in header


def test_person_files_carry_no_other_key_the_manifest_lists_every_key_so_it_is_central_only(fixture_out):
    """manifest.json names every person folder through its paths, so it is not a file for an audience."""
    import json

    manifest = json.loads((fixture_out / "manifest.json").read_text(encoding="utf-8"))
    listed = {PK.search(o["path"]).group(0) for o in manifest["outputs"] if o["path"].startswith("person/")}
    assert listed == {p.name for p in _person_folders(fixture_out)}
    assert not (fixture_out / "central" / "manifest.json").exists()


def test_person_files_carry_no_other_key_person_month_columns_are_the_central_ones_less_manager_key(fixture_out):
    central, _ = read_csv(fixture_out / "central" / "person_month.csv")
    folder = _person_folders(fixture_out)[0]
    own, _ = read_csv(folder / "person_month.csv")
    assert own == [c for c in central if c != "manager_key"]
