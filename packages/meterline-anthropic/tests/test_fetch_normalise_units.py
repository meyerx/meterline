"""Unit tests for fetch.py and normalise.py (no done-check names here)."""

import gzip
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from meterline_anthropic.testing import FakeApi
from meterline.writers import write_jsonl
from meterline_anthropic.client import AnalyticsClient
from meterline_anthropic.fetch import (
    RunManifest, batch_id_for, fetch_activity_day, fetch_org_totals,
    _gzip_json, fetch_summaries, fetch_usage_window, windows,
)
from meterline_anthropic.normalise import (
    drop_unchanged, is_final_period, load_published, normalise_run, read_raw, run_checks,
)

PULLED_AT = datetime(2026, 10, 9, 13, 0, 0, tzinfo=timezone.utc)
BATCH = batch_id_for(PULLED_AT)
TODAY = date(2026, 10, 9)


def _client(api, clock, key):
    return AnalyticsClient(key, transport=api.transport, clock=clock, sleep=clock.sleep, jitter=lambda: 0)


@pytest.fixture
def api(dataset, fake_clock, fake_key_env):
    return FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)


@pytest.fixture
def full_run(api, fake_clock, fake_key_env, data_dir):
    with _client(api, fake_clock, fake_key_env) as client:
        files = [p.path for p in fetch_usage_window(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT)]
        files += [p.path for p in fetch_org_totals(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT)]
        files.append(fetch_activity_day(client, data_dir, date(2026, 9, 3), PULLED_AT).path)
        files.append(fetch_summaries(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT).path)
    return normalise_run(files, BATCH, PULLED_AT), files


def test_windows_split():
    assert windows(date(2026, 7, 1), date(2026, 7, 31)) == [(date(2026, 7, 1), date(2026, 7, 31))]
    got = windows(date(2026, 7, 1), date(2026, 8, 5))
    assert got == [(date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 8, 1), date(2026, 8, 5))]


def test_full_run_passes_checks_and_counts(full_run):
    run, files = full_run
    assert run.usage and run.activity and run.seats and run.org_totals
    assert run_checks(run, TODAY) == []
    assert run.cost_only == []
    # no published batch: every product and model value is new; none after publishing them
    assert run.unknown_products and run.unknown_models
    again = normalise_run(files, BATCH, PULLED_AT, published={"usage": {r.key(): r for r in run.usage}})
    assert again.unknown_products == [] and again.unknown_models == []
    assert run.row_counts["user_usage_report"] == len(run.usage)


def test_missing_cost_gives_none(dataset, fake_clock, fake_key_env, data_dir):
    gone = dataset["meta"]["usage_rows_without_cost"][0]
    day = date.fromisoformat(gone["date"])
    api = FakeApi(dataset, clock=fake_clock, api_key=fake_key_env)
    with _client(api, fake_clock, fake_key_env) as client:
        pulls = fetch_usage_window(client, data_dir, day, day, PULLED_AT)
    run = normalise_run([p.path for p in pulls], BATCH, PULLED_AT)
    rec = next(
        r for r in run.usage
        if r.actor_id == gone["actor_id"] and r.product == gone["product"] and r.model == gone["model"]
    )
    assert rec.cost is None and rec.cost_list is None
    assert run_checks(run, TODAY) == []


def test_email_trim_and_lowercase(api, fake_clock, fake_key_env, data_dir):
    with _client(api, fake_clock, fake_key_env) as client:
        pulls = fetch_usage_window(client, data_dir, date(2026, 9, 1), date(2026, 9, 2), PULLED_AT)
    run = normalise_run([p.path for p in pulls], BATCH, PULLED_AT)
    emails = {r.actor_email for r in run.usage if r.actor_id == "user-007"}
    assert emails and all(e == e.strip().lower() for e in emails)
    assert all(r.actor_email is None for r in run.usage if r.actor_deleted)


