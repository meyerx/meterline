"""F-41: a Retry-After wait is capped (300 seconds unless configured), both
forms of RFC 9110 are read, and a value that is not finite is ignored instead
of crashing the run.
"""

import email.utils

import httpx
import pytest

from litellm_helpers import NOW, backfill, basic_rows, users
from meterline_litellm.client import LitellmClient


def _first_429(value):
    seen = {"n": 0}

    def handler(request):
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(429, headers={"retry-after": value}, json={"error": "slow down"})
        return httpx.Response(200, json={"results": []})

    return httpx.MockTransport(handler)


def _wait(fake_clock, value, **kw):
    kw.setdefault("wall", fake_clock)
    with LitellmClient("sk-test", base_url="https://proxy.example.com", transport=_first_429(value),
                       clock=fake_clock, sleep=fake_clock.sleep, jitter=lambda: 0, **kw) as client:
        client.get_page("user/daily/activity", {})
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
    script = _CHILD_CLOCK + 'from meterline_litellm.client import LitellmClient\nimport httpx\n{clock_src}\nn = [0]\ndef handler(request):\n    n[0] += 1\n    if n[0] == 1:\n        return httpx.Response(429, headers=@@H@@, json={"error": "x"})\n    return httpx.Response(200, json={"results": []})\nT = httpx.MockTransport(handler)\ntry:\n    with LitellmClient("sk-test", base_url="https://proxy.example.com", transport=T, clock=clock, sleep=clock.sleep, wall=clock, jitter=lambda: 0) as client:\n        client.get_page("user/daily/activity", {})\n    print(clock.sleeps[0])\nexcept Exception as error:\n    print(type(error).__name__)\n'.replace("{clock_src}", "").replace("@@H@@", repr(headers))
    return _in_child(script)


@pytest.mark.parametrize("value", ["-1e999999999", "1e999999999"])
def test_retry_after_cap_negative_huge_litellm(value):
    finished, out = _child({"retry-after": value})
    assert finished, "a huge exponent in retry-after hangs the rounding"
    assert 1 <= int(out) <= 300  # the back-off decides


def test_retry_after_cap_limits_a_huge_wait(fake_clock):
    assert _wait(fake_clock, "99999999") == 300


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


def test_retry_after_cap_option_on_the_command_line(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a"))
    proxy.fail("/user/daily/activity", 429, times=1, retry_after=99999999)
    assert backfill(data_dir, proxy, fake_clock, "2026-09-14", NOW, "--max-retry-after", "9") in (0, 1, 2)
    assert fake_clock.sleeps[0] == 9
