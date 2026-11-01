"""Unit tests of the v2 record types (the done-checks are in test_done_check_format.py)."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from meterline import (
    ActivityRecord,
    OrgTotal,
    RecordError,
    SeatAssignment,
    SeatSnapshot,
    UsageRecord,
    actor_cost_failures,
    amount_str,
    batch_failures,
    convert_v1,
    grain_for,
    latest_per_key,
    quantity_str,
    validate,
)

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 9)


def make_usage(**over):
    base = dict(
        provider="anthropic", provider_account_id="org-1", grain="day",
        period_start=date(2026, 9, 15), period_end=date(2026, 9, 15),
        actor_kind="user", actor_id="user-001", actor_email="a.b@example.com",
        product="claude_code", model="model-a",
        tokens_input_uncached=10, tokens_cache_read=5, tokens_cache_write=1,
        tokens_cache_write_5m=1, tokens_cache_write_1h=0, tokens_input_total=16,
        tokens_output=20, requests=3, cost=Decimal("412.80"), cost_list=Decimal("450.00"),
        currency="USD", cost_basis="consumption", is_final=False,
        pulled_at=T0, data_refreshed_at=T0, batch_id="2026-10-09T080000Z",
    )
    base.update(over)
    return UsageRecord(**base)


def make_org_total(**over):
    base = dict(
        provider="anthropic", provider_account_id="org-1", grain="day",
        period_start=date(2026, 9, 15), period_end=date(2026, 9, 15),
        tokens_output=2, cost=Decimal("1.5"), currency="USD", cost_basis="billed",
        is_final=True, pulled_at=T0, batch_id="b1",
    )
    base.update(over)
    return OrgTotal(**base)


def test_amount_and_quantity_str():
    assert amount_str(Decimal("412.800000")) == "412.80"
    assert amount_str(Decimal("5")) == "5.00"
    assert amount_str(Decimal("0.00012345")) == "0.00012345"
    assert amount_str(Decimal("1E+3")) == "1000.00"
    assert quantity_str(Decimal("12.50")) == "12.5"
    assert quantity_str(Decimal("100.0")) == "100"
    assert quantity_str(Decimal("1E+3")) == "1000"
    assert quantity_str(Decimal("0.00")) == "0"
    assert quantity_str(7) == "7"


def test_usage_roundtrip_serialisation_and_maps():
    rec = make_usage(
        cost=Decimal("412.800000"), quantities={"ai_credit": Decimal("12.50")},
        extras={"web_search_requests": 3, "flat_cost": Decimal("0.10")},
    )
    d = rec.to_dict()
    assert d["cost"] == "412.80" and d["period_start"] == "2026-09-15"
    assert d["pulled_at"] == "2026-10-09T08:00:00+00:00"
    assert d["quantities"] == {"ai_credit": "12.5"}
    assert d["extras"] == {"flat_cost": "0.1", "web_search_requests": 3}
    back = UsageRecord.from_dict(d)
    assert back.cost == Decimal("412.80") and isinstance(back.cost, Decimal)
    assert back.quantities == {"ai_credit": Decimal("12.5")}
    assert back.extras == rec.extras
    assert back.tokens_reasoning is None


def test_from_dict_rejects_v1_line_wrong_version_and_missing_field():
    d = make_usage().to_dict()
    no_version = {k: v for k, v in d.items() if k != "record_version"}
    with pytest.raises(RecordError, match="convert_v1"):
        UsageRecord.from_dict(no_version)
    with pytest.raises(RecordError):
        UsageRecord.from_dict(dict(d, record_version=3))
    missing = dict(d)
    del missing["model"]
    with pytest.raises(RecordError):
        UsageRecord.from_dict(missing)
    with pytest.raises(RecordError):
        UsageRecord.from_dict(dict(d, extras={"x": "not a number"}))
    with pytest.raises(RecordError):
        UsageRecord.from_dict(dict(d, extras={"x": True}))


def test_timestamps_z_suffix_and_naive():
    d = make_usage().to_dict()
    d["pulled_at"] = "2026-10-09T08:00:00Z"
    assert UsageRecord.from_dict(d).pulled_at == T0
    d["pulled_at"] = "2026-10-09T08:00:00"
    with pytest.raises(RecordError):
        UsageRecord.from_dict(d)


def test_keys():
    assert make_usage().key() == (
        "anthropic", "org-1", "", "day", date(2026, 9, 15), "user", "user-001", "claude_code", "model-a",
    )
    act = ActivityRecord(
        provider="anthropic", provider_account_id="org-1", grain="day", period_start=date(2026, 9, 15),
        period_end=date(2026, 9, 15), actor_kind="user", actor_id="user-001", product="chat",
        counters={"conversations": 1}, pulled_at=T0, batch_id="b1",
    )
    assert act.key() == ("anthropic", "org-1", "", "day", date(2026, 9, 15), "user", "user-001", "chat")
    seat = SeatSnapshot(
        provider="p", provider_account_id="o", period_start=date(2026, 9, 15), period_end=date(2026, 9, 15),
        seats=5, pulled_at=T0, batch_id="b1",
    )
    assert seat.key() == ("p", "o", "", date(2026, 9, 15))
    assert SeatSnapshot.from_dict(seat.to_dict()) == seat
    assert ActivityRecord.from_dict(act.to_dict()) == act
    assert make_org_total().key()[-2:] == ("", "")


def test_seat_assignment_roundtrip_and_validation():
    rec = SeatAssignment(
        provider="github_copilot", provider_account_id="org:acme", period_start=date(2026, 10, 9),
        period_end=date(2026, 10, 9), actor_id="1", actor_login="octo",
        pending_cancellation_date=date(2026, 11, 1), pulled_at=T0, batch_id="b1",
    )
    assert SeatAssignment.from_dict(rec.to_dict()) == rec
    validate(rec, TODAY)
    for bad in (
        SeatAssignment(**{**rec.__dict__, "period_start": date(2026, 10, 8), "period_end": date(2026, 10, 8)}),
        SeatAssignment(**{**rec.__dict__, "actor_kind": "api_key"}),
    ):
        with pytest.raises(RecordError):
            validate(bad, TODAY)


def test_validate_defaults_today_to_pulled_at():
    validate(make_usage())
    with pytest.raises(RecordError):
        validate(make_usage(period_start=date(2026, 10, 10), period_end=date(2026, 10, 10)))


@pytest.mark.parametrize(
    "over",
    [
        dict(period_start=date(2026, 10, 10), period_end=date(2026, 10, 10)),
        dict(period_end=date(2026, 9, 14)),
        dict(tokens_output=-1),
        dict(cost=Decimal("-0.01")),
        dict(cost=Decimal("NaN")),
        dict(extras={"x": -1}),
        dict(quantities={"ai_credit": Decimal("-1")}),
        dict(extras={"Bad Key": 1}),
        dict(grain="week"),
        dict(cost=1.5),
        dict(extras={"x": 1.5}),
        dict(actor_kind="robot"),
        dict(actor_kind="unassigned", actor_id="someone"),
        dict(actor_id=""),
        dict(actor_email="A.B@example.com"),
        dict(provider_account_id=""),
    ],
)
def test_validate_rejects(over):
    with pytest.raises(RecordError):
        validate(make_usage(**over), TODAY)


def test_validate_accepts_unassigned_and_null_cost():
    validate(make_usage(actor_kind="unassigned", actor_id="(unassigned)", actor_email=None), TODAY)
    validate(make_usage(cost=None, cost_list=None, currency="", cost_basis=None, requests=None), TODAY)
    validate(make_usage(cost=None, cost_list=Decimal("2"), cost_basis="estimate"), TODAY)


def test_grain_for():
    assert grain_for(date(2026, 9, 1), date(2026, 9, 1)) == "day"
    assert grain_for(date(2026, 2, 1), date(2026, 2, 28)) == "month"
    assert grain_for(date(2026, 9, 1), date(2026, 9, 29)) == "window"
    assert grain_for(date(2026, 9, 2), date(2026, 9, 30)) == "window"


def test_org_total_mixing_all_models_and_per_model_rows_fails():
    ok = [make_org_total(model="m1"), make_org_total(model="m2")]
    assert batch_failures(ok, TODAY) == []
    mixed = ok + [make_org_total(model="")]
    assert any("mix" in f for f in batch_failures(mixed, TODAY))
    other_period = ok + [make_org_total(model="", period_start=date(2026, 9, 16), period_end=date(2026, 9, 16))]
    assert batch_failures(other_period, TODAY) == []


def test_actor_cost_failures_scope_and_end_users():
    org = [make_org_total(cost=Decimal("10"), cost_basis="consumption")]
    under = [make_usage(cost=Decimal("4"), model="a"), make_usage(cost=Decimal("5"), model="b")]
    assert actor_cost_failures(under, org) == []
    over = [make_usage(cost=Decimal("6"), model="a"), make_usage(cost=Decimal("5"), model="b")]
    assert actor_cost_failures(over, org)
    # another basis or an end_user record is never compared
    assert actor_cost_failures([make_usage(cost=Decimal("50"), cost_basis="estimate")], org) == []
    assert actor_cost_failures([make_usage(cost=Decimal("50"), actor_kind="end_user")], org) == []


def test_latest_per_key_prefers_latest_batch():
    old = make_usage(batch_id="2026-10-05T000000Z", tokens_output=1, tokens_input_total=16)
    new = make_usage(batch_id="2026-11-05T000000Z", tokens_output=2, tokens_input_total=16)
    other = make_usage(batch_id="2026-10-05T000000Z", actor_id="user-002")
    for order in ([old, new, other], [new, old, other]):
        got = latest_per_key(order)
        assert len(got) == 2
        assert got[new.key()].tokens_output == 2
        assert got[other.key()] == other


def test_convert_v1_rejects_v2_and_unknown_kind():
    with pytest.raises(RecordError):
        convert_v1(make_usage().to_dict(), "usage")
    with pytest.raises(RecordError):
        convert_v1({}, "seat_assignments")
    with pytest.raises(RecordError):
        convert_v1({"provider": "anthropic"}, "usage")


def test_int_quantity_serialises_as_string_and_counters_must_be_integers():
    assert make_usage(quantities={"ai_credit": 3}).to_dict()["quantities"] == {"ai_credit": "3"}
    act = ActivityRecord(
        provider="p", provider_account_id="o", grain="day", period_start=date(2026, 9, 15),
        period_end=date(2026, 9, 15), actor_kind="user", actor_id="u", product="x",
        counters={"n": Decimal("1.5")}, pulled_at=T0, batch_id="b",
    )
    with pytest.raises(RecordError):
        validate(act, TODAY)