def test_is_final_boundary():
    # August ends on the 31st: the 7th is +7 days (not final), the 8th is +8 (final)
    assert is_final_period(date(2026, 8, 10), datetime(2026, 9, 7, 23, 0, tzinfo=timezone.utc)) is False
    assert is_final_period(date(2026, 8, 10), datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)) is True


def test_rule6_failures(full_run):
    run, _ = full_run
    future = replace(run.usage[0], period_start=date(2026, 10, 20), period_end=date(2026, 10, 20))
    assert any("future" in f for f in run_checks(replace(run, usage=run.usage + [future]), TODAY))

    negative = replace(run.usage[0], tokens_output=-1)
    bad = replace(run, usage=[negative] + run.usage[1:])
    assert any("negative" in f for f in run_checks(bad, TODAY))

    dup = replace(run, usage=run.usage + [run.usage[0]])
    assert any("duplicate" in f for f in run_checks(dup, TODAY))

    one = run.usage[0]
    changed = replace(one, tokens_input_uncached=one.tokens_input_uncached + 1,
                      tokens_input_total=one.tokens_input_total + 1)
    mismatch = run_checks(replace(run, usage=[changed] + run.usage[1:]), TODAY)
    assert any("tokens_input totals differ" in f for f in mismatch)

    # one user-day cost above that day's organization total
    day = run.org_totals[0].period_start
    org_total = sum((o.cost for o in run.org_totals if o.period_start == day), Decimal(0))
    raw = dict(run.raw_totals)
    raw["actor_day_cost"] = {("user-001", day): org_total + Decimal("0.01")}
    over = run_checks(replace(run, raw_totals=raw), TODAY)
    assert any("above the organization total" in f for f in over)


def test_drop_unchanged_ignores_run_fields(full_run):
    run, _ = full_run
    latest = {r.key(): r for r in run.usage}
    later = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)
    again = [replace(r, pulled_at=later, data_refreshed_at=later, batch_id="20261010T080000Z") for r in run.usage]
    assert drop_unchanged(again, latest) == []
    edited = replace(again[0], tokens_output=again[0].tokens_output + 1)
    assert drop_unchanged([edited] + again[1:], latest) == [edited]
    assert drop_unchanged(run.usage, {}) == run.usage


def test_load_published_reads_latest(full_run, data_dir):
    run, _ = full_run
    write_jsonl(run.usage, data_dir / "records" / "usage" / (BATCH + ".jsonl"))
    newer = [replace(run.usage[0], batch_id="20261011T000000Z", tokens_input_uncached=1)]
    write_jsonl(newer, data_dir / "records" / "usage" / "20261011T000000Z.jsonl")
    published = load_published(data_dir)
    assert len(published["usage"]) == len(run.usage)
    assert published["usage"][run.usage[0].key()].tokens_input_uncached == 1
    assert published["seats"] == {}


def test_raw_file_has_no_key_or_headers(full_run, fake_key_env):
    _, files = full_run
    for path in files:
        blob = gzip.open(path, "rb").read().decode("utf-8")
        assert fake_key_env not in blob
        low = blob.lower()
        assert "x-api-key" not in low and "user-agent" not in low
        raw = read_raw(path)
        assert set(raw) == {
            "endpoint", "window", "request_url", "pulled_at", "data_refreshed_at", "organization_id", "pages",
        }
        assert raw["organization_id"] == "org-synthetic-0001"
        assert path.parts[-4] == "raw" and path.parts[-3] == raw["endpoint"] and path.parts[-2] == raw["window"]
        assert path.name == BATCH + ".json.gz"


def test_raw_pages_are_response_text_and_gzip_deterministic(api, fake_clock, fake_key_env, data_dir, tmp_path):
    with _client(api, fake_clock, fake_key_env) as client:
        first = fetch_usage_window(client, data_dir, date(2026, 9, 1), date(2026, 9, 1), PULLED_AT)[0]
        other = tmp_path / "other"
        second = fetch_usage_window(client, other, date(2026, 9, 1), date(2026, 9, 1), PULLED_AT)[0]
    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.path.read_bytes()[4:8] == b"\x00\x00\x00\x00"  # gzip mtime field is zero
    raw = read_raw(first.path)
    assert isinstance(raw["pages"][0]["body"], str)
    assert json.loads(raw["pages"][0]["body"])["data"]
    assert first.window == "2026-09-01_2026-09-01"


