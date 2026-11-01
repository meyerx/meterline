import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from meterline import UsageRecord, read_jsonl, write_jsonl, write_parquet

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def rec(actor, day, **over):
    base = dict(
        provider="anthropic", provider_account_id="org-1", grain="day", period_start=day, period_end=day,
        actor_kind="user", actor_id=actor, product="chat", model="model-a",
        tokens_input_uncached=1, tokens_output=2, requests=None,
        quantities={"ai_credit": Decimal("12.5")}, cost=Decimal("412.800000"),
        currency="USD", cost_basis="consumption", extras={"web_search_requests": 1},
        is_final=False, pulled_at=T0, data_refreshed_at=T0, batch_id="b1",
    )
    base.update(over)
    return UsageRecord(**base)


def test_jsonl_is_sorted_deterministic_and_roundtrips(tmp_path):
    a = rec("user-002", date(2026, 9, 1))
    b = rec("user-001", date(2026, 9, 2), actor_email="café@example.com")
    c = rec("user-001", date(2026, 9, 1))
    p1 = tmp_path / "one.jsonl"
    p2 = tmp_path / "two.jsonl"
    assert write_jsonl([a, b, c], p1) == 3
    write_jsonl([c, a, b], p2)
    assert p1.read_bytes() == p2.read_bytes()
    raw = p1.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    assert "café".encode("utf-8") in raw  # ensure_ascii=False
    parsed = [json.loads(x) for x in raw.decode("utf-8").splitlines()]
    assert [(p["actor_id"], p["period_start"]) for p in parsed] == [
        ("user-001", "2026-09-01"), ("user-002", "2026-09-01"), ("user-001", "2026-09-02"),
    ]
    assert parsed[0]["cost"] == "412.80" and parsed[0]["quantities"] == {"ai_credit": "12.5"}
    assert list(parsed[0]) == sorted(parsed[0])
    assert read_jsonl(p1, UsageRecord) == [c, a, b]


def test_jsonl_reads_floats_as_decimal(tmp_path):
    d = rec("user-001", date(2026, 9, 1)).to_dict()
    text = json.dumps(d).replace('"cost": "412.80"', '"cost": 412.8')
    path = tmp_path / "f.jsonl"
    path.write_text(text + "\n", encoding="utf-8")
    assert read_jsonl(path, UsageRecord)[0].cost == Decimal("412.8")


def test_jsonl_creates_parent_dirs(tmp_path):
    target = tmp_path / "records" / "usage" / "b1.jsonl"
    write_jsonl([rec("user-001", date(2026, 9, 1))], target)
    assert target.exists()


def test_parquet_without_pyarrow_names_the_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(RuntimeError, match="parquet"):
        write_parquet([rec("user-001", date(2026, 9, 1))], tmp_path / "x.parquet")


def test_parquet_roundtrip_with_maps(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    p = tmp_path / "x.parquet"
    write_parquet([rec("user-001", date(2026, 9, 1))], p)
    table = pq.read_table(str(p))
    assert table.num_rows == 1
    assert table.column("cost").to_pylist() == [Decimal("412.80")]
    assert table.column("quantities").to_pylist() == [[("ai_credit", Decimal("12.5"))]]
    assert table.column("extras").to_pylist() == [[("web_search_requests", Decimal("1"))]]


def test_parquet_null_refresh_and_cost_only(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    p = tmp_path / "y.parquet"
    write_parquet([rec("user-001", date(2026, 9, 1), data_refreshed_at=None, cost_only=True)], p)
    row = pq.read_table(str(p)).to_pylist()[0]
    assert row["data_refreshed_at"] is None
    assert row["cost_only"] is True
    assert row["tokens_reasoning"] is None
