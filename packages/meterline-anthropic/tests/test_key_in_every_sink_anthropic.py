"""F-20: the API key never reaches any sink: raw pages, records, run manifests,
the state file, logs, stdout, stderr or the text of an exception.

The success path (backfill, a resuming daily, rebuild) and two failing paths
(a 401 whose body echoes the key, a 500 that exhausts the retries) are run with
the fixture key as the canary. The sinks are named, and each must exist, so the
scan cannot pass by finding nothing.
"""

import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from meterline_anthropic.cli import main
from meterline_anthropic.client import AnalyticsClient, ApiError

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 5, 7, 0, 0, tzinfo=timezone.utc)


def _run(transport, clock, data_dir, *argv, now=NOW):
    return main(
        [*argv, "--data-dir", str(data_dir)],
        transport=transport, clock=clock, sleep=clock.sleep, now=lambda: now, jitter=lambda: 0,
    )


def _sinks(data_dir):
    """Every file of the data directory, text as stored (gzip opened), by sink name."""
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


def _assert_no_key(key, data_dir, out, caplog):
    needle = key.encode("utf-8")
    for name, files in _sinks(data_dir).items():
        for path, data in files:
            assert needle not in data, "%s: %s" % (name, path)
    for where, text in (("stdout", out.out), ("stderr", out.err), ("log", caplog.text)):
        assert key not in text, where


def _echoing(key, status):
    def handler(request: httpx.Request) -> httpx.Response:
        seen = request.headers.get("x-api-key", "")
        return httpx.Response(status, json={"error": {"type": "api_error", "message": "rejected key %s" % seen}})

    return httpx.MockTransport(handler)


def test_key_never_reaches_any_sink_on_the_success_path(fake_api, fake_clock, data_dir, fake_key_env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    assert _run(fake_api.transport, fake_clock, data_dir, "backfill", "--since", "2026-09-20") == 0
    assert _run(fake_api.transport, fake_clock, data_dir, "daily", now=LATER) == 0
    assert main(["rebuild", "--data-dir", str(data_dir)]) == 0
    sinks = _sinks(data_dir)
    for name in ("raw pages", "records", "run manifests", "state file"):
        assert sinks[name], "no %s were written, so none could be scanned" % name
    _assert_no_key(fake_key_env, data_dir, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 500])
def test_key_never_reaches_any_sink_on_a_failing_run(status, fake_clock, tmp_path, fake_key_env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    rc = _run(_echoing(fake_key_env, status), fake_clock, tmp_path, "backfill", "--since", "2026-09-20")
    assert rc == (3 if status == 401 else 1)  # a refused key is permanent, a 500 transient
    _assert_no_key(fake_key_env, tmp_path, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_key_never_in_the_text_of_an_api_error(status, fake_clock, fake_key_env):
    client = AnalyticsClient(fake_key_env, transport=_echoing(fake_key_env, status), clock=fake_clock,
                             sleep=fake_clock.sleep, jitter=lambda: 0)
    with pytest.raises(ApiError) as caught:
        client.get_page("usage_report", {"starting_at": "2026-09-01T00:00:00Z"})
    exc = caught.value
    for text in (str(exc), repr(exc), repr(exc.args), json.dumps(exc.body, default=str)):
        assert fake_key_env not in text
