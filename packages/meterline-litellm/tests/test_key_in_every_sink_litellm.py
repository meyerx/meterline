"""F-20: the proxy key never reaches any sink: raw pages, records, run
manifests, the state file, logs, stdout, stderr or the text of an exception.

The success path (backfill, a resuming daily, rebuild) and two failing paths
(a 401 whose body echoes the key, a 500 that exhausts the retries) are run with
the fixture key as the canary. The sinks are named, and each must exist, so the
scan cannot pass by finding nothing.
"""

import gzip
import json
import logging
from pathlib import Path

import httpx
import pytest

from litellm_helpers import at, backfill, basic_rows, daily, run, users
from meterline_litellm.client import ApiError, LitellmClient

FAKE_KEY = "sk-synthetic-not-a-real-key"  # the key tests/conftest.py puts in the environment
BASE_URL = "https://proxy.example.com"


def _sinks(data_dir):
    found = {"raw pages": [], "records": [], "run manifests": [], "state file": [], "other": []}
    for path in sorted(Path(data_dir).rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            data = gzip.decompress(data)
        top = path.relative_to(data_dir).parts[0]
        name = {"raw": "raw pages", "records": "records", "runs": "run manifests", "state.json": "state file"}.get(top, "other")
        found[name].append((path, data))
    return found


def _assert_no_key(data_dir, out, caplog):
    needle = FAKE_KEY.encode("utf-8")
    for name, files in _sinks(data_dir).items():
        for path, data in files:
            assert needle not in data, "%s: %s" % (name, path)
    for where, text in (("stdout", out.out), ("stderr", out.err), ("log", caplog.text)):
        assert FAKE_KEY not in text, where


def _echoing(status):
    def handler(request: httpx.Request) -> httpx.Response:
        seen = request.headers.get("authorization", "")
        return httpx.Response(status, json={"error": {"message": "rejected credential %s" % seen}})

    return httpx.MockTransport(handler)


def test_key_never_reaches_any_sink_on_the_success_path(make_proxy, fake_clock, data_dir, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert backfill(data_dir, proxy, fake_clock) == 0
    assert daily(data_dir, proxy, fake_clock, at("2026-09-20T07:00:00")) == 0
    assert run(["rebuild", "--data-dir", str(data_dir)], proxy, fake_clock) == 0
    sinks = _sinks(data_dir)
    for name in ("raw pages", "records", "run manifests", "state file"):
        assert sinks[name], "no %s were written, so none could be scanned" % name
    _assert_no_key(data_dir, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 500])
def test_key_never_reaches_any_sink_on_a_failing_run(status, make_proxy, fake_clock, data_dir, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    proxy = make_proxy(basic_rows(), users=users("user-a"))
    proxy.transport = _echoing(status)
    rc = backfill(data_dir, proxy, fake_clock)
    assert rc == (3 if status == 401 else 1)
    _assert_no_key(data_dir, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_key_never_in_the_text_of_an_api_error(status, fake_clock):
    client = LitellmClient(FAKE_KEY, base_url=BASE_URL, transport=_echoing(status), clock=fake_clock,
                           sleep=fake_clock.sleep, jitter=lambda: 0)
    with pytest.raises(ApiError) as caught:
        client.get_page("user/daily/activity", {"start_date": "2026-09-01"})
    exc = caught.value
    for text in (str(exc), repr(exc), repr(exc.args), json.dumps(exc.body, default=str)):
        assert FAKE_KEY not in text
