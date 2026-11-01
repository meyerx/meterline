"""A datetime without a zone is refused (review finding A78).

``validate`` refuses a naive value in any datetime field of any record kind and names the field; before, it was
accepted and ``to_dict`` wrote it shifted by the host's time zone (08:00 naive under Europe/Zurich became 06:00Z).
"""

import os
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

import meterline as ml

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 10, 9, 8, 0)

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
DATETIME_FIELDS = {
    "usage": ["pulled_at", "data_refreshed_at"],
    "activity": ["pulled_at", "data_refreshed_at"],
    "seats": ["pulled_at", "data_refreshed_at"],
    "seat_assignments": ["pulled_at", "data_refreshed_at", "assigned_at", "last_active_at", "last_authenticated_at"],
    "org_totals": ["pulled_at", "data_refreshed_at"],
}
CASES = [(kind, name) for kind, names in DATETIME_FIELDS.items() for name in names]


@pytest.mark.parametrize("kind,name", CASES)
def test_validate_refuses_a_naive_datetime_field(kind, name):
    record = BUILDERS[kind]()
    ml.validate(record)  # the aware record is fine
    naive = replace(record, **{name: NAIVE})
    with pytest.raises(ml.RecordError, match=name):
        ml.validate(naive)


def test_naive_pulled_at_is_not_written_shifted_by_the_host_zone():
    """Under a zone east of UTC a naive 08:00 used to be written as 06:00Z; now it never reaches the writer."""
    if not hasattr(time, "tzset"):
        pytest.skip("no tzset on this platform")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Zurich"
    time.tzset()
    try:
        record = replace(BUILDERS["usage"](), pulled_at=NAIVE)
        with pytest.raises(ml.RecordError, match="pulled_at"):
            ml.validate(record)
        with pytest.raises(ml.RecordError, match="pulled_at"):
            ml.validate_batch([record])
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_aware_datetime_in_another_zone_is_still_normalised_to_utc():
    zone = timezone(timedelta(hours=2))
    record = replace(BUILDERS["usage"](), pulled_at=datetime(2026, 10, 9, 10, 0, tzinfo=zone))
    ml.validate(record)
    assert record.to_dict()["pulled_at"] == "2026-10-09T08:00:00+00:00"


