"""The compatibility policy of the record format (docs/records.md, section Compatibility; review finding F-07).

Three reader behaviours are part of the public contract and are pinned here for all five record kinds, through
``from_dict`` and through the file reader ``read_jsonl``:

- a line with a newer ``record_version`` is refused with a message that tells the user to upgrade meterline;
- a field the reader does not know is ignored (this is what makes a new nullable field a non-breaking change);
- a nullable field missing from a line reads as null (so an older line still reads after a field is added).
"""

import json
import re
from pathlib import Path

import pytest

import meterline as ml
from meterline.records import RECORD_VERSION

ROOT = Path(__file__).resolve().parents[3]
RECORDS_MD = ROOT / "docs" / "records.md"
KIND_CLASSES = {
    "UsageRecord": ml.UsageRecord,
    "ActivityRecord": ml.ActivityRecord,
    "SeatSnapshot": ml.SeatSnapshot,
    "SeatAssignment": ml.SeatAssignment,
    "OrgTotal": ml.OrgTotal,
}
NEWER_MESSAGE = (
    "{kind}: record_version {newer} is newer than this meterline reads ({current}); "
    "upgrade meterline to read it: pip install --upgrade meterline"
)


def _doc():
    return RECORDS_MD.read_text(encoding="utf-8")


def _compatibility_section():
    text = _doc()
    start = text.index("\n## Compatibility\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


def _examples():
    """The five example lines of docs/records.md, by class name."""
    text = _doc()
    section = text[text.index("\n## Examples\n"):]
    found = dict(re.findall(r"`(\w+)`:\s*\n\s*```json\n(.+?)\n```", section, flags=re.S))
    assert set(found) == set(KIND_CLASSES), found.keys()
    return {name: json.loads(line) for name, line in found.items()}


EXAMPLES = _examples()


def test_records_md_has_a_compatibility_section_with_every_part_of_the_policy():
    section = _compatibility_section()
    for heading in (
        "### Changes that keep `record_version`",
        "### Changes that need a new `record_version`",
        "### What a reader does",
        "### `record_version` and package versions",
        "### Versions of the packages",
    ):
        assert heading in section, heading
    assert "order of the fields" in section and "is free" in section


@pytest.mark.parametrize("kind", sorted(KIND_CLASSES))
def test_a_newer_record_version_is_refused_with_an_upgrade_message(kind, tmp_path):
    cls = KIND_CLASSES[kind]
    line = dict(EXAMPLES[kind], record_version=RECORD_VERSION + 1)
    expected = NEWER_MESSAGE.format(kind=kind, newer=RECORD_VERSION + 1, current=RECORD_VERSION)
    with pytest.raises(ml.RecordError) as caught:
        cls.from_dict(line)
    assert str(caught.value) == expected
    path = tmp_path / "newer.jsonl"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    with pytest.raises(ml.RecordError) as caught:
        ml.read_jsonl(path, cls)
    assert str(caught.value) == expected


def test_the_upgrade_message_is_quoted_in_the_policy():
    expected = NEWER_MESSAGE.format(kind="UsageRecord", newer=RECORD_VERSION + 1, current=RECORD_VERSION)
    assert expected in _compatibility_section()


@pytest.mark.parametrize("older", [0, 1])
def test_an_older_or_unknown_record_version_is_still_refused(older):
    with pytest.raises(ml.RecordError, match="is not %d" % RECORD_VERSION):
        ml.UsageRecord.from_dict(dict(EXAMPLES["UsageRecord"], record_version=older))


@pytest.mark.parametrize("kind", sorted(KIND_CLASSES))
def test_an_unknown_field_is_ignored_and_not_written_back(kind, tmp_path):
    cls = KIND_CLASSES[kind]
    line = dict(EXAMPLES[kind], a_field_from_a_newer_writer=7, another_one={"nested": "x"})
    record = cls.from_dict(line)
    assert record == cls.from_dict(EXAMPLES[kind])
    assert "a_field_from_a_newer_writer" not in record.to_dict()
    path = tmp_path / "with_unknown.jsonl"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert ml.read_jsonl(path, cls) == [record]


@pytest.mark.parametrize("kind", sorted(KIND_CLASSES))
def test_a_missing_nullable_field_reads_as_null(kind):
    cls = KIND_CLASSES[kind]
    line = dict(EXAMPLES[kind])
    nullable = [name for name, value in line.items() if value is None]
    assert nullable, "the example of %s has no null field to drop" % kind
    for name in nullable:
        del line[name]
    record = cls.from_dict(line)
    assert all(getattr(record, name) is None for name in nullable)


def test_the_version_table_names_the_current_record_version():
    section = _compatibility_section()
    rows = re.findall(r"^\| (\d+) \|", section, flags=re.M)
    assert rows and int(rows[-1]) == RECORD_VERSION, rows
