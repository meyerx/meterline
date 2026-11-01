"""Record values are checked, not coerced (review finding A77).

- an int field takes an int only and a bool field a bool only, in ``validate`` and in ``from_dict`` (a string, a
  float or a boolean in an int field, and a string in a bool field, used to be turned into a plausible wrong value).
"""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

import meterline as ml

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 9)

COMMON = dict(provider="p", provider_account_id="o", period_start=DAY, period_end=DAY, pulled_at=T0, batch_id="b1")
BUILDERS = {
    "usage": lambda: ml.UsageRecord(
        **COMMON, grain="day", actor_kind="user", actor_id="u1", product="x", model="m", is_final=True),
    "activity": lambda: ml.ActivityRecord(**COMMON, grain="day", actor_kind="user", actor_id="u1", product="x"),
    "seats": lambda: ml.SeatSnapshot(**COMMON),
    "seat_assignments": lambda: ml.SeatAssignment(**COMMON, actor_id="u1"),
    "org_totals": lambda: ml.OrgTotal(**COMMON, grain="day", is_final=True),
}


@pytest.mark.parametrize("bad", ["3", 1.5, 3.0, True])
def test_validate_refuses_a_non_int_in_an_int_field(bad):
    record = replace(BUILDERS["usage"](), requests=bad)
    with pytest.raises(ml.RecordError, match="requests"):
        ml.validate(record)


@pytest.mark.parametrize("kind,name", [("activity", "sessions"), ("seats", "seats"), ("org_totals", "tokens_output")])
def test_validate_refuses_a_string_in_an_int_field_of_every_kind(kind, name):
    record = replace(BUILDERS[kind](), **{name: "3"})
    with pytest.raises(ml.RecordError, match=name):
        ml.validate(record)


def test_validate_refuses_the_wrong_type_in_a_bool_field():
    for bad in ("false", 1, 0, "True"):
        record = replace(BUILDERS["usage"](), is_final=bad)
        with pytest.raises(ml.RecordError, match="is_final"):
            ml.validate(record)
    for bad in ("no", 1):
        record = replace(BUILDERS["usage"](), cost_only=bad)
        with pytest.raises(ml.RecordError, match="cost_only"):
            ml.validate(record)
    record = replace(BUILDERS["usage"](), actor_deleted="yes")
    with pytest.raises(ml.RecordError, match="actor_deleted"):
        ml.validate(record)


def test_validate_still_accepts_real_ints_and_bools():
    record = replace(BUILDERS["usage"](), requests=3, is_final=False, cost_only=True, actor_deleted=False)
    ml.validate(record)


def _line(kind="usage", **over):
    data = BUILDERS[kind]().to_dict()
    data.update(over)
    return data


@pytest.mark.parametrize("bad", [1.9, "3", True, 2.0])
def test_from_dict_refuses_coerced_int_field(bad):
    with pytest.raises(ml.RecordError, match="requests") as info:
        ml.UsageRecord.from_dict(_line(requests=bad))
    assert type(bad).__name__ in str(info.value)


@pytest.mark.parametrize("bad", ["false", "true", 1, 0, ""])
def test_from_dict_refuses_coerced_bool_field(bad):
    with pytest.raises(ml.RecordError, match="is_final") as info:
        ml.UsageRecord.from_dict(_line(is_final=bad))
    assert type(bad).__name__ in str(info.value)


def test_from_dict_bool_field_nullable_takes_null_and_bool_only():
    assert ml.UsageRecord.from_dict(_line(actor_deleted=None)).actor_deleted is None
    assert ml.UsageRecord.from_dict(_line(actor_deleted=False)).actor_deleted is False
    with pytest.raises(ml.RecordError, match="actor_deleted"):
        ml.UsageRecord.from_dict(_line(actor_deleted="false"))


def test_from_dict_int_field_in_every_kind_takes_int_only():
    for kind, name in [("activity", "sessions"), ("seats", "seats"), ("org_totals", "tokens_output")]:
        cls = ml.KINDS[kind]
        assert getattr(cls.from_dict(_line(kind, **{name: 7})), name) == 7
        with pytest.raises(ml.RecordError, match=name):
            cls.from_dict(_line(kind, **{name: 7.5}))
