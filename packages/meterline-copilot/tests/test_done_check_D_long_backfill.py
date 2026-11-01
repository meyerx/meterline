"""Improvements 1 D: a long Copilot backfill against the fake does not wait on the real clock.

The client throttles to ``rate_per_hour`` requests in any hour. With the real clock and a ``sleep`` that does nothing, a
throttle that is full spins for the rest of the hour; with ``FakeClock`` the sleep moves the clock, so the wait costs no time.
"""

import time
from datetime import date, datetime, timezone

from meterline.http import FakeClock
from meterline_copilot import run
from meterline_copilot.testing import SAMPLE_TOKEN, FakeAPI
from meterline_copilot.testing import FakeClock as ExportedFakeClock

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
SINCE = date(2026, 5, 18)  # 120 days before the last closed day


def _backfill(tmp_path, fake, **hooks):
    started = time.monotonic()
    result = run("backfill", since=SINCE, level="organization", slug="my-org", data_dir=tmp_path / "out",
                 credentials={"COPILOT_TOKEN": SAMPLE_TOKEN}, transport=fake.transport, now=lambda: NOW,
                 jitter=lambda: 0, **hooks)
    return result, time.monotonic() - started


def test_a_120_day_backfill_with_a_no_op_sleep_finishes_in_under_10_seconds(tmp_path):
    fake = FakeAPI()
    result, seconds = _backfill(tmp_path, fake, sleep=lambda s: None)
    assert result.exit_code == 0, result
    assert seconds < 10, "%.1f s" % seconds
    assert len(fake.api_requests()) > 100  # a real backfill: a request per day at least


def test_the_throttle_waits_through_the_fake_clock_not_the_real_one(tmp_path):
    fake = FakeAPI()
    clock = FakeClock()
    result, seconds = _backfill(tmp_path, fake, clock=clock, sleep=clock.sleep, wall=clock, rate_per_hour=60)
    assert result.exit_code == 0, result
    assert seconds < 10, "%.1f s" % seconds  # 60 requests an hour for 300+ requests is hours of waiting, on the fake clock
    assert clock.now - 1_000_000 >= 3600 and clock.sleeps  # the waits happened, on the injected clock


def test_the_fake_clock_is_exported_by_the_testing_module():
    assert ExportedFakeClock is FakeClock
