"""The small accessors ``meterline-job`` reads to compose its published folder: the Parquet writer that starts from text,
the map of file forms and the privacy parameters of a policy file. All three are internal-stable."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import meterline_gold
from meterline_gold import dataset_docs, dataset_forms, policy_privacy, write, write_parquet_text

EXAMPLES = Path(meterline_gold.__file__).resolve().parent / "examples"
pq = pytest.importorskip("pyarrow.parquet")


def _values(kind, column):
    return {"text": column + "-x", "date": date(2026, 4, 1), "count": 3, "money": Decimal("10.5"), "quantity": Decimal("2"),
            "ratio": Decimal("0.5"), "bool": True, "map_count": {"a": 1}, "map_money": {"a": Decimal("1.5")},
            "json": '{"a":1}', "ntext": "5", "dq": "x", "nx": "x"}[kind]


@pytest.mark.parametrize("schema_name", ["person_month", "exec_costs_month", "groups_month_marked", "pools_month", "comparison"])
def test_parquet_from_text_equals_parquet_from_values(tmp_path, schema_name):
    schema = write.SCHEMAS[schema_name]
    values = [tuple(None if c in ("budget_crossed_on", "hidden") else _values(k, c) for c, k in schema)]
    columns = [c for c, _ in schema]
    write.write_parquet(tmp_path / "a.parquet", schema_name, columns, values)
    header, rows = write.format_rows(schema_name, columns, values)
    write_parquet_text(tmp_path / "b.parquet", schema_name, header, rows)
    one, two = pq.read_table(tmp_path / "a.parquet"), pq.read_table(tmp_path / "b.parquet")
    assert one.schema.equals(two.schema) and one.equals(two)
    assert any(str(f.type).startswith("decimal128(38, 12)") for f in two.schema) or schema_name == "comparison"


def test_parquet_from_text_refuses_a_header_that_is_not_the_schema(tmp_path):
    with pytest.raises(ValueError, match="the header is"):
        write_parquet_text(tmp_path / "x.parquet", "seats_month", ["schema_version", "month"], [])


def test_dataset_forms_cover_every_variant_and_name_the_keys():
    forms = dataset_forms()
    assert {a for a, _ in forms} == {"central", "exec", "manager", "person"}
    for _stem, schema_name, dataset, audiences in dataset_docs.VARIANTS:
        for audience in audiences:
            if audience != "delivery":
                assert forms[(audience, dataset)] == (schema_name, tuple(write.DATASET_KEYS[dataset]))
    assert forms[("exec", "groups_month")][0] == "groups_month_marked" and forms[("person", "person_month")][0] == "person_month_person"


def test_policy_privacy_reads_k_and_the_exec_depth_and_the_hash_a_build_records(tmp_path):
    digest, params = policy_privacy(EXAMPLES / "policy.example.yaml")
    assert params == {"k": 5, "exec_max_depth": 2} and len(digest) == 64
    with pytest.raises(meterline_gold.InputError):
        policy_privacy(tmp_path / "missing.yaml")
