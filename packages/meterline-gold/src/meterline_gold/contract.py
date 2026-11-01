"""JSON Schemas of the organisation CSV files gold reads, of the manifest.json it writes (review finding F-38) and
of every dataset file a build writes (integration base B3), plus the audience rules and the dataset reference page.

The committed copies live in ``docs/schemas/`` and ``docs/reference/`` of the repository; a test fails when they
differ from what this module generates, checks the required columns against the reader in ``load.py``, validates a
real manifest and validates every row of a real build. To regenerate them after a change::

    python -m meterline_gold.contract docs/schemas docs/reference/gold-datasets.md

The dataset columns and types come from ``write.SCHEMAS``; what the writer does not know (descriptions, units, joins,
audiences, the cost rule) is the one table in ``dataset_docs``. The silver record schemas sit beside the schemas and
come from ``meterline.schema``.
"""

import json
import sys
from pathlib import Path

from meterline_gold import GOLD_SCHEMA_VERSION, audiences, dataset_docs, write
from meterline_gold.load import METHODS, ORG_FILES

DRAFT = "https://json-schema.org/draft/2020-12/schema"
DATE = {"type": "string", "format": "date", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
TEXT = {"type": "string", "minLength": 1}
SHA256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
COUNT = {"type": "integer", "minimum": 0}

# The columns load.read_org refuses to find empty (identities.person_id is empty exactly when method is non_person).
REQUIRED = {
    "units.csv": ("unit_id", "valid_from", "dimension"),
    "people.csv": ("person_id", "valid_from", "unit_id"),
    "memberships.csv": ("person_id", "unit_id", "valid_from"),
    "identities.csv": ("provider", "method", "actor_kind", "actor_id", "valid_from"),
    "directory.csv": ("person_id",),
}
DATES = ("valid_from", "valid_to")
ROW_NOTE = ("One data row of %s, read as an object of column name to cell text; an empty cell is null. The header "
            "row must be exactly the columns of x-columns, in that order. Generated from meterline_gold/load.py by "
            "meterline_gold.contract; do not edit by hand. Meaning of each column: the gold spec, section 3.2.")


def _org_schema(name):
    columns = ORG_FILES[name]
    properties = {}
    for column in columns:
        if column in DATES:
            cell = dict(DATE)
        elif name == "identities.csv" and column == "method":
            cell = {"enum": list(METHODS)}
        else:
            cell = dict(TEXT)
        if column not in REQUIRED[name]:
            cell = {"anyOf": [cell, {"type": "null"}]}
        properties[column] = cell
    schema = {
        "$schema": DRAFT,
        "title": name,
        "description": ROW_NOTE % name,
        "type": "object",
        "properties": properties,
        "required": list(columns),
        "additionalProperties": False,
        "x-columns": list(columns),
    }
    if name == "identities.csv":
        schema["if"] = {"properties": {"method": {"const": "non_person"}}}
        schema["then"] = {"properties": {"person_id": {"type": "null"}}}
        schema["else"] = {"properties": {"person_id": dict(TEXT)}}
    return schema


def _obj(properties, **extra):
    return dict({"type": "object", "properties": properties, "required": sorted(properties),
                 "additionalProperties": False}, **extra)


def manifest_schema():
    """The schema of manifest.json as write.write_manifest writes it."""
    path_hash = _obj({"path": {"type": "string"}, "sha256": SHA256})
    return dict(_obj({
        "gold_schema_version": {"const": GOLD_SCHEMA_VERSION},
        "generator": _obj({"name": {"const": "meterline-gold"}, "version": {"type": "string"}}),
        "as_of": {"anyOf": [DATE, {"type": "null"}]},
        "data_through": {"anyOf": [DATE, {"type": "null"}]},
        "months": {"type": "array", "items": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"}},
        "key_check": {"type": "string", "pattern": "^kc_"},
        "inputs": _obj({
            "policy_sha256": SHA256,
            "prices_sha256": SHA256,
            "invoice_sha256": {"anyOf": [SHA256, {"type": "null"}]},
            "org": {"type": "array", "items": path_hash},
            "silver": {"type": "array", "items": path_hash},
        }),
        "suppression": _obj({"cells": COUNT, "k": COUNT, "differencing": COUNT, "secondary": COUNT}),
        "input_flags": _obj({"persons_missing": COUNT, "rows_person_missing": COUNT, "units_parent_unknown": COUNT}),
        "unknown_envelope_keys": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        "outputs": {"type": "array", "items": _obj({
            "path": {"type": "string"}, "bytes": COUNT, "sha256": SHA256,
            "rows": {"anyOf": [COUNT, {"type": "null"}]},
        })},
    }), **{
        "$schema": DRAFT,
        "title": "manifest.json",
        "description": "The manifest of a gold folder, gold_schema_version %s. A key is added or changed only with a "
                       "new GOLD_SCHEMA_VERSION. Generated from meterline_gold/write.py by meterline_gold.contract; "
                       "do not edit by hand. Meaning of each key: the gold spec, section 9.3." % GOLD_SCHEMA_VERSION,
    })


# ---- the datasets (B3) ----------------------------------------------------------------------------------------

_PATTERNS = {
    "count": "^(0|[1-9][0-9]*)$",
    "money": "^-?[0-9]+\\.[0-9]{2,}$",
    "quantity": "^-?(0|[0-9]+(\\.[0-9]+)?)$",
    "ratio": "^-?[0-9]+\\.[0-9]{4}$",
    "date": DATE["pattern"],
    "month": "^[0-9]{4}-[0-9]{2}$",
    "map": "^\\{.*\\}$",
}
DATASET_NOTE = ("One data row of %s, read as an object of column name to cell text; an empty cell is null. The header "
                "row must be exactly the columns of x-columns, in that order. Generated from meterline_gold/write.py "
                "and meterline_gold/dataset_docs.py by meterline_gold.contract; do not edit by hand. Meaning of each "
                "column: docs/reference/gold-datasets.md and the gold spec, sections 6 to 8.")


def _unit(column, kind):
    if column in dataset_docs.UNIT_BY_NAME:
        return dataset_docs.UNIT_BY_NAME[column]
    if column.startswith("tokens_"):
        return "tokens"
    if column in dataset_docs.KEY_COLUMNS:
        return "key"
    return dataset_docs.UNIT_BY_KIND[kind]


def _pattern(column, kind):
    if column == "month":
        return _PATTERNS["month"]
    if kind in ("map_count", "map_money", "json"):
        return _PATTERNS["map"]
    return _PATTERNS.get(kind)


def _description(dataset, column):
    return dataset_docs.OVERRIDES.get((dataset, column)) or dataset_docs.COLUMNS[column]


def dataset_files():
    """[(file stem, write.SCHEMAS name, dataset, audiences)] of the dataset schema files, in a fixed order."""
    return list(dataset_docs.VARIANTS)


def _never_empty(stem, dataset, covered):
    base = set(dataset_docs.NEVER_EMPTY[dataset].split()) - set(covered)
    return base | {"schema_version", "hidden"}


# columns renamed before the first public release (GOLD_SCHEMA_VERSION 2.0): new name -> retired name
RENAMED_COLUMNS = {"provider_account_id": "org_id", "cell_kind": "group_id"}


def _schema_note(names):
    """One sentence, in the description of a schema that has a renamed column, saying so."""
    renamed = ["%s is now %s" % (RENAMED_COLUMNS[c], c) for c in names if c in RENAMED_COLUMNS]
    return (" Retired names: schema_version 2.0 renamed a column (%s)." % "; ".join(renamed)) if renamed else ""


def _dataset_schema(stem, schema_name, dataset, audiences_):
    columns = [("schema_version", "text")] + list(write.SCHEMAS[schema_name])
    names = [c for c, _ in columns]
    covered = dataset_docs.HIDDEN_COVERED.get(stem, ())
    never_empty = _never_empty(stem, dataset, covered)
    properties = {}
    for column, kind in columns:
        cell = {"type": ["string"] if column in never_empty else ["string", "null"]}
        if kind == "bool":
            cell["enum"] = ["true", "false"] + ([] if column in never_empty else [None])
        elif column == "schema_version":
            cell["enum"] = [GOLD_SCHEMA_VERSION]
        else:
            pattern = _pattern(column, kind)
            if pattern:
                cell["pattern"] = pattern
        cell["description"] = ("GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row."
                               if column == "schema_version" else _description(dataset, column))
        cell["x-unit"] = _unit(column, kind)
        cell["x-gold-type"] = kind
        cell["x-hidden-covered"] = column in covered
        properties[column] = cell
    joins = {c: list(t) for c, t in dataset_docs.JOINS.get(dataset, {}).items() if c in names}
    title, grain, cost_rule = dataset_docs.DATASET_TEXT[dataset]
    files = [dataset_docs.FOLDER[a] + dataset + ".csv" for a in audiences_]
    return {
        "$schema": DRAFT,
        "title": "gold-dataset-" + stem,
        "description": DATASET_NOTE % " and ".join(files) + _schema_note(names),
        "type": "object",
        "properties": properties,
        "required": names,
        "additionalProperties": False,
        "x-columns": names,
        "x-dataset": dataset,
        "x-audiences": list(audiences_),
        "x-files": files,
        "x-grain": grain,
        "x-primary-key": list(write.DATASET_KEYS[dataset]),
        "x-joins": joins,
        "x-cost-column-rule": cost_rule,
    }


def dataset_schemas():
    """{file name: schema} of the dataset schema files, one per dataset form an audience folder holds."""
    return {"gold-dataset-%s.schema.json" % stem: _dataset_schema(stem, schema_name, dataset, aud)
            for stem, schema_name, dataset, aud in dataset_files()}


def schema_file(path):
    """The dataset schema file for a CSV path inside a gold folder, such as ``exec/seats_month.csv``, or None."""
    parts = str(path).split("/")
    if len(parts) < 2 or not parts[-1].endswith(".csv"):
        return None
    audience = parts[0]
    dataset = parts[-1][:-len(".csv")]
    for stem, _schema_name, name, audiences_ in dataset_files():
        if name == dataset and audience in audiences_:
            return "gold-dataset-%s.schema.json" % stem
    return None


def audiences_schema():
    """The schema of ``audiences.json``."""
    rule = _obj({
        "pattern": {"type": "string", "minLength": 1},
        "audience": {"enum": ["central", "exec", "manager", "person", "none"]},
        "serve": {"enum": ["audience-only", "central-only", "never"]},
        "contains_person_keys": {"type": "boolean"},
        "hidden_marker": {"anyOf": [{"const": "hidden"}, {"type": "null"}]},
    })
    return dict(_obj({
        "description": {"type": "string"},
        "gold_schema_version": {"const": GOLD_SCHEMA_VERSION},
        "rules": {"type": "array", "minItems": 1, "items": rule},
    }), **{
        "$schema": DRAFT,
        "title": "gold-audiences.json",
        "description": "Which files of a gold folder may be served to which reader (meterline_gold.servable reads "
                       "it). The source is meterline_gold/audiences.json; this schema is generated by "
                       "meterline_gold.contract; do not edit by hand.",
    })


def gold_schemas():
    """{file name: schema} of the gold-side contract files in docs/schemas/."""
    out = {"gold-org-%s.schema.json" % name[:-len(".csv")]: _org_schema(name) for name in ORG_FILES}
    out["gold-manifest.schema.json"] = manifest_schema()
    out["gold-audiences.schema.json"] = audiences_schema()
    out.update(dataset_schemas())
    return out


def dumps(schema):
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_schemas(out_dir):
    """Write every schema and ``gold-audiences.json`` into ``out_dir``; returns the file names."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for name, schema in sorted(gold_schemas().items()):
        (out / name).write_text(dumps(schema), encoding="utf-8", newline="\n")
        names.append(name)
    (out / "gold-audiences.json").write_text(dumps(audiences.load_rules()), encoding="utf-8", newline="\n")
    names.append("gold-audiences.json")
    return names


# ---- the reference page ---------------------------------------------------------------------------------------


def _forms(dataset):
    return [(stem, schema_name, aud) for stem, schema_name, name, aud in dataset_files() if name == dataset]


def _column_table(schema, dataset=None):
    lines = ["| Column | Unit | May be empty | Description |", "|---|---|---|---|"]
    for column, spec in schema["properties"].items():
        empty = "no" if "null" not in spec["type"] else "yes"
        mark = " Covered by `hidden`." if spec["x-hidden-covered"] else ""
        if (dataset, column) in dataset_docs.TRAPS:
            mark += " See [column traps](%s)." % dataset_docs.TRAPS_LINK
        if (dataset, column) in dataset_docs.REFERENCE_SHARE:
            mark += " See [reference unallocated share](%s)." % dataset_docs.REFERENCE_SHARE_LINK
        lines.append("| `%s` | %s | %s | %s%s |" % (column, spec["x-unit"], empty, spec["description"], mark))
    return lines


COMPATIBILITY_LINES = [
    "## Compatibility",
    "",
    "What may change in a gold dataset without breaking a reader, and what a reader must do. The record format has the "
    "same kind of rule ([records, Compatibility](../records.md#compatibility)).",
    "",
    "**Non-breaking** (the minor part of `GOLD_SCHEMA_VERSION` rises):",
    "",
    "- a new dataset;",
    "- a new column, appended after the existing ones (the columns that were there keep their name, type and order);",
    "- a new value in an open list: a text column that holds a status, a reason, a provider or a unit takes values from "
    "a list that may grow.",
    "",
    "**Breaking** (a new major `GOLD_SCHEMA_VERSION`):",
    "",
    "- a column renamed, removed or retyped;",
    "- a dataset removed, or its grain changed (its key or what one row stands for).",
    "",
    "**A reader**:",
    "",
    "- selects columns by name, never by position;",
    "- ignores a column it does not know;",
    "- fails on a column it needs that is missing, naming the column and the dataset (a missing column is not an empty "
    "cell: an empty cell means no value, a missing column means the file is not what the reader expects);",
    "- treats a value it does not know in an open list as \"other\" and does not fail;",
    "- reads `schema_version`, the first column of every file, before the rest: the same major part means this rule "
    "applies, a different major part means the reader stops and says which version it read and which it supports.",
    "",
    "The JSON schemas in `docs/schemas` describe one version exactly (the columns in file order, no others): use them "
    "to check a file of that version, not as a loader that must accept the next one.",
    "",
    "Retired names: `GOLD_SCHEMA_VERSION` 2.0 is the first major version. Before the first public release the column "
    "`org_id` was renamed `provider_account_id` and the column `group_id` was renamed `cell_kind`, and the option "
    "`--org` of `meterline-gold build` became `--org-files`; a gold folder written by the 1.x builds is rebuilt from silver.",
]


def reference_markdown():
    """The page docs/reference/gold-datasets.md."""
    schemas = dataset_schemas()
    out = [
        "# Gold datasets",
        "",
        "Generated from `meterline_gold` by `python -m meterline_gold.contract docs/schemas "
        "docs/reference/gold-datasets.md`; do not edit by hand. A test fails when this page differs from what the code "
        "generates.",
        "",
        "This page lists every dataset a gold build writes, with its audience, grain, key, columns and the rule for "
        "which cost column to sum. The same facts are machine-readable in `docs/schemas/gold-dataset-*.schema.json` "
        "(one schema per file form) and `docs/schemas/gold-audiences.json` (who may be served which file). Every "
        "file is a CSV whose first column is `schema_version`; money is written with at least two decimals, a ratio "
        "with four, and an empty cell means no value. Which reader may be served which file is in the gold package "
        "README, section \"What a build writes\". Three kinds of cost figure appear below and must not be added together: "
        "billed, consumption and reference cost; the cookbook section "
        "[Billed, consumption and reference cost](../how-to/finops-kpis.md#billed-consumption-and-reference-cost) "
        "explains them with a do and a don't.",
        "",
        "Units: `currency` (an amount in the reference currency, or the provider's own where the column says so), "
        "`tokens`, `count`, `ratio`, `date`, `month`, `key` (an id or pseudonymous key, not a quantity), `text` and "
        "`flag` (`true` or `false`).",
        "",
    ] + COMPATIBILITY_LINES
    seen = []
    for _stem, _schema_name, dataset, _aud in dataset_files():
        if dataset in seen:
            continue
        seen.append(dataset)
        forms = _forms(dataset)
        main_stem = forms[0][0]
        main = schemas["gold-dataset-%s.schema.json" % main_stem]
        title, grain, cost_rule = dataset_docs.DATASET_TEXT[dataset]
        out += ["", "## `%s`" % dataset, "", "%s %s" % (title + ".", grain), ""]
        out += ["- **Audiences and files:** " + "; ".join(
            "%s (`%s`)" % (a, dataset_docs.FOLDER[a] + dataset + ".csv") for _, _, aud in forms for a in aud) + ".",
                "- **Key:** " + ", ".join("`%s`" % c for c in main["x-primary-key"]) + ".",
                "- **Schema:** " + ", ".join("`gold-dataset-%s.schema.json`" % s for s, _, _ in forms) + "."]
        if main["x-joins"]:
            out.append("- **Joins:** " + "; ".join(
                "`%s` to %s" % (c, ", ".join("`%s`" % t for t in targets)) for c, targets in main["x-joins"].items()) + ".")
        out += ["", "The columns of the %s form, in file order:" % (forms[0][2][0]), ""]
        out += _column_table(main, dataset)
        for stem2, _sn, aud in forms[1:]:
            form = schemas["gold-dataset-%s.schema.json" % stem2]
            added = [c for c in form["x-columns"] if c not in main["x-columns"]]
            removed = [c for c in main["x-columns"] if c not in form["x-columns"]]
            covered = [c for c, s in form["properties"].items() if s["x-hidden-covered"]]
            bits = []
            if added:
                bits.append("adds " + ", ".join("`%s`" % c for c in added))
            if removed:
                bits.append("leaves out " + ", ".join("`%s`" % c for c in removed))
            if covered:
                bits.append("withholds values in " + ", ".join("`%s`" % c for c in covered) +
                            " and says so in `hidden`")
            out += ["", "The %s form (`gold-dataset-%s.schema.json`) %s." % (
                " and ".join(aud), stem2, "; ".join(bits) if bits else "has the same columns")]
        out += ["", "**Which cost column to sum.** " + cost_rule]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: python -m meterline_gold.contract OUT_DIR [REFERENCE_MD]")
    for written in write_schemas(sys.argv[1]):
        print(written)
    if len(sys.argv) == 3:
        target = Path(sys.argv[2])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(reference_markdown(), encoding="utf-8", newline="\n")
        print(target)
