"""Unit tests for the client, the fetcher helpers, the normaliser edges and the
command line (resume, rebuild, configuration errors, no leaks)."""

import gzip
import json
import logging
from datetime import date
from decimal import Decimal

import httpx
import pytest

from meterline_litellm.testing import PATH_A, PATH_B, PATH_C, row
from litellm_helpers import (
    D1, NOW, at, backfill, basic_rows, daily, latest, manifests, records, run, users,
)

from meterline_litellm.client import ApiError, LitellmClient
from meterline_litellm.fetch import backward_windows, forward_windows


def _client(proxy, clock, key, **kw):
    kw.setdefault("jitter", lambda: 0)
    return LitellmClient(key, base_url="https://proxy.example.com", transport=proxy.transport,
                         clock=clock, sleep=clock.sleep, **kw)


def test_windows_are_at_most_31_days_and_cover_the_range():
    fw = forward_windows(date(2026, 8, 1), date(2026, 9, 20))
    assert fw == [(date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 20))]
    bw = backward_windows(date(2026, 8, 1), date(2026, 9, 20))
    assert bw == [(date(2026, 8, 21), date(2026, 9, 20)), (date(2026, 8, 1), date(2026, 8, 20))]
    assert backward_windows(date(2026, 9, 20), date(2026, 9, 20)) == [(date(2026, 9, 20), date(2026, 9, 20))]


def test_client_sends_bearer_key_and_masks_it(make_proxy, fake_clock, fake_env):
    proxy = make_proxy(basic_rows())
    with _client(proxy, fake_clock, fake_env) as c:
        c.get("user/daily/activity", {"start_date": D1, "end_date": D1})
    assert proxy.log[0].authorization == "Bearer " + fake_env
    with _client(proxy, fake_clock, "wrong-key") as c:
        with pytest.raises(ApiError) as err:
            c.get("user/daily/activity", {"start_date": D1, "end_date": D1})
    assert err.value.status == 401
    assert "wrong-key" not in str(err.value)


def test_client_retries_429_and_5xx_and_honours_retry_after(make_proxy, fake_clock, fake_env):
    proxy = make_proxy(basic_rows())
    proxy.fail(PATH_A, 429, retry_after=17)
    proxy.fail(PATH_A, 503)
    with _client(proxy, fake_clock, fake_env) as c:
        body = c.get("user/daily/activity", {"start_date": D1, "end_date": D1})
    assert body["results"]
    statuses = [e.status for e in proxy.log]
    assert statuses == [429, 503, 200]
    assert proxy.log[1].time - proxy.log[0].time >= 17


def test_client_gives_up_after_max_tries(make_proxy, fake_clock, fake_env):
    proxy = make_proxy(basic_rows())
    proxy.fail(PATH_A, 500, times=99)
    with _client(proxy, fake_clock, fake_env, max_tries=3) as c:
        with pytest.raises(ApiError) as err:
            c.get("user/daily/activity", {"start_date": D1, "end_date": D1})
    assert err.value.status == 500
    assert len(proxy.log) == 3


def test_client_throttle_never_exceeds_the_rate(make_proxy, fake_clock, fake_env):
    proxy = make_proxy(basic_rows())
    with _client(proxy, fake_clock, fake_env, rate_per_minute=30) as c:
        for _ in range(100):
            c.get("user/daily/activity", {"start_date": D1, "end_date": D1})
    times = [e.time for e in proxy.log]
    for t in times:
        assert len([u for u in times if t <= u <= t + 60]) <= 30
    assert fake_clock.sleeps


def test_missing_key_or_url_is_a_configuration_error(make_proxy, fake_clock, data_dir, monkeypatch, capsys):
    proxy = make_proxy(basic_rows())
    monkeypatch.delenv("LITELLM_API_KEY")
    assert daily(data_dir, proxy, fake_clock) == 2
    assert "LITELLM_API_KEY" in capsys.readouterr().err
    assert proxy.log == []


