"""F-48: the three connector clients behave the same where the providers do not
differ: the same Retry-After reading and capping, the same wait before a retry,
and the same throttle window rule. One suite runs every scenario against every
installed connector, so the copies cannot drift apart again.

A connector that is not installed is skipped (the core package can be tested
on its own)."""

import email.utils
import importlib

import pytest

httpx = pytest.importorskip("httpx")


class Clock:
    def __init__(self, now=1_000_000):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)


def _anthropic(transport, clock, rate, **kw):
    mod = importlib.import_module("meterline_anthropic.client")
    client = mod.AnalyticsClient("k-test", transport=transport, clock=clock, sleep=clock.sleep, wall=clock,
                                 rate_per_minute=rate or 10_000, **kw)
    return client, lambda: client.get_page("usage_report", {}), 60


def _litellm(transport, clock, rate, **kw):
    mod = importlib.import_module("meterline_litellm.client")
    client = mod.LitellmClient("k-test", base_url="https://proxy.example.com", transport=transport, clock=clock,
                               sleep=clock.sleep, wall=clock, rate_per_minute=rate or 10_000, **kw)
    return client, lambda: client.get_page("user/daily/activity", {}), 60


def _copilot(transport, clock, rate, **kw):
    mod = importlib.import_module("meterline_copilot.client")
    client = mod.CopilotClient("t-test", transport=transport, clock=clock, sleep=clock.sleep, wall=clock,
                               rate_per_hour=rate or 100_000, **kw)
    return client, lambda: client.get_page("/orgs/example-org/copilot/billing"), 3600


CONNECTORS = {"anthropic": _anthropic, "litellm": _litellm, "copilot": _copilot}


@pytest.fixture(params=sorted(CONNECTORS))
def connector(request):
    pytest.importorskip("meterline_%s" % request.param)
    return CONNECTORS[request.param]


def _scripted(responses):
    """A transport answering the scripted (status, headers) pairs, then 200."""
    queue = list(responses)

    def handler(request):
        if queue:
            status, headers = queue.pop(0)
            return httpx.Response(status, headers=headers, json={"message": "slow down"})
        return httpx.Response(200, json={"data": [], "has_more": False, "next_page": None})

    return httpx.MockTransport(handler)


def _sleeps(connector, responses, **kw):
    clock = Clock()
    client, get, _ = connector(_scripted(responses), clock, None, jitter=lambda: 0.999, **kw)
    with client:
        get()
    return clock.sleeps


def test_a_retry_waits_the_larger_of_the_backoff_and_retry_after(connector):
    # back-off with full jitter is 1, 2, 4; the server asks for 3 each time
    assert _sleeps(connector, [(429, {"retry-after": "3"})] * 3) == [3, 3, 4]


@pytest.mark.parametrize("value", ["Infinity", "NaN", "garbage", "", "-5", "-1e999999999"])
def test_an_unusable_or_past_retry_after_leaves_the_backoff(connector, value):
    assert _sleeps(connector, [(429, {"retry-after": value})] * 2) == [1, 2]


@pytest.mark.parametrize("value, expected", [("99999999", 300), ("1e999999999", 300), ("120", 120)])
def test_a_retry_after_is_capped_at_the_same_limit(connector, value, expected):
    assert _sleeps(connector, [(429, {"retry-after": value})]) == [expected]


def test_the_cap_is_configurable_the_same_way(connector):
    assert _sleeps(connector, [(503, {"retry-after": "99999999"})], max_retry_after=7) == [7]


def test_an_http_date_is_read_against_the_wall_clock(connector):
    when = email.utils.formatdate(Clock()() + 120, usegmt=True)
    assert _sleeps(connector, [(429, {"retry-after": when})]) == [120]


def test_a_5xx_without_retry_after_waits_the_backoff(connector):
    assert _sleeps(connector, [(500, {}), (502, {}), (503, {})]) == [1, 2, 4]


def test_the_throttle_counts_a_request_exactly_one_window_old(connector):
    clock = Clock()
    client, get, window = connector(_scripted([]), clock, 2, jitter=lambda: 0)
    times = []
    with client:
        for _ in range(5):
            get()
            times.append(clock())
    for t in times:
        assert len([u for u in times if t <= u <= t + window]) <= 2