def test_probe_carries_the_window_end_and_caps_the_window(api, fake_clock, fake_key_env, data_dir):
    api.refreshed_at = "2026-09-03T06:00:00Z"
    with _client(api, fake_clock, fake_key_env) as client:
        pulls = fetch_usage_window(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT)
    assert len(pulls) == 2
    probe, window = api.requests_for("user_usage_report")[:2]
    assert probe.params["limit"] == "1"
    assert probe.params["ending_at"] == "2026-09-08T00:00:00Z"  # B1-a: required with bucket_width; the window's own end
    assert probe.params["starting_at"] == "2026-09-01T00:00:00Z"
    assert probe.params["group_by[]"] == ["model", "product"]
    assert window.params["starting_at"] == "2026-09-01T00:00:00Z"
    assert window.params["ending_at"] == "2026-09-03T00:00:00Z"  # UTC midnight of the cap's day
    assert window.params["limit"] == "1000"
    assert all(p.provider_account_id == "org-synthetic-0001" for p in pulls)
    assert read_raw(pulls[0].path)["organization_id"] == "org-synthetic-0001"


def test_org_probe_and_window_limits(api, fake_clock, fake_key_env, data_dir):
    with _client(api, fake_clock, fake_key_env) as client:
        fetch_org_totals(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT)
    probe, window = api.requests_for("usage_report")[:2]
    assert probe.params["limit"] == "1" and "ending_at" not in probe.params
    assert "group_by[]" not in probe.params
    assert window.params["limit"] == "31"
    assert window.params["ending_at"] == "2026-09-08T00:00:00Z"


def test_null_probe_skips_the_window(api, fake_clock, fake_key_env, data_dir):
    api.set_refreshed_at_for_window(date(2026, 9, 1), None)
    skipped = []
    with _client(api, fake_clock, fake_key_env) as client:
        pulls = fetch_usage_window(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT, skipped)
    assert pulls == []
    assert skipped == [
        "user_usage_report 2026-09-01_2026-09-07: no data yet",
        "user_cost_report 2026-09-01_2026-09-07: no data yet",
    ]
    assert len(api.requests_for("user_usage_report")) == 1  # the probe only
    assert not (data_dir / "raw").exists()


def test_probe_stamp_before_window_start_skips(api, fake_clock, fake_key_env, data_dir):
    api.refreshed_at = "2026-08-31T20:00:00Z"
    skipped = []
    with _client(api, fake_clock, fake_key_env) as client:
        assert fetch_org_totals(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT, skipped) == []
    assert len(skipped) == 2 and all("no data yet" in s for s in skipped)


def test_summaries_end_date_is_exclusive(api, fake_clock, fake_key_env, data_dir):
    with _client(api, fake_clock, fake_key_env) as client:
        pull = fetch_summaries(client, data_dir, date(2026, 9, 1), date(2026, 9, 7), PULLED_AT, provider_account_id="org-x")
    entries = api.requests_for("summaries")
    assert len(entries) == 1
    assert entries[0].params == {"starting_date": "2026-09-01", "ending_date": "2026-09-08"}
    assert pull.row_count == 7
    assert read_raw(pull.path)["organization_id"] == "org-x"
    assert pull.data_refreshed_at is None


def test_activity_day_learns_the_provider_account_id_with_one_probe(api, fake_clock, fake_key_env, data_dir):
    with _client(api, fake_clock, fake_key_env) as client:
        pull = fetch_activity_day(client, data_dir, date(2026, 9, 3), PULLED_AT)
    assert pull.provider_account_id == "org-synthetic-0001"
    assert read_raw(pull.path)["organization_id"] == "org-synthetic-0001"
    probe = api.requests_for("usage_report")
    assert len(probe) == 1
    assert probe[0].params["limit"] == "1" and "ending_at" not in probe[0].params
    assert not (data_dir / "raw" / "usage_report").exists()


