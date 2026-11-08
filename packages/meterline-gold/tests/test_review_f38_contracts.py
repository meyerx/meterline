"""Review finding F-38: gold's contracts are machine-readable and every consumer's read list is under test.

- docs/schemas/gold-*.schema.json (organisation CSV files, manifest.json) equal what meterline_gold.contract
  generates, the required columns match the reader, and the fixture's files and a built manifest validate;
- the silver fields gold reads exist in meterline's record classes;
- the files and columns the example dashboard reads by name exist in a built gold folder;
- the LiteLLM keys the price importer reads are the ones the gold spec lists (section 3.4).
"""

import ast
import csv
import json
import re
import shutil
from dataclasses import fields
from pathlib import Path

import pytest

from gold_helpers import FIXTURE, KEY

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "docs" / "schemas"
SRC = Path(__file__).resolve().parents[1] / "src" / "meterline_gold"
REGENERATE = "regenerate with: python -m meterline_gold.contract docs/schemas"

# The silver fields gold reads, per record kind (load.py, load_all and read_silver). A field gold starts to read
# must be added here; a field meterline renames fails here before it fails inside a build.
GOLD_READS = {
    "usage": ["provider", "provider_account_id", "scope", "grain", "period_start", "actor_kind", "actor_id", "product", "model",
              "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write", "tokens_cache_write_5m",
              "tokens_cache_write_1h", "tokens_input_total", "tokens_output", "requests", "quantities", "cost",
              "currency", "cost_basis", "is_final", "batch_id", "pulled_at", "model_provider"],
    "activity": ["provider", "provider_account_id", "scope", "grain", "period_start", "actor_kind", "actor_id", "product",
                 "sessions", "messages", "requests", "suggestions_shown", "suggestions_accepted", "lines_added",
                 "lines_removed", "edits_accepted", "edits_rejected", "commits", "pull_requests", "pulled_at"],
    "seats": ["provider", "provider_account_id", "scope", "grain", "period_start", "seats", "pulled_at"],
    "seat_assignments": ["provider", "provider_account_id", "scope", "grain", "period_start", "actor_kind", "actor_id", "pulled_at"],
    "org_totals": ["provider", "provider_account_id", "scope", "grain", "period_start", "product", "model", "cost", "currency",
                   "cost_basis", "quantities", "is_final", "extras", "pulled_at"],
}
SILVER_TABLES = {"silver_usage": "usage", "silver_usage_batches": "usage", "silver_activity": "activity",
                 "silver_seats": "seats", "silver_assignments": "seat_assignments",
                 "silver_org_totals": "org_totals"}


def _validator(name):
    loaded = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(loaded)
    return jsonschema.Draft202012Validator(loaded, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)


def _csv_rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], [{c: (v if v != "" else None) for c, v in zip(rows[0], r)} for r in rows[1:] if r]


# ---- schemas -----------------------------------------------------------------------------------------------


def test_committed_gold_schemas_equal_the_generated_ones():
    from meterline_gold import contract

    generated = contract.gold_schemas()
    assert sorted(p.name for p in SCHEMAS.glob("gold-*.schema.json")) == sorted(generated), REGENERATE
    for name, content in generated.items():
        assert (SCHEMAS / name).read_text(encoding="utf-8") == contract.dumps(content), name + ": " + REGENERATE


def test_the_fixture_org_files_are_valid():
    from meterline_gold.load import ORG_FILES

    for name in ORG_FILES:
        schema_name = "gold-org-%s.schema.json" % name[:-len(".csv")]
        header, rows = _csv_rows(FIXTURE / "input" / "org" / name)
        assert header == json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))["x-columns"]
        validator = _validator(schema_name)
        for row in rows:
            assert list(validator.iter_errors(row)) == [], (name, row)


def _blank(org_dir, name, column):
    path = org_dir / name
    header, rows = _csv_rows(path)
    if not rows:  # memberships.csv of the fixture is empty: add one valid row first
        rows = [{"person_id": "p01", "unit_id": "u01", "dimension": "line", "role": "member",
                 "valid_from": "2026-05-01", "valid_to": None}]
    rows[0][column] = None
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(["" if row[c] is None else row[c] for c in header])
    return path


def test_the_required_columns_of_the_schemas_are_the_ones_the_reader_refuses_empty(tmp_path):
    from meterline_gold import contract, load
    from meterline_gold.errors import InputError

    hasher = load.Hasher(KEY)
    for name, columns in load.ORG_FILES.items():
        for column in columns:
            if name == "identities.csv" and column == "person_id":
                continue  # required exactly when method is not non_person: the schema's if/then/else
            org = tmp_path / ("%s-%s" % (name, column))
            shutil.copytree(FIXTURE / "input" / "org", org)
            _blank(org, name, column)
            required = column in contract.REQUIRED[name]
            try:
                load.read_org(org, hasher, "line", {"location"})
                refused = False
            except InputError:
                refused = True
            assert refused == required, (name, column, "refused" if refused else "accepted")


