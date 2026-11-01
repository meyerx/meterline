"""Integration base B3: every gold dataset has a machine-readable schema, generated and drift-tested.

- a build of the ``tiny`` preset (seed 7) and the hand-built fixture are validated row by row against the schemas,
  every file of every audience folder;
- the committed schemas and the reference page equal what the code generates;
- every column of every dataset has a description and a unit; columns and types come from ``write.SCHEMAS``;
- ``meterline-gold schemas`` and ``python -m meterline_gold.contract`` write the dataset schemas.

Synthetic data only.
"""

import csv
import json
import re
from pathlib import Path

import pytest

from gold_helpers import KEY

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "docs" / "schemas"
REFERENCE = ROOT / "docs" / "reference" / "gold-datasets.md"
REGENERATE = "regenerate with: python -m meterline_gold.contract docs/schemas docs/reference/gold-datasets.md"
UNITS = {"currency", "tokens", "count", "ratio", "date", "month", "key", "text", "flag"}


@pytest.fixture(scope="module")
def tiny_gold(tmp_path_factory):
    import meterline_gold
    meterline_sim = pytest.importorskip("meterline_sim")

    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("b3_sim"))
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    out = tmp_path_factory.mktemp("b3_gold") / "gold"
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY, out=out)
    return out


def _validator(schema):
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)


def _rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], [{c: (v if v != "" else None) for c, v in zip(rows[0], r)} for r in rows[1:]]


def row_problems(folder):
    """Every problem found validating each CSV file under ``folder`` against its dataset schema."""
    from meterline_gold import contract

    schemas = contract.dataset_schemas()
    problems, seen = [], 0
    for path in sorted(Path(folder).rglob("*.csv")):
        rel = path.relative_to(folder).as_posix()
        name = contract.schema_file(rel)
        if name is None:
            problems.append("%s: no schema" % rel)
            continue
        schema = schemas[name]
        header, rows = _rows(path)
        if header != schema["x-columns"]:
            problems.append("%s: header %s is not %s" % (rel, header, schema["x-columns"]))
            continue
        validator = _validator(schema)
        for n, row in enumerate(rows, start=2):
            for error in validator.iter_errors(row):
                problems.append("%s line %d: %s" % (rel, n, error.message[:160]))
        seen += 1
    assert seen, "no CSV file found under " + str(folder)
    return problems


# ---- done-check B3 ------------------------------------------------------------------------------------------


def test_done_check_B3_tiny_build_validates_row_by_row_for_every_audience(tiny_gold):
    folders = {p.relative_to(tiny_gold).parts[0] for p in tiny_gold.rglob("*.csv")}
    assert folders == {"central", "delivery", "exec", "manager", "person"}
    assert row_problems(tiny_gold) == []


def test_done_check_B3_fixture_build_validates_row_by_row(fixture_out):
    assert row_problems(fixture_out) == []


def test_done_check_B3_a_corrupted_cell_is_caught(tiny_gold, tmp_path):
    # negative control: a planted defect in a copy of the build
    import shutil

    copy = tmp_path / "gold"
    shutil.copytree(tiny_gold, copy)
    target = copy / "central" / "person_month.csv"
    text = target.read_text(encoding="utf-8")
    header, first, rest = text.split("\n", 2)
    columns = header.split(",")
    cells = first.split(",")
    cells[columns.index("month")] = "2026-13x"
    cells[columns.index("cache_rate")] = "1.5"
    target.write_text("\n".join([header, ",".join(cells), rest]), encoding="utf-8")
    found = row_problems(copy)
    assert any("person_month.csv line 2" in p for p in found), found


def test_done_check_B3_committed_schemas_and_reference_equal_the_generated_ones():
    from meterline_gold import contract

    generated = contract.dataset_schemas()
    committed = sorted(p.name for p in SCHEMAS.glob("gold-dataset-*.schema.json"))
    assert committed == sorted(generated), REGENERATE
    for name, schema in generated.items():
        assert (SCHEMAS / name).read_text(encoding="utf-8") == contract.dumps(schema), name + ": " + REGENERATE
    assert REFERENCE.read_text(encoding="utf-8") == contract.reference_markdown(), REGENERATE


def test_done_check_B3_drift_is_caught(tmp_path):
    # negative control: a stale schema file and a stale page differ from the generated text
    from meterline_gold import contract

    name, schema = next(iter(sorted(contract.dataset_schemas().items())))
    stale = dict(schema, description=schema["description"] + " (edited)")
    assert contract.dumps(stale) != contract.dumps(schema)
    assert contract.reference_markdown().replace("## ", "## x", 1) != contract.reference_markdown()