def _write_raw_file(data_dir, endpoint, window, bodies, **extra):
    envelope = {
        "endpoint": endpoint, "window": window, "request_url": "https://example.com/" + endpoint,
        "pulled_at": "2026-10-09T13:00:00Z",
        "pages": [{"status": 200, "body": json.dumps(b)} for b in bodies],
    }
    envelope.update(extra)
    path = data_dir / "raw" / endpoint / window / (BATCH + ".json.gz")
    _gzip_json(path, envelope)
    return path


def _user_row(user_id="user-x", **blocks):
    row = {"user": {"type": "user", "id": user_id, "email_address": " Mixed.Case@Example.com "}}
    row.update(blocks)
    return row


def test_missing_data_refreshed_at_in_a_raw_file(data_dir):
    usage = {"data": [{
        "actor": {"type": "user_actor", "user_id": "u1", "email_address": None, "deleted": False},
        "starting_at": "2026-09-01T00:00:00Z", "ending_at": "2026-09-02T00:00:00Z",
        "model": None, "product": None, "uncached_input_tokens": 5, "output_tokens": 1,
        "cache_read_input_tokens": 0, "requests": None, "server_tool_use": {"web_search_requests": 0},
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
    }], "has_more": False, "next_page": None, "organization_id": "org-a"}  # no data_refreshed_at key
    path = _write_raw_file(data_dir, "user_usage_report", "2026-09-01_2026-09-01", [usage],
                           organization_id="org-a")
    run = normalise_run([path], BATCH, PULLED_AT)
    assert len(run.usage) == 1
    rec = run.usage[0]
    assert rec.data_refreshed_at is None
    assert (rec.product, rec.model, rec.provider_account_id, rec.actor_email) == ("", "", "org-a", None)
    assert rec.requests is None and rec.cost is None and rec.cost_only is False
    assert run.unknown_products == [] and run.unknown_models == []


def test_provider_account_id_falls_back_to_the_page_body_then_raises(data_dir):
    body = {"data": [], "has_more": False, "next_page": None, "organization_id": "org-page"}
    path = _write_raw_file(data_dir, "usage_report", "2026-09-01_2026-09-01", [dict(body, data=[
        {"starting_at": "2026-09-01T00:00:00Z", "ending_at": "2026-09-02T00:00:00Z", "results": []}])])
    run = normalise_run([path], BATCH, PULLED_AT)
    assert [(r.provider_account_id, r.requests, r.cost, r.is_final) for r in run.org_totals] == [("org-page", None, None, True)]
    bare = {"data": [{"starting_at": "2026-09-01T00:00:00Z", "results": []}]}
    other = _write_raw_file(data_dir, "usage_report", "2026-09-02_2026-09-02", [bare])
    with pytest.raises(ValueError):
        normalise_run([other], BATCH, PULLED_AT)


def test_null_counts_are_zero_and_empty_blocks_give_no_record(data_dir):
    row = _user_row(
        chat_metrics={"distinct_conversation_count": None, "message_count": 3},
        claude_code_metrics={"core_metrics": {"distinct_session_count": None, "commit_count": 2,
                                              "lines_of_code": None}, "tool_actions": None},
        cowork_metrics={"distinct_session_count": None, "message_count": 0},
        design_metrics={"distinct_session_count": None, "message_count": 0},
        science_metrics={"distinct_session_count": None, "message_count": 0},
        web_search_count=None,
    )
    path = _write_raw_file(data_dir, "users", "2026-09-03", [{"data": [row], "next_page": None}],
                           organization_id="org-a", data_refreshed_at=None)
    run = normalise_run([path], BATCH, PULLED_AT)
    by_product = {r.product: r for r in run.activity}
    assert set(by_product) == {"chat", "claude_code"}
    assert (by_product["chat"].messages, by_product["chat"].counters["conversations"]) == (3, 0)
    assert (by_product["claude_code"].commits, by_product["claude_code"].sessions) == (2, 0)
    assert by_product["chat"].actor_email == "mixed.case@example.com"
    assert all(r.data_refreshed_at is None for r in run.activity)
    assert run_checks(run, TODAY) == []


