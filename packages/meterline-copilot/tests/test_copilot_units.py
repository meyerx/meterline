"""Unit and behaviour tests beyond the done-checks: resume, rebuild, bad
bodies, 204 days, credentials, logging, windows."""

import gzip
import hashlib
import json
import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from meterline_copilot.testing import FakeAPI, aggregate_row, feature, item, seat, user_row
from copilot_helpers import (
    FAKE_BILLING_TOKEN, FAKE_TOKEN, NOW, SLUG, at, backfill, daily, latest, manifests, records, run,
)

DAY = "2026-09-18"


def _hash_tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_missing_token_or_bad_slug_is_a_configuration_error(make_api, fake_clock, data_dir, monkeypatch, capsys, level):
    api = make_api()
    monkeypatch.delenv("COPILOT_TOKEN")
    assert daily(data_dir, api, fake_clock) == 2
    assert "COPILOT_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("COPILOT_TOKEN", FAKE_TOKEN)
    assert run(["daily", "--data-dir", str(data_dir), "--level", level, "--slug", "bad slug/x"], api, fake_clock) == 2
    assert run(["daily", "--data-dir", str(data_dir), "--level", level, "--slug", SLUG, "--rate-per-hour", "0"],
               api, fake_clock) == 2
    assert not api.log


def test_billing_token_defaults_to_the_usage_token(make_api, fake_clock, data_dir, monkeypatch, level):
    api = make_api()
    api.billing_token = FAKE_TOKEN
    monkeypatch.delenv("COPILOT_BILLING_TOKEN")
    api.billing_day[DAY] = [item()]
    assert daily(data_dir, api, fake_clock) == 0
    assert records(data_dir, "org_totals")


