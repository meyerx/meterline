"""The JSON Schemas of the two freshness files the publish step writes: ``central/published.json`` and ``freshness.json``.

Generated, never edited by hand: ``python -m meterline_job.schemas docs/schemas/job`` writes ``published.schema.json`` and
``freshness.schema.json``. A test fails when a committed file differs from what this module generates, and a second one
validates real files against them.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

DRAFT = "https://json-schema.org/draft/2020-12/schema"
_MONTH = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"}
_MONTHS = {"type": "array", "items": _MONTH}
_UTC = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+\\+00:00$",
        "description": "a UTC instant, ISO 8601 with the +00:00 offset"}


def published_schema() -> Dict[str, Any]:
    return {
        "$schema": DRAFT,
        "title": "published.json",
        "description": "central/published.json of the published folder: what it holds and how fresh it is. Written by the publish "
                       "step of meterline-job; central only. Generated from meterline_job/schemas.py; do not edit by hand.",
        "type": "object",
        "additionalProperties": False,
        "required": ["latest_build", "data_complete_through", "published_at", "frozen_months", "in_progress_months",
                     "forced_freezes", "key_rotations", "policy_sha256", "warnings"],
        "properties": {
            "latest_build": {"type": "string", "description": "the name of the build the in-progress months come from (builds/<name>)"},
            "data_complete_through": {"anyOf": [{"type": "string", "format": "date", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                                                {"type": "null"}],
                                      "description": "the earliest complete_through of the configured connectors; null when one has none"},
            "published_at": dict(_UTC, description="when this folder was assembled (UTC)"),
            "frozen_months": dict(_MONTHS, description="months delivered once and never changed"),
            "in_progress_months": dict(_MONTHS, description="months of the latest build that are refreshed on every run"),
            "forced_freezes": dict(_MONTHS, description="frozen months whose sources were not all final: the cap forced them"),
            "key_rotations": dict(_MONTHS, description="frozen months whose hashing key differs from the latest build's: "
                                                       "manager and person folders do not show them"),
            "policy_sha256": {"type": "object", "additionalProperties": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                              "description": "per source build (the latest and each frozen month's), the hash of the policy it was made with"},
            "warnings": {"type": "array", "items": {"type": "string"},
                         "description": "forced_freeze YYYY-MM and policy_changed YYYY-MM of this publish"},
        },
    }


def freshness_schema() -> Dict[str, Any]:
    return {
        "$schema": DRAFT,
        "title": "freshness.json",
        "description": "freshness.json of an audience folder of the published folder (exec/, manager/<key>/, person/<key>/): when "
                       "it was assembled and which frozen months it holds. Generated from meterline_job/schemas.py; do not edit by hand.",
        "type": "object",
        "additionalProperties": False,
        "required": ["published_at", "frozen_months"],
        "properties": {
            "published_at": dict(_UTC, description="when the published folder was assembled (UTC)"),
            "frozen_months": dict(_MONTHS, description="the frozen months this folder shows"),
        },
    }


def write_schemas(folder: Any) -> List[Path]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in (("published.schema.json", published_schema()), ("freshness.schema.json", freshness_schema())):
        path = folder / name
        path.write_text(json.dumps(schema, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m meterline_job.schemas FOLDER")
    for written in write_schemas(sys.argv[1]):
        print("wrote", written)
