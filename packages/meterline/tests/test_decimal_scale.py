"""MF-2: one decimal scale for the whole record format, owned by the core.

A money or quantity figure keeps at most ``MAX_DECIMALS`` (12) decimals: ``validate`` refuses more, and the Parquet
columns are ``decimal128(38, 12)``, so a record that validates always writes. Before this, validate accepted any
number of decimals while Parquet failed above 10 (an uncaught ArrowInvalid) and the gold build refused above 12.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from meterline import MAX_DECIMALS, RecordError, UsageRecord, validate, write_parquet

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 9)


def make(**over):
    base = dict(
        provider="litellm", provider_account_id="proxy-one", grain="day", period_start=date(2026, 9, 15), period_end=date(2026, 9, 15),
        actor_kind="proxy_user", actor_id="user-a", product="", model="model-a", tokens_input_total=0, tokens_output=1,
        quantities={"ai_credit": Decimal("12.5")}, cost=Decimal("0.5"), currency="USD", cost_basis="estimate",
        extras={"flat_cost": Decimal("0.25")}, is_final=False, pulled_at=T0, data_refreshed_at=None, batch_id="b1",
    )
    base.update(over)
    return UsageRecord(**base)


def test_the_scale_is_twelve():
    assert MAX_DECIMALS == 12


@pytest.mark.parametrize(
    "over",
    [
        dict(cost=Decimal("0.0027199999999999998")),  # 19 decimals, a LiteLLM float spend
        dict(cost=Decimal("1.0000000000001")),  # 13
        dict(cost_list=Decimal("0.0000000000001")),
        dict(quantities={"ai_credit": Decimal("0.0000000000001")}),
        dict(extras={"flat_cost": Decimal("1.0000000000001")}),
    ],
)
def test_validate_rejects_more_than_twelve_decimals(over):
    with pytest.raises(RecordError, match="decimals"):
        validate(make(**over), TODAY)


@pytest.mark.parametrize(
    "over",
    [
        dict(cost=Decimal("1.000000000001")),  # exactly 12 decimals
        dict(cost=Decimal("0.500000000000000000")),  # trailing zeros past the twelfth carry no digit
        dict(cost=Decimal("1E+3")),
        dict(cost=Decimal("0")),
    ],
)
def test_validate_accepts_twelve_decimals_and_trailing_zeros(over):
    validate(make(**over), TODAY)


def test_parquet_column_types_pinned(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    pa = pytest.importorskip("pyarrow")
    path = tmp_path / "x.parquet"
    write_parquet([make(cost=Decimal("0.123456789012"), cost_list=Decimal("1.000000000001"))], path)
    schema = pq.read_schema(str(path))
    money = pa.decimal128(38, 12)
    scalars = {f.name: str(f.type) for f in schema if not pa.types.is_map(f.type)}
    maps = {f.name: (f.type.key_type, f.type.item_type) for f in schema if pa.types.is_map(f.type)}
    assert scalars == {
        **{name: "int64" for name in (
            "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write", "tokens_cache_write_5m", "tokens_cache_write_1h",
            "tokens_input_total", "tokens_output", "tokens_reasoning", "requests", "record_version")},
        "cost": str(money), "cost_list": str(money),
        **{name: "string" for name in (
            "currency", "cost_basis", "provider", "provider_account_id", "scope", "grain", "actor_kind", "actor_id", "actor_email",
            "actor_login", "product", "model", "model_provider", "batch_id")},
        "period_start": "date32[day]", "period_end": "date32[day]",
        **{name: "bool" for name in ("actor_deleted", "is_final", "cost_only")},
        "pulled_at": "timestamp[us, tz=UTC]", "data_refreshed_at": "timestamp[us, tz=UTC]",
    }
    assert maps == {"quantities": (pa.string(), money), "extras": (pa.string(), money)}
    # the digits survive the round trip
    row = pq.read_table(str(path)).to_pylist()[0]
    assert row["cost"] == Decimal("0.123456789012") and row["cost_list"] == Decimal("1.000000000001")