def test_web_search_count_is_one_all_record(data_dir):
    rows = [
        _user_row("u-a", web_search_count=4),
        _user_row("u-b", web_search_count=0, chat_metrics={"message_count": 1}),
    ]
    path = _write_raw_file(data_dir, "users", "2026-09-03", [{"data": rows, "next_page": None}],
                           organization_id="org-a")
    run = normalise_run([path], BATCH, PULLED_AT)
    alls = [r for r in run.activity if r.product == "all"]
    assert [(r.actor_id, r.counters["web_searches"]) for r in alls] == [("u-a", 4)]
    assert sum(r.counters["web_searches"] for r in alls) == 4
    assert all(r.sessions == r.messages == r.commits == 0 for r in alls)
    assert [r.actor_id for r in run.activity if r.product == "chat"] == ["u-b"]
    assert run_checks(run, TODAY) == []


def test_unified_and_office_blocks(data_dir):
    row = _user_row(
        office_metrics={"word": {"distinct_session_count": 2, "message_count": 5},
                        "excel": {"distinct_session_count": 0, "message_count": 0}},
        chat_cowork_unified_metrics={"chat": {"distinct_conversation_count": 1, "message_count": 2},
                                     "sessions": {"distinct_session_count": 3, "artifacts_created_count": 1}},
    )
    path = _write_raw_file(data_dir, "users", "2026-09-03", [{"data": [row], "next_page": None}],
                           organization_id="org-a")
    run = normalise_run([path], BATCH, PULLED_AT)
    assert {r.product for r in run.activity} == {
        "office_agent.word", "chat_cowork_unified.chat", "chat_cowork_unified.sessions",
    }
    by = {r.product: r for r in run.activity}
    assert (by["office_agent.word"].sessions, by["office_agent.word"].messages) == (2, 5)
    assert (by["chat_cowork_unified.sessions"].sessions, by["chat_cowork_unified.sessions"].counters["artifacts"]) == (3, 1)
    assert by["chat_cowork_unified.chat"].counters["conversations"] == 1


def test_manifest_reads_a_file_written_before_the_new_fields(data_dir):
    m = RunManifest(command="daily", batch_id=BATCH, pulled_at=PULLED_AT, skipped_windows=["b", "a"], cost_only=["z", "y"])
    written = json.loads(m.to_json())
    assert written["skipped_windows"] == ["a", "b"] and written["cost_only"] == ["y", "z"]
    del written["skipped_windows"], written["cost_only"]
    path = data_dir / "runs" / "old.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(written), encoding="utf-8")
    old = RunManifest.read(path)
    assert old.skipped_windows == [] and old.cost_only == []


def test_activity_unavailable_returns_none(api, fake_clock, fake_key_env, data_dir):
    api.set_latest_activity_date(date(2026, 9, 10))
    seen = []
    with _client(api, fake_clock, fake_key_env) as client:
        assert fetch_activity_day(client, data_dir, date(2026, 9, 11), PULLED_AT, on_unavailable=seen.append) is None
        assert fetch_activity_day(client, data_dir, date(2026, 9, 10), PULLED_AT) is not None
    assert seen == [date(2026, 9, 10)]


def test_manifest_round_trip(data_dir):
    m = RunManifest(command="daily", batch_id=BATCH, pulled_at=PULLED_AT, totals={"cost": "1.00"})
    path = m.write(data_dir)
    assert path == data_dir / "runs" / (BATCH + ".json")
    assert RunManifest.read(path) == m
    assert m.to_json() == m.to_json()