def test_tokens_go_only_to_their_endpoints(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.billing_day[DAY] = [item()]
    api.seats = [seat("login1", 1)]
    assert daily(data_dir, api, fake_clock) == 0
    for entry in api.api_requests():
        want = FAKE_BILLING_TOKEN if "/settings/billing/" in entry.path else FAKE_TOKEN
        assert entry.authorization == "Bearer " + want


def test_a_204_day_is_stored_and_publishes_nothing_for_it(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.no_report_days.add(DAY)
    api.users["2026-09-17"] = [user_row("2026-09-17", 1, "login1", level=level)]
    assert daily(data_dir, api, fake_clock) == 0
    days = {r.period_start.isoformat() for r in records(data_dir, "usage")}
    assert days == {"2026-09-17"}
    raw = list((data_dir / "raw" / "users_1_day" / DAY).glob("*.json.gz"))
    assert len(raw) == 1
    body = json.loads(gzip.decompress(raw[0].read_bytes()))
    assert body["status"] == 204 and body["files"] == []


def test_a_broken_download_link_restarts_the_day_once_then_fails(make_api, fake_clock, data_dir, level):
    api = make_api(links_per_report=1)
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    api.download_status_once["users-%s-1.ndjson" % DAY] = 403
    assert daily(data_dir, api, fake_clock) == 0
    assert len(api.api_requests("users-1-day")) >= 2  # the report call was made again
    assert records(data_dir, "usage")

    # a download that keeps answering 403 fails the run (exit 3, a refusal) after one restart
    stuck = make_api(links_per_report=1)
    stuck.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    original = stuck._download

    def always_forbidden(path, reply):
        if path.endswith("users-%s-1.ndjson" % DAY):
            return reply(403, {"message": "expired"})
        return original(path, reply)

    stuck._download = always_forbidden
    assert daily(data_dir.parent / "stuck", stuck, fake_clock) == 3
    assert len([e for e in stuck.api_requests("users-1-day") if e.params["day"] == DAY]) == 2


@pytest.mark.parametrize("which", ["no_links", "no_items", "bad_ndjson"])
def test_a_body_that_breaks_the_shape_exits_2_with_a_failed_manifest(make_api, fake_clock, data_dir, capsys, which, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    if which == "no_links":
        api.broken_envelope = True
    elif which == "no_items":
        def broken(params, reply):
            return reply(200, {"timePeriod": {"year": 2026}, "organization": "x"})

        api._billing = broken
        api.billing_day[DAY] = [item()]
    else:
        def garbage(path, reply):
            return reply(200, text="{not json\n")

        api._download = garbage
    code = daily(data_dir, api, fake_clock)
    err = capsys.readouterr().err
    assert code == 2, err
    assert "Traceback" not in err
    assert not (data_dir / "records").exists()
    failed = manifests(data_dir)
    assert failed and failed[-1]["status"] == "failed"
    assert (data_dir / "state.json").exists()
    assert json.loads((data_dir / "state.json").read_text())["pending"] is None


def test_a_failed_call_resumes_without_repeating_finished_calls(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    api.billing_day[DAY] = [item()]
    api.fail("/copilot/billing/seats", 500, times=20)
    assert daily(data_dir, api, fake_clock) == 1
    done = len(api.api_requests("users-1-day"))
    assert done > 0 and not (data_dir / "records").exists()
    api._faults.clear()
    api.log.clear()
    assert daily(data_dir, api, fake_clock) == 0
    assert not api.api_requests("users-1-day")  # reused from the first attempt
    assert records(data_dir, "usage")
    assert api.api_requests("/copilot/billing/seats")


def test_rebuild_reproduces_published_records_byte_for_byte(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users["2026-09-17"] = [user_row("2026-09-17", 1, "login1", level=level, cli=True, app=True,
                                        credits=Decimal("3.5"))]
    api.users[DAY] = [user_row(DAY, 2, "login2", level=level)]
    api.aggregates["2026-09-19"] = aggregate_row("2026-09-19", 3, 4, 5)
    api.billing_day[DAY] = [item()]
    api.seats = [seat("login1", 1), seat("login2", 2)]
    api.billing_user[("login1", 2026, 9)] = [item()]
    api.billing_user[("login2", 2026, 9)] = [item(model="Claude")]
    micro = NOW.replace(microsecond=123456)  # an injected clock with a fraction
    assert daily(data_dir, api, fake_clock, micro) == 0
    api.users[DAY] = [user_row(DAY, 2, "login2", level=level, credits=Decimal("9"))]
    later = at("2026-09-21T06:00:00").replace(microsecond=654321)
    assert daily(data_dir, api, fake_clock, later) == 0
    before = _hash_tree(data_dir / "records")
    assert len(before) >= 6
    assert all(r.pulled_at.microsecond == 0 for r in records(data_dir, "usage"))
    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 0
    assert _hash_tree(data_dir / "records") == before
    assert not (data_dir / ".rebuild").exists()


def test_logs_names_and_manifests_carry_no_logins(make_api, fake_clock, data_dir, caplog, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "secret-login", level=level)]
    api.seats = [seat("secret-login", 1)]
    api.billing_user[("secret-login", 2026, 9)] = [item()]
    with caplog.at_level(logging.DEBUG):
        assert daily(data_dir, api, fake_clock) == 0
    assert "secret-login" not in caplog.text
    for path in data_dir.rglob("*"):
        assert "secret-login" not in path.relative_to(data_dir).as_posix()
    for text in (p.read_text(encoding="utf-8") for p in (data_dir / "runs").glob("*.json")):
        assert "secret-login" not in text
    person = list((data_dir / "raw" / "billing_user_month").rglob("*.json.gz"))
    assert person
    for path in person:
        body = json.loads(gzip.decompress(path.read_bytes()))
        assert "secret-login" not in body["request_url"]


def test_raw_files_keep_no_query_signature_and_no_credentials(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    assert daily(data_dir, api, fake_clock) == 0
    for path in (data_dir / "raw").rglob("*.json.gz"):
        text = gzip.decompress(path.read_bytes()).decode("utf-8")
        assert "synthetic-signature" not in text
        assert "Authorization" not in text and "authorization" not in text
        assert "@" not in json.loads(text).get("request_url", "")


def test_provider_account_id_differs_between_levels(fake_clock, tmp_path):
    out = {}
    for kind in ("organization", "enterprise"):
        api = FakeAPI(dataset=None, level=kind, slug=SLUG, token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, clock=fake_clock)
        api.users[DAY] = [user_row(DAY, 1, "login1", level=kind)]
        assert daily(tmp_path / kind, api, fake_clock) == 0
        out[kind] = {r.provider_account_id for r in records(tmp_path / kind, "usage")}
    assert out == {"organization": {"org:" + SLUG}, "enterprise": {"enterprise:" + SLUG}}


def test_daily_without_state_starts_at_the_month_and_rereads_four_days(make_api, fake_clock, data_dir, level):
    api = make_api()
    assert daily(data_dir, api, fake_clock) == 0
    days = sorted({e.params["day"] for e in api.api_requests("users-1-day")})
    assert days[0] == "2026-09-01" and days[-1] == "2026-09-19"
    api.log.clear()
    assert daily(data_dir, api, fake_clock, at("2026-09-21T06:00:00")) == 0
    days = sorted({e.params["day"] for e in api.api_requests("users-1-day")})
    assert days == ["2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]


def test_early_in_the_month_the_previous_month_is_read_too(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users["2026-08-29"] = [user_row("2026-08-29", 1, "login-aug", level=level)]
    api.users["2026-09-01"] = [user_row("2026-09-01", 2, "login-sep", level=level)]
    api.billing_user[("login-aug", 2026, 8)] = [item()]
    api.billing_user[("login-sep", 2026, 9)] = [item()]
    assert daily(data_dir, api, fake_clock, at("2026-09-02T06:00:00")) == 0
    # the 29th of August is outside the 4-day window; load it by a backfill, then ask again
    api2 = make_api()
    api2.users["2026-08-29"] = api.users["2026-08-29"]
    api2.users["2026-09-01"] = api.users["2026-09-01"]
    api2.billing_user = dict(api.billing_user)
    other = data_dir.parent / "b"
    assert backfill(other, api2, fake_clock, "2026-08-28", at("2026-09-02T06:00:00")) == 0
    api2.log.clear()
    assert daily(other, api2, fake_clock, at("2026-09-03T06:00:00")) == 0
    asked = sorted((e.params["user"], e.params["month"]) for e in api2.billing_requests() if "user" in e.params)
    assert asked == [("login-aug", "8"), ("login-sep", "9")]
    months = {(r.actor_login, r.period_start.isoformat()) for r in records(other, "usage") if r.grain == "month"}
    assert months == {("login-aug", "2026-08-01"), ("login-sep", "2026-09-01")}


def test_backfill_clamps_to_the_first_report_day_and_one_year(make_api, fake_clock, data_dir, level):
    api = make_api()
    now = at("2026-10-12T06:00:00")
    assert backfill(data_dir, api, fake_clock, "2024-01-01", now) == 0
    days = sorted({e.params["day"] for e in api.api_requests("users-1-day")})
    assert days[0] == "2025-10-12" and days[-1] == "2026-10-11"
    assert manifests(data_dir)[-1]["notes"]["since_clamped_to"] == "2025-10-12"
    assert len(days) == 365


def test_finality_four_days_for_reports_and_a_week_after_month_end(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users["2026-09-14"] = [user_row("2026-09-14", 1, "login1", level=level)]
    api.users["2026-09-15"] = [user_row("2026-09-15", 1, "login1", level=level)]
    api.billing_user[("login1", 2026, 9)] = [item()]
    assert backfill(data_dir, api, fake_clock, "2026-09-14", at("2026-09-19T00:00:00")) == 0
    got = {r.period_start.isoformat(): r.is_final for r in records(data_dir, "usage") if r.grain == "day"}
    assert got["2026-09-14"] is True   # ended 2026-09-15 00:00; four days later is 2026-09-19 00:00
    assert got["2026-09-15"] is False
    month = next(r for r in records(data_dir, "usage") if r.grain == "month")
    assert month.is_final is False
    # a month more than seven days over
    api2 = make_api()
    api2.users["2026-09-14"] = api.users["2026-09-14"]
    api2.billing_user[("login1", 2026, 9)] = [item()]
    other = data_dir.parent / "late"
    assert backfill(other, api2, fake_clock, "2026-09-14", at("2026-10-08T00:00:00")) == 0
    # the 8th of October asks for the previous month too (day <= 8), and 2026-09-30 + 7 < 2026-10-08
    month = next(r for r in records(other, "usage") if r.grain == "month" and r.period_start.isoformat() == "2026-09-01")
    assert month.is_final is True


def test_unknown_products_and_models_are_listed_once(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.billing_day[DAY] = [item("Copilot", "GPT-5"), item("Other Product", "Model-X")]
    assert daily(data_dir, api, fake_clock) == 0
    first = manifests(data_dir)[-1]
    assert first["unknown_products"] == ["copilot", "other_product"]
    assert first["unknown_models"] == ["GPT-5", "Model-X"]
    assert daily(data_dir, api, fake_clock, at("2026-09-20T07:00:00")) == 0
    again = manifests(data_dir)[-1]
    assert again["unknown_products"] == [] and again["unknown_models"] == []


def test_the_person_price_is_kept_only_when_all_items_agree(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    api.billing_user[("login1", 2026, 9)] = [
        item("Copilot", "GPT-5", "credits", 10, Decimal("0.1"), price=Decimal("0.01")),
        item("Copilot", "GPT-5", "requests", 2, Decimal("0.08"), price=Decimal("0.04")),
        item("Copilot", "Claude", "credits", 10, Decimal("0.1"), price=Decimal("0.01")),
    ]
    assert daily(data_dir, api, fake_clock) == 0
    by = {r.model: r for r in records(data_dir, "usage") if r.grain == "month"}
    assert "price_per_unit" not in by["GPT-5"].extras
    assert by["Claude"].extras["price_per_unit"] == Decimal("0.01")
    assert by["GPT-5"].quantities == {"ai_credit": Decimal("10"), "premium_request": Decimal("2")}


def test_aggregate_without_the_days_entry_leaves_active_counts_null(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.aggregates["2026-09-19"] = {"day_totals": [], "report_start_day": "2026-09-19", "report_end_day": "2026-09-19"}
    assert daily(data_dir, api, fake_clock) == 0
    assert "2026-09-19" in manifests(data_dir)[-1]["aggregate_missing"]
    snap = {r.period_start.isoformat(): r for r in records(data_dir, "seats")}
    assert "2026-09-19" not in snap or snap["2026-09-19"].daily_active is None


def test_aggregate_row_with_top_level_counts_is_read(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.aggregates["2026-09-19"] = {"day": "2026-09-19", "daily_active_users": 4, "weekly_active_users": 5,
                                    "monthly_active_users": 6}
    assert daily(data_dir, api, fake_clock) == 0
    snap = {r.period_start.isoformat(): r for r in records(data_dir, "seats")}["2026-09-19"]
    assert (snap.daily_active, snap.weekly_active, snap.monthly_active) == (4, 5, 6)
    assert snap.seats is None


def test_organization_summary_fills_pending_counts(fake_clock, data_dir):
    api = FakeAPI(dataset=None, level="organization", slug=SLUG, token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, clock=fake_clock)
    api.seats = [seat("login1", 1)]
    assert daily(data_dir, api, fake_clock) == 0
    snap = records(data_dir, "seats")[-1]
    assert (snap.seats, snap.pending_invites, snap.pending_cancellations) == (1, 1, 2)


def test_seat_pages_follow_the_link_header(make_api, fake_clock, data_dir, level):
    api = make_api(seats_per_page=2)
    api.seats = [seat("login%d" % i, i) for i in range(1, 6)]
    assert daily(data_dir, api, fake_clock) == 0
    assert len(api.api_requests("/copilot/billing/seats")) == 3
    assert len(records(data_dir, "seat_assignments")) == 5


def test_zero_credits_is_a_zero_record_not_null(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, credits=0)]
    assert daily(data_dir, api, fake_clock) == 0
    rec = next(r for r in records(data_dir, "usage") if r.product == "copilot")
    assert rec.quantities == {"ai_credit": Decimal(0)} and rec.cost == Decimal(0)


def test_cli_and_app_activity_take_sessions_and_requests(make_api, fake_clock, data_dir, level):
    api = make_api()
    feats = [feature("copilot_cli", gen=0, acc=0, added=0, sug_add=0, msgs=4), feature("code_completion")]
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, features=feats, cli=True)]
    assert daily(data_dir, api, fake_clock) == 0
    cli = next(r for r in records(data_dir, "activity") if r.product == "copilot_cli")
    assert (cli.sessions, cli.requests, cli.messages) == (2, 2, 4)
    other = next(r for r in records(data_dir, "activity") if r.product == "code_completion")
    assert (other.sessions, other.requests) == (None, None)
    distinct = next(r for r in records(data_dir, "activity") if r.product == "copilot")
    assert distinct.counters["skills_used"] == 3 and distinct.messages is None


def test_two_runs_on_one_day_replace_the_snapshot_through_revisions(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.seats = [seat("login1", 1)]
    assert daily(data_dir, api, fake_clock, at("2026-09-20T06:00:00")) == 0
    api.seats = [seat("login1", 1), seat("login2", 2)]
    assert daily(data_dir, api, fake_clock, at("2026-09-20T18:00:00")) == 0
    snap = latest(data_dir, "seats")
    day = next(v for k, v in snap.items() if k[-1].isoformat() == "2026-09-20")
    assert day.seats == 2
    assert len(latest(data_dir, "seat_assignments")) == 2


def test_a_model_less_billing_item_beside_models_does_not_fail_the_run(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.billing_day[DAY] = [item("Copilot", "GPT-5"), item("Copilot", "", gross_a=Decimal("0.4"))]
    assert daily(data_dir, api, fake_clock) == 0
    models = sorted(r.model for r in records(data_dir, "org_totals") if r.period_start.isoformat() == DAY)
    assert models == ["(no model)", "GPT-5"]


def test_a_data_dir_of_another_level_is_refused(make_api, fake_clock, data_dir, level, capsys):
    api = make_api()
    assert daily(data_dir, api, fake_clock) == 0
    other = FakeAPI(dataset=None, level="enterprise" if level == "organization" else "organization", slug=SLUG,
                       token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, clock=fake_clock)
    assert daily(data_dir, other, fake_clock, at("2026-09-21T06:00:00")) == 2
    assert "another organization" in capsys.readouterr().err
    assert not other.log