def schema_problems(schemas):
    problems = []
    for name, schema in schemas.items():
        for column, spec in schema["properties"].items():
            if not str(spec.get("description", "")).strip():
                problems.append("%s.%s: no description" % (name, column))
            if spec.get("x-unit") not in UNITS:
                problems.append("%s.%s: unit %r" % (name, column, spec.get("x-unit")))
    return problems


def test_done_check_B3_every_column_has_a_description_and_a_unit():
    from meterline_gold import contract

    assert schema_problems(contract.dataset_schemas()) == []


def test_done_check_B3_a_missing_description_or_unit_is_caught():
    # negative control: the same predicate on a planted gap
    planted = {"x": {"properties": {"a": {"x-unit": "count", "description": " "}, "b": {"description": "d"}}}}
    assert schema_problems(planted) == ["x.a: no description", "x.b: unit None"]


def test_columns_and_types_come_from_write_schemas():
    from meterline_gold import GOLD_SCHEMA_VERSION, contract, dataset_docs, write

    generated = contract.dataset_schemas()
    assert len(generated) == len(dataset_docs.VARIANTS)
    for stem, schema_name, dataset, _audiences in dataset_docs.VARIANTS:
        schema = generated["gold-dataset-%s.schema.json" % stem]
        expected = [c for c, _ in write.SCHEMAS[schema_name]]
        assert schema["x-columns"] == ["schema_version"] + expected
        assert list(schema["properties"]) == schema["x-columns"] == schema["required"]
        for column, kind in write.SCHEMAS[schema_name]:
            assert schema["properties"][column]["x-gold-type"] == kind, (stem, column)
        assert schema["properties"]["schema_version"]["enum"] == [GOLD_SCHEMA_VERSION]


def test_every_write_column_is_described():
    from meterline_gold import dataset_docs, write

    missing = sorted({c for sch in write.SCHEMAS.values() for c, _ in sch}
                     - set(dataset_docs.COLUMNS) - {c for _, c in dataset_docs.OVERRIDES})
    assert missing == []
    assert set(write.SCHEMAS) == {v[1] for v in dataset_docs.VARIANTS}


def test_primary_keys_and_joins_name_real_columns():
    from meterline_gold import contract, write

    generated = contract.dataset_schemas()
    columns_of = {s["x-dataset"]: set(s["x-columns"]) for s in generated.values()}
    for name, schema in generated.items():
        assert schema["x-primary-key"] == list(write.DATASET_KEYS[schema["x-dataset"]]), name
        assert set(schema["x-primary-key"]) <= set(schema["x-columns"]), name
        for column, targets in schema["x-joins"].items():
            assert column in schema["x-columns"], (name, column)
            for target in targets:
                dataset, _, col = target.partition(".")
                assert col in columns_of[dataset], (name, target)
        assert schema["x-audiences"], name


def test_the_hidden_marker_covers_the_columns_of_the_spec():
    from meterline_gold import contract

    generated = contract.dataset_schemas()
    covered = {name: sorted(c for c, s in sch["properties"].items() if s["x-hidden-covered"])
               for name, sch in generated.items()}
    assert covered["gold-dataset-exec-costs_month.schema.json"] == sorted(
        ["usage_cost_source", "cost_source_basis", "usage_cost_ref", "total_cost_ref"])
    assert covered["gold-dataset-exec-seats_month.schema.json"] == ["active_seats", "idle_seats"]
    assert covered["gold-dataset-exec-scenario_summary.schema.json"] == ["persons_stopped"]
    assert covered["gold-dataset-comparison.schema.json"] == ["comparison_band"]
    assert covered["gold-dataset-person_day.schema.json"] == []


def test_the_reference_page_has_a_section_and_a_cost_rule_per_dataset():
    from meterline_gold import contract, dataset_docs

    text = contract.reference_markdown()
    datasets = {v[2] for v in dataset_docs.VARIANTS}
    for dataset in datasets:
        assert re.search(r"^## `%s`$" % dataset, text, re.M), dataset
    assert text.count("**Which cost column to sum.**") == len(datasets)
    assert "../how-to/finops-kpis.md#billed-consumption-and-reference-cost" in text  # the cookbook section B7 writes


def test_the_cli_and_the_module_write_the_dataset_schemas(tmp_path):
    from meterline_gold import cli, contract

    by_cli, by_module = tmp_path / "cli", tmp_path / "module"
    assert cli.main(["schemas", "--out", str(by_cli)]) == 0
    contract.write_schemas(by_module)
    expected = sorted(contract.dataset_schemas())
    for folder in (by_cli, by_module):
        assert sorted(p.name for p in folder.glob("gold-dataset-*.schema.json")) == expected
        for name in expected:
            assert (folder / name).read_text(encoding="utf-8") == contract.dumps(contract.dataset_schemas()[name])
    assert (by_cli / "policy.schema.json").is_file()  # what the command wrote before is still written