def test_a_failed_call_resumes_without_repeating_finished_calls(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    proxy.fail(PATH_A, 500, times=99)  # every call fails: the first (global) one
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 1
    assert not (data_dir / "records").exists()
    proxy._faults.clear()
    # the global call succeeds; the per-user calls fail until the retries run out
    orig = proxy._handle

    def flaky(request):
        if request.url.path.endswith(PATH_A) and "user_id" in request.url.params:
            return httpx.Response(500, json={"error": "boom"})
        return orig(request)

    proxy.transport = httpx.MockTransport(flaky)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 1
    globals_before = [e for e in proxy.requests_for(PATH_A) if "user_id" not in e.params and e.status == 200]
    assert len(globals_before) == 1
    pending = json.loads((data_dir / "state.json").read_text())["pending"]
    assert any(k.startswith("global:") for k in pending["steps"])
    proxy.transport = httpx.MockTransport(orig)  # the proxy recovers
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    globals_after = [e for e in proxy.requests_for(PATH_A) if "user_id" not in e.params and e.status == 200]
    assert len(globals_after) == 1  # the finished global call was reused
    assert len(records(data_dir, "usage")) == 4
    assert json.loads((data_dir / "state.json").read_text())["pending"] is None


def test_rebuild_reproduces_published_records_byte_for_byte(make_proxy, fake_clock, data_dir):
    rows = basic_rows() + [row("2026-09-19", "user-a", "model-a", prompt_tokens=80, api_requests=1, spend=Decimal("0.11"))]
    proxy = make_proxy(rows, users=users("user-a", "user-b"))
    assert daily(data_dir, proxy, fake_clock, NOW, "--since", "2026-09-01") == 0
    rows[-1]["spend"] = Decimal("0.25")
    assert daily(data_dir, proxy, fake_clock, at("2026-09-20T09:00:00")) == 0
    before = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    assert len(before) == 4  # usage and org_totals, two batches each
    from meterline_litellm.cli import main
    assert main(["rebuild", "--data-dir", str(data_dir)]) == 0
    after = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    assert before == after


def test_logs_and_files_carry_no_user_ids_or_emails_or_key(make_proxy, fake_clock, data_dir, caplog, fake_env):
    caplog.set_level(logging.DEBUG)
    proxy = make_proxy(basic_rows(), users=[{"user_id": "ada@example.com", "user_email": "ada@example.com"}])
    rows = [dict(r, user_id="ada@example.com") for r in basic_rows()]
    proxy.rows = rows
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "ada@example.com" not in logged
    for folder in ("runs",):
        for p in (data_dir / folder).rglob("*"):
            if p.is_file():
                assert "ada@example.com" not in p.read_text(encoding="utf-8")
    for p in data_dir.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            if p.name.endswith(".gz"):
                data = gzip.decompress(data)
            assert fake_env.encode() not in data
    # file names carry a hash of the id, never the id
    assert not any("ada" in str(p) for p in (data_dir / "raw").rglob("*"))


def test_a_negative_difference_fails_the_run(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    extra = row(D1, "user-b", "model-a", prompt_tokens=999, api_requests=1, spend=Decimal("1"))
    real_rows = proxy.rows
    # the global call sees the original rows, the per-user call for user-b sees one more
    handle = proxy._handle

    def tricky(request):
        if request.url.params.get("user_id") == "user-b":
            proxy.rows = real_rows + [extra]
        else:
            proxy.rows = real_rows
        return handle(request)

    proxy.transport = httpx.MockTransport(tricky)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 2
    assert "nothing published" in capsys.readouterr().err
    assert manifests(data_dir)[-1]["status"] == "failed"
    assert not (data_dir / "records").exists()


def test_modelless_usage_is_kept_under_an_empty_model(make_proxy, fake_clock, data_dir):
    rows = [
        row(D1, "user-a", "model-a", prompt_tokens=100, api_requests=1, spend=Decimal("0.5")),
        row(D1, "user-a", None, prompt_tokens=7, api_requests=1, spend=Decimal("0.01")),
        row(D1, None, None, prompt_tokens=3, api_requests=1, spend=Decimal("0.02")),
    ]
    proxy = make_proxy(rows, users=users("user-a"))
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    got = {(r.actor_kind, r.model): r for r in records(data_dir, "usage")}
    assert set(got) == {("proxy_user", "model-a"), ("proxy_user", ""), ("unassigned", "")}
    assert got[("proxy_user", "")].tokens_input_total == 7
    assert got[("unassigned", "")].cost == Decimal("0.02")
    assert manifests(data_dir)[-1]["modelless_days"] == [D1]
    # the organization total has the per-model row only (spec 3.6)
    assert [(r.model, r.tokens_input_total) for r in records(data_dir, "org_totals")] == [("model-a", 100)]


def test_missing_metric_fails_the_run(make_proxy, fake_clock, data_dir, capsys):
    """Spec 5.2 once said a missing metric reads as null; F-06 changed that: a
    supported proxy (v1.67.0-stable or later) sends all nine, so an absent one
    is a broken answer and nothing is published."""
    rows = [row(D1, "user-a", "model-a", prompt_tokens=100, completion_tokens=5, api_requests=1, spend=Decimal("0.5"))]
    proxy = make_proxy(rows, users=users("user-a"), omit_metrics=("cache_read_input_tokens",))
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 2
    assert "cache_read_input_tokens is missing" in capsys.readouterr().err
    assert records(data_dir, "usage") == []


def test_flat_cost_is_an_extra_only_when_non_zero(make_proxy, fake_clock, data_dir):
    rows = [row(D1, "user-a", "model-a", prompt_tokens=1, api_requests=1, spend=Decimal("0.5"), flat_cost=Decimal("0.25"))]
    proxy = make_proxy(rows, users=users("user-a"))
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    rec = records(data_dir, "usage")[0]
    assert rec.extras["flat_cost"] == Decimal("0.25")
    assert rec.extras["successful_requests"] == 0


def test_finality_needs_two_full_days(make_proxy, fake_clock, data_dir):
    rows = [row(d, "user-a", "model-a", prompt_tokens=1, api_requests=1, spend=Decimal("0.5"))
            for d in ("2026-09-16", "2026-09-17", "2026-09-18")]
    proxy = make_proxy(rows, users=users("user-a"))
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-16", now=at("2026-09-20T00:00:00")) == 0
    final = {r.period_start.isoformat(): r.is_final for r in records(data_dir, "usage")}
    assert final == {"2026-09-16": True, "2026-09-17": True, "2026-09-18": False}


def test_end_user_ids_with_a_comma_are_not_called(make_proxy, fake_clock, data_dir):
    from meterline_litellm.testing import end_row
    ends = [end_row(D1, "a,b", "model-a", prompt_tokens=1, api_requests=1, spend=Decimal("0.1")),
            end_row(D1, "ok", "model-a", prompt_tokens=1, api_requests=1, spend=Decimal("0.1"))]
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"), end_rows=ends)
    assert backfill(data_dir, proxy, fake_clock, "2026-09-15", NOW, "--end-users") == 0
    ids = sorted(e.params.get("end_user_ids", "") for e in proxy.requests_for(PATH_B))
    assert ids == ["", "ok"]
    assert manifests(data_dir)[-1]["skipped_entities"]["ids_with_comma"] == 1


def test_daily_with_since_and_no_state_starts_there_and_rereads_two_days(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert daily(data_dir, proxy, fake_clock, NOW, "--since", "2026-09-01") == 0
    first = proxy.requests_for(PATH_A)[0].params
    assert (first["start_date"], first["end_date"]) == ("2026-09-01", "2026-09-19")
    proxy.log.clear()
    assert daily(data_dir, proxy, fake_clock, at("2026-09-20T07:00:00")) == 0
    again = proxy.requests_for(PATH_A)[0].params
    assert (again["start_date"], again["end_date"]) == ("2026-09-18", "2026-09-19")
    proxy.log.clear()
    assert daily(data_dir, proxy, fake_clock, at("2026-09-23T07:00:00")) == 0
    later = proxy.requests_for(PATH_A)[0].params
    # 09-18 and 09-19 have no rows and were still open at the second run, so they were not recorded as loaded (MF-3);
    # the third run reads them once more, now that they are final
    assert (later["start_date"], later["end_date"]) == ("2026-09-18", "2026-09-22")
    proxy.log.clear()
    assert daily(data_dir, proxy, fake_clock, at("2026-09-24T07:00:00")) == 0
    final = proxy.requests_for(PATH_A)[0].params
    assert (final["start_date"], final["end_date"]) == ("2026-09-21", "2026-09-23")


@pytest.mark.parametrize("noise", [Decimal("1e-16"), Decimal("-1e-16")])
def test_float_rounding_noise_is_not_usage(make_proxy, fake_clock, data_dir, noise):
    rows = [
        row(D1, "user-a", "model-a", prompt_tokens=100, api_requests=1, spend=Decimal("0.1")),
        row(D1, "user-a", "model-a", key="k2", prompt_tokens=100, api_requests=1, spend=Decimal("0.2")),
    ]
    proxy = make_proxy(rows, users=users("user-a"), spend_noise=noise)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    usage = records(data_dir, "usage")
    assert [(r.actor_kind, r.model) for r in usage] == [("proxy_user", "model-a")]
    assert manifests(data_dir)[-1]["modelless_days"] == []


def test_pulled_at_with_microseconds_still_rebuilds_byte_for_byte(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert daily(data_dir, proxy, fake_clock, NOW.replace(microsecond=123456), "--since", "2026-09-01") == 0
    before = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    from meterline_litellm.cli import main
    assert main(["rebuild", "--data-dir", str(data_dir)]) == 0
    after = {str(p.relative_to(data_dir)): p.read_bytes() for p in (data_dir / "records").rglob("*.jsonl")}
    assert before and before == after


def test_a_model_split_across_pages_is_summed(make_proxy, fake_clock, data_dir):
    rows = [
        row(D1, "user-a", "model-a", key="k1", prompt_tokens=100, api_requests=1, spend=Decimal("0.10")),
        row(D1, "user-a", "model-b", key="k1", prompt_tokens=50, api_requests=1, spend=Decimal("0.05")),
        row(D1, "user-a", "model-a", key="k2", prompt_tokens=200, api_requests=2, spend=Decimal("0.20")),
    ]
    proxy = make_proxy(rows, users=users("user-a"), page_cap=2)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    got = {r.model: r for r in records(data_dir, "usage")}
    assert (got["model-a"].tokens_input_total, got["model-a"].requests, got["model-a"].cost) == (300, 3, Decimal("0.30"))
    assert got["model-b"].tokens_input_total == 50


def test_end_users_never_change_the_unassigned_records(make_proxy, fake_clock, tmp_path):
    from meterline_litellm.testing import end_row
    rows = basic_rows() + [row(D1, None, "model-a", prompt_tokens=9, api_requests=1, spend=Decimal("0.01"))]
    ends = [end_row(D1, "end-1", "model-a", prompt_tokens=9, api_requests=1, spend=Decimal("0.01"))]
    proxy = make_proxy(rows, users=users("user-a", "user-b"), end_rows=ends)
    off, on = tmp_path / "off", tmp_path / "on"
    assert backfill(off, proxy, fake_clock, since="2026-09-15") == 0
    assert backfill(on, proxy, fake_clock, "2026-09-15", NOW, "--end-users") == 0
    pick = lambda d: sorted(json.dumps(r.to_dict(), sort_keys=True) for r in records(d, "usage") if r.actor_kind == "unassigned")
    assert pick(off) and pick(off) == pick(on)
    assert not any(r.actor_kind == "unassigned" and r.actor_id != "(unassigned)" for r in records(on, "usage"))


def test_request_url_in_raw_files_drops_base_url_credentials(make_proxy, fake_clock, data_dir, monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "https://someone:hunter2@example.com")
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 0
    for p in (data_dir / "raw").rglob("*.gz"):
        text = gzip.decompress(p.read_bytes()).decode("utf-8")
        assert "hunter2" not in text and "someone" not in text


def test_an_unusable_body_fails_the_run_with_a_manifest(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    handle = proxy._handle

    def bad(request):
        response = handle(request)
        if request.url.path.endswith(PATH_A) and "user_id" not in request.url.params:
            body = json.loads(response.content)
            body["results"][0]["metrics"]["prompt_tokens"] = "many"
            return httpx.Response(200, json=body)
        return response

    proxy.transport = httpx.MockTransport(bad)
    assert backfill(data_dir, proxy, fake_clock, since="2026-09-15") == 2
    assert "nothing published" in capsys.readouterr().err
    assert manifests(data_dir)[-1]["status"] == "failed"
    assert json.loads((data_dir / "state.json").read_text())["pending"] is None
