"""A10 and A11 (spec section 8): the key reaches no sink; is_final turns true 48 hours after the bucket ended."""

import gzip
import json
import logging
from pathlib import Path

import httpx
import pytest

from admin_helpers import D1, NOW, backfill, basic_cost, basic_usage, daily, run, usage_by_key
from admin_helpers import BASE_URL, FAKE_KEY
from meterline_anthropic_admin.testing import USAGE_PATH, utc
from meterline_anthropic_admin.client import AdminClient, ApiError


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
    for name, files in _sinks(data_dir).items():
        for path, data in files:
            assert FAKE_KEY.encode("utf-8") not in data, "%s: %s" % (name, path)
    for where, text in (("stdout", out.out), ("stderr", out.err), ("log", caplog.text)):
        assert FAKE_KEY not in text, where


def _echoing(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"type": "error", "error": {"type": "x", "message": "rejected key %s" % request.headers.get("x-api-key")}})

    return httpx.MockTransport(handler)


def test_done_check_A10_the_key_never_reaches_any_sink_on_the_success_path(make_api, fake_clock, data_dir, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    api = make_api(basic_usage(), basic_cost())
    assert backfill(data_dir, api, fake_clock) == 0
    assert daily(data_dir, api, fake_clock, utc("2026-09-20T07:00:00")) == 0
    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 0
    sinks = _sinks(data_dir)
    for name in ("raw pages", "records", "run manifests", "state file"):
        assert sinks[name], "no %s were written, so none could be scanned" % name
    _assert_no_key(data_dir, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 500])
def test_done_check_A10_the_key_never_reaches_any_sink_on_a_failing_run(status, make_api, fake_clock, data_dir, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    api = make_api(basic_usage(), basic_cost())
    api.transport = _echoing(status)
    assert backfill(data_dir, api, fake_clock) == (3 if status == 401 else 1)
    _assert_no_key(data_dir, capsys.readouterr(), caplog)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_done_check_A10_the_key_is_not_in_the_text_of_an_api_error(status, fake_clock):
    client = AdminClient(FAKE_KEY, base_url=BASE_URL, transport=_echoing(status), clock=fake_clock, sleep=fake_clock.sleep, jitter=lambda: 0)
    with pytest.raises(ApiError) as caught:
        client.get_page(USAGE_PATH, {"starting_at": "2026-09-15T00:00:00Z"})
    exc = caught.value
    for text in (str(exc), repr(exc), repr(exc.args), json.dumps(exc.body, default=str)):
        assert FAKE_KEY not in text


def test_done_check_A10_the_key_comes_from_the_environment_only(monkeypatch, make_api, fake_clock, data_dir, capsys):
    monkeypatch.delenv("ANTHROPIC_ADMIN_KEY")
    assert backfill(data_dir, make_api(basic_usage()), fake_clock) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_ADMIN_KEY" in err and FAKE_KEY not in err
    source = "".join(p.read_text(encoding="utf-8") for p in (Path(__file__).resolve().parents[1] / "src").rglob("*.py")).lower()
    assert "find-generic-password" not in source and "keyring" not in source and "environ" in source


@pytest.mark.parametrize("hours, final", [(47, False), (48, True), (49, True)])
def test_done_check_A11_is_final_turns_true_48_hours_after_the_bucket_ended(make_api, fake_clock, tmp_path, hours, final):
    # D1 ends at 2026-09-16T00:00:00Z
    from datetime import timedelta

    pulled = utc("2026-09-16T00:00:00") + timedelta(hours=hours)
    out = tmp_path / ("h%d" % hours)
    assert backfill(out, make_api(basic_usage(), basic_cost()), fake_clock, "2026-09-15", pulled) == 0
    assert usage_by_key(out)[("apikey_a", D1, "model-a")].is_final is final


def test_done_check_A11_a_day_not_final_is_pulled_again_and_the_latest_batch_wins(make_api, fake_clock, data_dir):
    from admin_helpers import latest

    api = make_api(basic_usage() + [], basic_cost())
    assert daily(data_dir, api, fake_clock, utc("2026-09-17T12:00:00"), "--since", "2026-09-14") == 0
    first = usage_by_key(data_dir)[("apikey_a", D1, "model-a")]
    assert first.is_final is False  # 36 hours after the bucket ended
    api.usage_rows[0]["uncached"] = 1111  # late data
    assert daily(data_dir, api, fake_clock, utc("2026-09-19T01:00:00")) == 0
    new = latest(data_dir, "usage")
    key = next(k for k, r in new.items() if r.period_start.isoformat() == D1 and r.actor_id == "apikey_a" and r.model == "model-a")
    assert new[key].tokens_input_uncached == 1111 and new[key].is_final is True
    assert new[key].batch_id > first.batch_id
