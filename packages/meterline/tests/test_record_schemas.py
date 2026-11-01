"""Machine-readable JSON Schemas of the five record types (review finding F-38).

The files in docs/schemas/ are generated from records.py by ``python -m meterline.schema docs/schemas``; this test
fails when a committed file differs from what the code generates, and checks the schemas against real lines: the
examples of docs/records.md and records built in code, through ``to_dict``.
"""

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

import meterline as ml  # noqa: E402
from meterline import schema  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "docs" / "schemas"
RECORDS_MD = ROOT / "docs" / "records.md"
REGENERATE = "regenerate with: python -m meterline.schema docs/schemas"
T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def _validator(kind):
    loaded = json.loads((SCHEMAS / ("%s.schema.json" % kind)).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(loaded)
    return jsonschema.Draft202012Validator(loaded, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)


def _doc_examples():
    text = RECORDS_MD.read_text(encoding="utf-8")
    section = text[text.index("\n## Examples\n"):]
    found = dict(re.findall(r"`(\w+)`:\s*\n\s*```json\n(.+?)\n```", section, flags=re.S))
    by_class = {cls.__name__: kind for kind, cls in ml.KINDS.items()}
    return {by_class[name]: json.loads(line) for name, line in found.items()}


def test_committed_schemas_equal_the_generated_ones():
    generated = schema.record_schemas()
    committed = sorted(p.name for p in SCHEMAS.glob("*.schema.json") if not p.name.startswith("gold-"))
    assert committed == sorted(generated), REGENERATE
    for name, content in generated.items():
        assert (SCHEMAS / name).read_text(encoding="utf-8") == schema.dumps(content), name + ": " + REGENERATE


@pytest.mark.parametrize("kind", sorted(ml.KINDS))
def test_every_field_of_the_code_is_in_the_schema(kind):
    loaded = json.loads((SCHEMAS / ("%s.schema.json" % kind)).read_text(encoding="utf-8"))
    names = [f.name for f in ml.KINDS[kind].__dataclass_fields__.values()]
    assert sorted(loaded["properties"]) == sorted(names)
    assert loaded["x-record-key"] == list(ml.KINDS[kind].KEY_FIELDS)


@pytest.mark.parametrize("kind", sorted(ml.KINDS))
def test_the_records_md_example_is_valid(kind):
    errors = list(_validator(kind).iter_errors(_doc_examples()[kind]))
    assert errors == []


def _usage(**over):
    base = dict(
        provider="litellm", provider_account_id="proxy-a", grain="day", period_start=date(2026, 9, 15),
        period_end=date(2026, 9, 15), actor_kind="proxy_user", actor_id="user-1", product="", model="m",
        tokens_input_uncached=0, tokens_cache_read=None, tokens_cache_write=None, tokens_input_total=7,
        tokens_output=3, quantities={"ai_credit": Decimal("12.50")}, cost=Decimal("0.000123"),
        currency="USD", cost_basis="estimate", is_final=True, extras={"flat_cost": Decimal("0.10"), "failed_requests": 2},
        pulled_at=T0, batch_id="20261009T080000Z",
    )
    base.update(over)
    return ml.UsageRecord(**base)


def test_a_record_written_by_the_code_is_valid_and_broken_lines_are_not():
    validator = _validator("usage")
    line = _usage().to_dict()
    assert list(validator.iter_errors(line)) == []
    assert list(validator.iter_errors(dict(line, a_field_from_a_newer_writer=1))) == []  # unknown fields allowed
    for broken in (
        dict(line, tokens_output="3"),          # a count written as a string
        dict(line, cost=0.000123),              # money as a JSON number
        dict(line, grain="week"),               # not a grain
        dict(line, actor_id=""),                # empty id
        dict(line, record_version=3),           # another version
        dict(line, quantities={"AI credit": "1"}),  # map key not lower-case with underscores
        {k: v for k, v in line.items() if k != "model"},  # a required field missing
    ):
        assert list(validator.iter_errors(broken)), broken


def test_a_missing_nullable_field_is_not_required():
    line = _usage().to_dict()
    del line["tokens_reasoning"]
    assert list(_validator("usage").iter_errors(line)) == []
