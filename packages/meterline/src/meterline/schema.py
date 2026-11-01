"""JSON Schemas of the five record types, generated from the dataclasses in ``records.py``.

The committed copies live in ``docs/schemas/`` of the repository; a test fails when they differ from what this
module generates. To regenerate them after a change to ``records.py``::

    python -m meterline.schema docs/schemas

A schema describes one JSON line as meterline writes it. A nullable field is not required (a missing nullable
field reads as null), and ``additionalProperties`` is true because a reader ignores a field it does not know
(docs/records.md, Compatibility). Rules that span several fields (token sums, grain against dates, cost basis
against cost) are checked by ``meterline.validate`` and are not repeated here.
"""

import json
import sys
import typing
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

from meterline.records import (
    ACTOR_KINDS, COST_BASES, GRAINS, KINDS, RECORD_VERSION, SeatAssignment, SeatSnapshot, _unwrap,
)

DRAFT = "https://json-schema.org/draft/2020-12/schema"
MAP_KEY = "^[a-z][a-z0-9_]*$"
COUNT = {"type": "integer", "minimum": 0}
MONEY = {"type": "string", "pattern": "^[0-9]+\\.[0-9]{2,}$", "description": "A Decimal, at least two decimals."}
QUANTITY = {"type": "string", "pattern": "^[0-9]+(\\.[0-9]*[1-9])?$", "description": "A Decimal, no trailing zeros."}
DATE = {"type": "string", "format": "date", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
TIMESTAMP = {"type": "string", "format": "date-time",
             "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?\\+00:00$"}
NON_EMPTY = ("provider", "provider_account_id", "actor_id")
ENUMS = {"grain": GRAINS, "actor_kind": ACTOR_KINDS, "cost_basis": COST_BASES}
CONSTANTS = {(SeatSnapshot, "grain"): "day", (SeatAssignment, "grain"): "day", (SeatAssignment, "actor_kind"): "user"}
MAPS = {
    "quantities": {"type": "object", "propertyNames": {"pattern": MAP_KEY}, "additionalProperties": QUANTITY},
    "extras": {"type": "object", "propertyNames": {"pattern": MAP_KEY},
               "additionalProperties": {"anyOf": [COUNT, QUANTITY]}},
    "counters": {"type": "object", "propertyNames": {"pattern": MAP_KEY}, "additionalProperties": COUNT},
}


def _base(cls: type, name: str, tp: Any) -> Dict[str, Any]:
    if name == "record_version":
        return {"const": RECORD_VERSION}
    if (cls, name) in CONSTANTS:
        return {"const": CONSTANTS[(cls, name)]}
    if name in ENUMS:
        return {"enum": list(ENUMS[name])}
    if name in MAPS:
        return dict(MAPS[name])
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return dict(COUNT)
    if tp is Decimal:
        return dict(MONEY)
    if tp is datetime:
        return dict(TIMESTAMP)
    if tp is date:
        return dict(DATE)
    if tp is str:
        return {"type": "string", "minLength": 1} if name in NON_EMPTY else {"type": "string"}
    raise TypeError("no JSON Schema rule for %s.%s (%r)" % (cls.__name__, name, tp))


def record_schema(kind: str) -> Dict[str, Any]:
    """The JSON Schema of one line of ``records/<kind>/*.jsonl``."""
    cls = KINDS[kind]
    hints = typing.get_type_hints(cls)
    properties: Dict[str, Any] = {}
    required = []
    for f in fields(cls):
        tp, optional = _unwrap(hints[f.name])
        prop = _base(cls, f.name, tp)
        if optional:
            prop = {"anyOf": [prop, {"type": "null"}]}
        else:
            required.append(f.name)
        properties[f.name] = prop
    return {
        "$schema": DRAFT,
        "title": cls.__name__,
        "description": "One line of records/%s/<batch_id>.jsonl, record_version %d. Generated from "
                       "meterline/records.py by meterline.schema; do not edit by hand. Fields: docs/records.md. "
                       "Retired names: before the first public release the field org_id was renamed provider_account_id "
                       "(record_version 2 was redefined, not bumped)."
                       % (kind, RECORD_VERSION),
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": True,
        "x-record-key": list(cls.KEY_FIELDS),
    }


def record_schemas() -> Dict[str, Dict[str, Any]]:
    """{file name: schema} for the five record kinds."""
    return {"%s.schema.json" % kind: record_schema(kind) for kind in KINDS}


def dumps(schema: Dict[str, Any]) -> str:
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_schemas(out_dir) -> list:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for name, schema in sorted(record_schemas().items()):
        (out / name).write_text(dumps(schema), encoding="utf-8", newline="\n")
        names.append(name)
    return names


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m meterline.schema OUT_DIR")
    for written in write_schemas(sys.argv[1]):
        print(written)