def test_a_built_manifest_is_valid_and_a_changed_one_is_not(fixture_out):
    manifest = json.loads((fixture_out / "manifest.json").read_text(encoding="utf-8"))
    validator = _validator("gold-manifest.schema.json")
    assert list(validator.iter_errors(manifest)) == []
    assert list(validator.iter_errors(dict(manifest, a_key_added_by_hand=1)))
    lost = dict(manifest)
    del lost["input_flags"]
    assert list(validator.iter_errors(lost))


# ---- read lists --------------------------------------------------------------------------------------------


def _silver_reads_in_code():
    """{kind: attribute names read on ``r``} inside each load_table call of a silver_* table in load.py."""
    tree = ast.parse((SRC / "load.py").read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "load_table" and len(node.args) > 1:
            table = node.args[1]
            if isinstance(table, ast.Constant) and table.value in SILVER_TABLES:
                kind = SILVER_TABLES[table.value]
                names = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
                         and isinstance(n.value, ast.Name) and n.value.id == "r"}
                found.setdefault(kind, set()).update(names)
    return found


def test_the_silver_fields_gold_reads_are_declared_and_exist_in_meterline():
    import meterline

    in_code = _silver_reads_in_code()
    assert set(in_code) == set(GOLD_READS)
    for kind, read in GOLD_READS.items():
        undeclared = in_code[kind] - set(read)
        assert not undeclared, "load.py reads %s fields not declared in GOLD_READS: %s" % (kind, sorted(undeclared))
        known = {f.name for f in fields(meterline.KINDS[kind])}
        assert set(read) <= known, "meterline has no %s field %s" % (kind, sorted(set(read) - known))
        schema = json.loads((SCHEMAS / ("%s.schema.json" % kind)).read_text(encoding="utf-8"))
        assert set(read) <= set(schema["properties"])


def _js_lists(source, var):
    """{key: [strings]} of a JavaScript object literal of string arrays, ``var NAME = {...};``."""
    body = re.search(r"var %s = \{(.*?)\n\};" % var, source, flags=re.S).group(1)
    return {key.strip('"'): re.findall(r'"([^"]+)"', items)
            for key, items in re.findall(r'("?\w+"?):\s*\[([^\]]*)\]', body)}


def _built_headers(folder):
    """{file name: [headers of every built file of that name]}."""
    out = {}
    for path in sorted(Path(folder).rglob("*.csv")):
        with open(path, encoding="utf-8", newline="") as handle:
            out.setdefault(path.stem, []).append(next(csv.reader(handle)))
    return out


def test_the_files_and_columns_the_dashboard_reads_exist_in_a_built_folder(fixture_out):
    source = (SRC / "dashboard" / "index.html").read_text(encoding="utf-8")
    known = _js_lists(source, "KNOWN")
    assert set(known) == {"central", "exec", "manager", "person"}
    for audience, names in known.items():
        folders = [fixture_out / audience] if audience in ("central", "exec") else \
            sorted(p for p in (fixture_out / audience).iterdir() if p.is_dir())
        assert folders, audience
        for name in names:
            assert (folders[0] / (name + ".csv")).is_file(), (audience, name)
    headers = _built_headers(fixture_out)
    for var in ("COVERED", "MONEY"):
        for name, columns in _js_lists(source, var).items():
            assert name in headers, (var, name)
            seen = set().union(*headers[name])
            assert set(columns) <= seen, (var, name, sorted(set(columns) - seen))
    by_name = set(re.findall(r'indexOf\("(\w+)"\)', source))
    every_column = set().union(*(set(h) for hs in headers.values() for h in hs))
    assert by_name and by_name <= every_column, sorted(by_name - every_column)


def test_the_litellm_keys_the_importer_reads_are_the_ones_the_spec_lists():
    tree = ast.parse((SRC / "importers" / "litellm.py").read_text(encoding="utf-8"))
    in_code = {n.args[0].value for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "entry"
               and n.args and isinstance(n.args[0], ast.Constant)}
    spec = (ROOT / "docs" / "dev" / "meterline-gold-core-SPEC-v1.2-2026-10-15.md").read_text(encoding="utf-8")
    line = next(t for t in spec.splitlines() if "the importer reads only" in t)
    declared = set(re.findall(r"`(\w+)`", line))
    assert in_code == declared
