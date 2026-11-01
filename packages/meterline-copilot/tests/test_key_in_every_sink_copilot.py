"""F-20: neither GitHub token reaches any sink: raw pages, records, run
manifests, the state file, logs, stdout, stderr or the text of an exception.

The success path (backfill, a resuming daily, rebuild) and two failing paths
(a 401 whose body echoes the credential, a 500 that exhausts the retries) are
run with the fixture tokens as the canaries. The sinks are named, and each
must exist, so the scan cannot pass by finding nothing.
"""

import gzip
import json
import logging
from pathlib import Path

import httpx
import pytest

from meterline_copilot.testing import aggregate_row, item, seat, user_row
from copilot_helpers import FAKE_BILLING_TOKEN, FAKE_TOKEN, at, backfill, daily, run
from meterline_copilot.client import ApiError, CopilotClient

DAY = "2026-09-18"
NEEDLES = (FAKE_TOKEN, FAKE_BILLING_TOKEN)


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


def _assert_no_token(data_dir, out, caplog):
    for needle in NEEDLES:
        for name, files in _sinks(data_dir).items():
            for path, data in files:
                assert needle.encode("utf-8") not in data, "%s: %s" % (name, path)
        for where, text in (("stdout", out.out), ("stderr", out.err), ("log", caplog.text)):
            assert needle not in text, where


def _echoing(status):
    def handler(request: httpx.Request) -> httpx.Response:
        seen = request.headers.get("authorization", "")
        return httpx.Response(status, json={"message": "rejected credential %s" % seen})

    return httpx.MockTransport(handler)


def test_key_never_reaches_any_sink_on_the_success_path(make_api, fake_clock, data_dir, level, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, cli=True, app=True)]
    api.aggregates[DAY] = aggregate_row(DAY, 3, 5, 7)
    api.billing_day[DAY] = [item()]
    api.billing_user[("login1", 2026, 9)] = [item()]
    api.seats = [seat("login1", 1)]
    assert backfill(data_dir, api, fake_clock, since=DAY) == 0
    assert daily(data_dir, api, fake_clock, at("2026-09-20T07:00:00")) == 0
    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 0
    sinks = _sinks(data_dir)
    for name in ("raw pages", "records", "run manifests", "state file"):
        assert sinks[name], "no %s were written, so none could be scanned" % name
    _assert_no_token(data_dir, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 500])
def test_key_never_reaches_any_sink_on_a_failing_run(status, make_api, fake_clock, data_dir, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    api = make_api()
    api.transport = _echoing(status)
    rc = backfill(data_dir, api, fake_clock, since=DAY)
    assert rc == (3 if status == 401 else 1)
    _assert_no_token(data_dir, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 404, 500])
def test_key_never_in_the_text_of_an_api_error(status, fake_clock):
    client = CopilotClient(FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, transport=_echoing(status),
                           clock=fake_clock, sleep=fake_clock.sleep, wall=fake_clock, jitter=lambda: 0)
    for billing in (False, True):
        with pytest.raises(ApiError) as caught:
            client.get_page("/orgs/example-org/copilot/billing", billing=billing)
        exc = caught.value
        for text in (str(exc), repr(exc), repr(exc.args), json.dumps(exc.body, default=str)):
            for needle in NEEDLES:
                assert needle not in text
