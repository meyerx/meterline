"""F-39: docs/PRIVACY.md lists every file a gold build writes, with exactly the personal columns that file holds,
and every personal field of the record format.

The inventory is checked against a real build of the hand-built fixture, so a new file or a new personal column
fails this test until the privacy statement names it, and a column that leaves a file (as manager_key left the
person folders) fails it until the statement drops it.
"""

import dataclasses
import re
from pathlib import Path

from gold_helpers import REPO, read_csv

PRIVACY = REPO / "docs" / "PRIVACY.md"
# Columns that identify a person directly or through a key.
PERSONAL = {"person_key", "manager_key", "actor_key", "head_key", "subject_key", "given_name", "family_name", "email"}


def _inventory_rows():
    """File cell -> set of backticked names in the personal-columns cell, for every inventory table row."""
    text = PRIVACY.read_text(encoding="utf-8")
    body = re.search(r"^## Personal-data inventory\b(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert body, "docs/PRIVACY.md has no '## Personal-data inventory' section"
    rows = {}
    for line in body.group(1).splitlines():
        if not line.startswith("|") or set(line) <= set("|- :"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        for name in re.findall(r"`([^`]+)`", cells[1]):
            rows[name] = set(re.findall(r"`([^`]+)`", cells[2]))
    return rows


def _logical(rel):
    parts = rel.split("/")
    if parts[0] == "manager" and len(parts) == 3:
        return "manager/<manager_key>/" + parts[2]
    if parts[0] == "person" and len(parts) == 3:
        return "person/<person_key>/" + parts[2]
    return rel


def test_privacy_inventory_lists_every_gold_file_with_its_personal_columns(fixture_out):
    rows = _inventory_rows()
    seen = {}
    for path in sorted(Path(fixture_out).rglob("*.csv")):
        header, _ = read_csv(path)
        seen[_logical(path.relative_to(fixture_out).as_posix())] = PERSONAL & set(header)
    assert len(seen) > 20
    problems = []
    for name, personal in sorted(seen.items()):
        if name not in rows:
            problems.append("%s is not in the inventory" % name)
        elif rows[name] & PERSONAL != personal:
            problems.append("%s: inventory says %s, the file has %s"
                            % (name, sorted(rows[name] & PERSONAL), sorted(personal)))
    assert "manifest.json" in rows, "manifest.json is not in the inventory"
    assert not problems, problems


def test_privacy_inventory_lists_every_personal_field_of_the_records():
    from meterline import records

    personal = set()
    for cls in (records.UsageRecord, records.ActivityRecord, records.SeatAssignment):
        personal |= {f.name for f in dataclasses.fields(cls) if f.name.startswith("actor_")}
    personal |= {"granted_by"}
    listed = set()
    for name, columns in _inventory_rows().items():
        if name.startswith("records/"):
            listed |= columns
    assert personal <= listed, sorted(personal - listed)
