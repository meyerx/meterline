"""F-41: a Retry-After wait is capped (300 seconds unless configured), both
forms of RFC 9110 are read, and a value that is not finite is ignored instead
of crashing the run.
"""

import email.utils

import httpx
import pytest

from meterline_copilot.testing import user_row
from copilot_helpers import NOW, backfill
from meterline_copilot.client import CopilotClient


PATH = "/orgs/example-org/copilot/billing"


def _first(status, value):
    seen = {"n": 0}

    def handler(request):
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(status, headers={"retry-after": value}, json={"message": "slow down"})
        return httpx.Response(200, json={"seat_breakdown": {}})

    return httpx.MockTransport(handler)


def _wait(fake_clock, value, status=429, **kw):
    with CopilotClient("t-test", transport=_first(status, value), clock=fake_clock, sleep=fake_clock.sleep,
                       wall=fake_clock, jitter=lambda: 0, **kw) as client:
        client.get_page(PATH)
    return fake_clock.sleeps[0]



def _in_child(script, seconds=10):
    """Run a script in a child interpreter: a huge exponent can hold the GIL in C,
    so only a separate process can be timed out. Returns (finished, stdout)."""
    import subprocess
    import sys
    try:
        done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        return False, ""
    assert done.returncode == 0, done.stderr
    return True, done.stdout.strip()


_CHILD_CLOCK = """
class Clock:
    def __init__(self): self.now = 1000000.0; self.sleeps = []
    def __call__(self): return self.now
    def sleep(self, s): self.sleeps.append(s); self.now += max(s, 0)
clock = Clock()
"""


def _child(headers):
    script = _CHILD_CLOCK + 'from meterline_copilot.client import CopilotClient\nimport httpx\n{clock_src}\nn = [0]\ndef handler(request):\n    n[0] += 1\n    if n[0] == 1:\n        return httpx.Response(429, headers=@@H@@, json={"message": "x"})\n    return httpx.Response(200, json={"seat_breakdown": {}})\nT = httpx.MockTransport(handler)\ntry:\n    with CopilotClient("t-test", transport=T, clock=clock, sleep=clock.sleep, wall=clock, jitter=lambda: 0) as client:\n        client.get_page("/orgs/example-org/copilot/billing")\n    print(clock.sleeps[0])\nexcept Exception as error:\n    print(type(error).__name__)\n'.replace("{clock_src}", "").replace("@@H@@", repr(headers))
    return _in_child(script)


@pytest.mark.parametrize("value", ["-1e999999999", "1e999999999"])
def test_retry_after_cap_negative_huge_copilot(value):
    finished, out = _child({"retry-after": value})
    assert finished, "a huge exponent in retry-after hangs the rounding"
    assert 1 <= int(out) <= 300  # the back-off decides


@pytest.mark.parametrize("value, expected", [("1e999999999", "ApiError"), ("-1e999999999", "1")])
def test_retry_after_cap_reset_header_huge_copilot(value, expected):
    finished, out = _child({"x-ratelimit-reset": value})
    assert finished, "a huge exponent in x-ratelimit-reset hangs the rounding"
    assert out == expected  # beyond the 7,200 s cap is refused; far in the past retries at once


@pytest.mark.parametrize("status", [429, 403])
def test_retry_after_cap_limits_a_huge_wait(fake_clock, status):
    assert _wait(fake_clock, "99999999", status) == 300


def test_retry_after_cap_below_the_cap_is_honoured(fake_clock):
    assert _wait(fake_clock, "120") == 120


def test_retry_after_cap_is_configurable(fake_clock):
    assert _wait(fake_clock, "99999999", max_retry_after=7) == 7


@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN", "inf", "garbage", "", "-5", "1e999999999"])
def test_retry_after_cap_survives_values_that_are_not_usable(fake_clock, value):
    assert 1 <= _wait(fake_clock, value) <= 300


def test_retry_after_cap_reads_an_http_date(fake_clock):
    assert _wait(fake_clock, email.utils.formatdate(fake_clock() + 120, usegmt=True)) == 120


def test_retry_after_cap_caps_an_http_date_far_ahead(fake_clock):
    assert _wait(fake_clock, email.utils.formatdate(fake_clock() + 86400 * 30, usegmt=True)) == 300


def test_retry_after_cap_ignores_an_http_date_in_the_past(fake_clock):
    assert _wait(fake_clock, email.utils.formatdate(fake_clock() - 3600, usegmt=True)) == 1


def test_retry_after_cap_option_on_the_command_line(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.fail("/copilot/billing/seats", 429, times=1, headers={"retry-after": "99999999"})
    api.users["2026-09-18"] = [user_row("2026-09-18", 1, "login1", level=level)]
    assert backfill(data_dir, api, fake_clock, "2026-09-18", NOW, "--max-retry-after", "9") in (0, 1, 2)
    assert 9 in fake_clock.sleeps
