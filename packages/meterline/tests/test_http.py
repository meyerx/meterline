"""F-48: the shared HTTP plumbing (throttle, back-off, Retry-After) has one
behaviour, pinned here once for every connector."""

import email.utils
from decimal import Decimal

import pytest

from meterline import http


class Clock:
    def __init__(self, now=1_000_000):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)


# ---- back-off -----------------------------------------------------------------


@pytest.mark.parametrize("attempt, low, high", [(0, 1, 1), (1, 1, 2), (2, 2, 4), (3, 4, 8), (6, 30, 60), (40, 30, 60)])
def test_backoff_doubles_is_capped_and_scaled_by_the_jitter(attempt, low, high):
    assert http.backoff_seconds(attempt, lambda: 0) == low
    assert http.backoff_seconds(attempt, lambda: 0.999) == high


@pytest.mark.parametrize("jitter", [-5, 7])
def test_backoff_clamps_a_jitter_outside_zero_to_one(jitter):
    assert 1 <= http.backoff_seconds(3, lambda: jitter) <= 8


# ---- Retry-After ----------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    (None, None), ("", None), ("garbage", None), ("NaN", None), ("Infinity", None), ("-Infinity", None),
    ("0", 0), ("-3", 0), ("-1e999999999", 0), ("5", 5), (" 5 ", 5), ("1.5", 2), ("120", 120),
    ("99999999", 300), ("1e999999999", 300),
])
def test_retry_after_reads_delay_seconds(raw, expected):
    assert http.retry_after_seconds(raw, wall=Clock()) == expected


def test_retry_after_reads_an_http_date_against_the_wall_clock():
    clock = Clock()
    assert http.retry_after_seconds(email.utils.formatdate(clock() + 120, usegmt=True), wall=clock) == 120
    assert http.retry_after_seconds(email.utils.formatdate(clock() - 3600, usegmt=True), wall=clock) == 0
    assert http.retry_after_seconds(email.utils.formatdate(clock() + 86400 * 30, usegmt=True), wall=clock) == 300


def test_retry_after_cap_is_configurable():
    assert http.retry_after_seconds("99999999", wall=Clock(), cap=7) == 7
    assert http.retry_after_seconds("6", wall=Clock(), cap=7) == 6


def test_retry_after_a_number_never_reaches_float():
    assert http.header_number(" 1.25 ") == Decimal("1.25")
    assert http.header_number("x") is None
    assert http.header_number(None) is None


# ---- the wait before a retry and the retryable statuses ---------------------------


@pytest.mark.parametrize("backoff, server, expected", [(1, None, 1), (4, 1, 4), (1, 17, 17), (2, 0, 2)])
def test_retry_wait_is_never_sooner_than_the_backoff_or_the_server_asks(backoff, server, expected):
    assert http.retry_wait(backoff, server) == expected


@pytest.mark.parametrize("status, expected", [(429, True), (500, True), (503, True), (400, False), (403, False), (404, False), (410, False)])
def test_retryable_statuses(status, expected):
    assert http.is_retryable(status) is expected


# ---- throttle -----------------------------------------------------------------------


@pytest.mark.parametrize("rate, window", [(3, 60), (2, 3600)])
def test_throttle_never_lets_more_than_rate_requests_into_a_closed_window(rate, window):
    clock = Clock()
    throttle = http.Throttle(rate, window, clock=clock, sleep=clock.sleep)
    times = []
    for _ in range(4 * rate + 1):
        throttle.wait()
        times.append(clock())
    for t in times:
        assert len([u for u in times if t <= u <= t + window]) <= rate
    assert clock.sleeps


def test_throttle_needs_a_positive_rate():
    with pytest.raises(ValueError):
        http.Throttle(0, 60, clock=Clock(), sleep=Clock().sleep)


# ---- secrets and URLs -----------------------------------------------------------------


def test_mask_replaces_every_secret_in_nested_values():
    body = {"error": {"message": "bad key k-1 and t-2"}, "k-1": ["k-1", 3]}
    assert http.mask(body, ["k-1", "t-2"]) == {"error": {"message": "bad key *** and ***"}, "***": ["***", 3]}
    assert http.mask("", ["k-1"]) == ""
    assert http.mask("x", ["", None]) == "x"


def test_without_userinfo_drops_the_credentials_of_a_url():
    url = "https://user:pw@example.com/v1/x?a=1#f"
    assert http.without_userinfo(url) == "https://example.com/v1/x?a=1#f"
    assert http.without_userinfo(url, keep_query=False, keep_fragment=False) == "https://example.com/v1/x"
