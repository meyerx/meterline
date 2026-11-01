"""Done-checks C02 to C12 (spec v2 section 8), through the command line and the
fake API, on synthetic data. C01 (the shapes) is in its own file. Each runs at
both levels (organization and enterprise) except C10, which is the enterprise
level by its text."""

import ast
import gzip
import json
import logging
import re
from decimal import Decimal
from pathlib import Path

from meterline_copilot.testing import FakeAPI, aggregate_row, feature, item, seat, user_row
from copilot_helpers import (
    FAKE_BILLING_TOKEN, FAKE_TOKEN, NOW, SLUG, at, daily, backfill, latest, manifests, records,
)

DAY = "2026-09-18"


def _usage(data_dir, product="copilot"):
    return {(r.actor_id, r.period_start.isoformat(), r.grain): r for r in records(data_dir, "usage") if r.product == product}


def test_done_check_C02_two_links_and_no_token_to_the_download_host(make_api, fake_clock, data_dir, level):
    api = make_api(links_per_report=2)
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level), user_row(DAY, 2, "login2", level=level)]
    assert daily(data_dir, api, fake_clock) == 0
    got = _usage(data_dir)
    assert {k[0] for k in got if k[1] == DAY} == {"1", "2"}  # one row came from each file
    assert api.downloads(), "no download was made"
    assert len([d for d in api.downloads() if "users-%s-" % DAY in d.path]) == 2
    for entry in api.downloads():
        assert entry.authorization is None
        assert "authorization" not in entry.headers
    assert api.api_requests() and all(e.authorization for e in api.api_requests())


def test_done_check_C03_credits_become_quantity_and_cost(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, credits=Decimal("12.5"))]
    assert daily(data_dir, api, fake_clock) == 0
    rec = _usage(data_dir)[("1", DAY, "day")]
    assert rec.quantities == {"ai_credit": Decimal("12.5")}
    assert rec.cost == Decimal("0.125") and rec.cost_list == Decimal("0.125")
    assert (rec.currency, rec.cost_basis) == ("USD", "consumption")
    assert (rec.actor_kind, rec.actor_login, rec.actor_email, rec.model, rec.provider) == ("user", "login1", None, "", "github_copilot")
    assert rec.tokens_input_total is None and rec.tokens_output is None and rec.tokens_cache_read is None
    text = "".join(p.read_text(encoding="utf-8") for p in (data_dir / "records" / "usage").glob("*.jsonl"))
    assert '"cost": "0.125"' in text and '"ai_credit": "12.5"' in text
    assert rec.provider_account_id == ("org:" if level == "organization" else "enterprise:") + SLUG


def test_done_check_C04_cli_and_app_tokens(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, cli=True, app=True)]
    assert daily(data_dir, api, fake_clock) == 0
    cli = _usage(data_dir, "copilot_cli")[("1", DAY, "day")]
    app = _usage(data_dir, "copilot_app")[("1", DAY, "day")]
    assert (cli.tokens_input_total, cli.tokens_output, cli.requests) == (3800, 5000, 2)
    assert (app.tokens_input_total, app.tokens_output, app.requests) == (5400, 4200, 3)
    for rec in (cli, app):
        assert (rec.tokens_input_uncached, rec.tokens_cache_read, rec.tokens_cache_write) == (None, None, None)
        assert rec.quantities == {} and rec.cost is None and rec.cost_basis is None and rec.currency == ""
    # credits stay on the product "copilot" record only
    assert _usage(data_dir)[("1", DAY, "day")].quantities == {"ai_credit": Decimal("1.5")}


def test_done_check_C05_features_sum_to_row_and_mismatch_is_listed(make_api, fake_clock, data_dir, level):
    api = make_api()
    feats = [feature("code_completion", gen=5, acc=3, added=20, deleted=2, sug_add=25, sug_del=3, msgs=0),
             feature("chat_panel_ask_mode", gen=2, acc=1, added=4, deleted=0, sug_add=6, sug_del=0, msgs=7)]
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, features=feats)]
    assert daily(data_dir, api, fake_clock) == 0
    acts = [r for r in records(data_dir, "activity") if r.period_start.isoformat() == DAY and r.product != "copilot"]
    assert sorted(r.product for r in acts) == ["chat_panel_ask_mode", "code_completion"]
    assert sum(r.suggestions_shown for r in acts) == 7
    assert sum(r.suggestions_accepted for r in acts) == 4
    assert sum(r.lines_added for r in acts) == 24 and sum(r.lines_removed for r in acts) == 2
    assert sum(r.messages for r in acts) == 7
    assert sum(r.counters["lines_suggested_to_add"] for r in acts) == 31
    assert manifests(data_dir)[-1]["feature_sum_mismatch"] == []

    api2 = make_api()
    bad = user_row(DAY, 1, "login1", level=level, features=feats)
    bad["code_generation_activity_count"] += 5  # the features no longer add up
    api2.users[DAY] = [bad]
    other = data_dir.parent / "mismatch"
    assert daily(other, api2, fake_clock) == 0
    found = manifests(other)[-1]["feature_sum_mismatch"]
    assert len(found) == 1 and "login1" not in found[0]
    assert records(other, "activity")  # the run still published


def test_done_check_C06_per_person_billing_calls(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.users["2026-09-17"] = [user_row("2026-09-17", 1, "login-x", level=level)]
    api.users[DAY] = [user_row(DAY, 2, "login-y", level=level)]
    api.seats = [seat("login-y", 2), seat("login-z", 3)]
    for who in ("login-x", "login-y", "login-z"):
        api.billing_user[(who, 2026, 9)] = [item("Copilot", "GPT-5", gross_a=Decimal("2"), disc_a=Decimal("0.5")),
                                            item("Copilot", "Claude", gross_a=Decimal("1"))]
    assert daily(data_dir, api, fake_clock) == 0
    calls = [e.params for e in api.billing_requests() if "user" in e.params]
    assert sorted((c["user"], c["year"], c["month"]) for c in calls) == [
        ("login-x", "2026", "9"), ("login-y", "2026", "9"), ("login-z", "2026", "9")]
    months = [r for r in records(data_dir, "usage") if r.grain == "month"]
    assert len(months) == 6  # three people, two models
    assert {r.period_start.isoformat() for r in months} == {"2026-09-01"}
    assert {r.period_end.isoformat() for r in months} == {"2026-09-30"}
    assert {r.cost_basis for r in months} == {"consumption"}
    assert all("net_amount" in r.extras for r in months)
    by = {(r.actor_login, r.model): r for r in months}
    gpt = by[("login-x", "GPT-5")]
    assert (gpt.cost_list, gpt.cost, gpt.extras["net_amount"]) == (Decimal("2"), Decimal("1.5"), Decimal("1.5"))
    assert gpt.quantities == {"ai_credit": Decimal("100")}
    assert (by[("login-x", "GPT-5")].actor_id, by[("login-y", "GPT-5")].actor_id, by[("login-z", "GPT-5")].actor_id) == ("1", "2", "3")
    assert gpt.product == "copilot" and gpt.is_final is False


def test_done_check_C07_organization_day_per_model_is_billed(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.billing_day[DAY] = [item("Copilot", "GPT-5", gross_q=1000, gross_a=Decimal("10"), disc_q=300, disc_a=Decimal("3")),
                            item("Copilot", "Claude", gross_q=50, gross_a=Decimal("0.5"))]
    assert daily(data_dir, api, fake_clock) == 0
    totals = {(r.period_start.isoformat(), r.model): r for r in records(data_dir, "org_totals")}
    gpt = totals[(DAY, "GPT-5")]
    assert (gpt.cost, gpt.cost_list, gpt.cost_basis, gpt.currency, gpt.grain) == (Decimal("7"), Decimal("10"), "billed", "USD", "day")
    assert gpt.quantities == {"ai_credit": Decimal("1000")}
    assert gpt.product == "copilot" and gpt.provider == "github_copilot"
    assert totals[(DAY, "Claude")].cost == Decimal("0.5")
    assert gpt.provider_account_id == ("org:" if level == "organization" else "enterprise:") + SLUG


def test_done_check_C08_unit_names_and_product_merge(make_api, fake_clock, data_dir, level):
    api = make_api()
    api.billing_day[DAY] = [
        item("Copilot", "GPT-5", "credits", 100, Decimal("1")),
        item("copilot", "GPT-5", "ai-credits", 50, Decimal("0.5")),
        item("Copilot", "GPT-5", "minutes", 10, Decimal("0.2")),
        item("Copilot", "Claude", "ai_credits", 5, Decimal("0.05")),
        item("Copilot", "Claude", "requests", 3, Decimal("0.12")),
    ]
    assert daily(data_dir, api, fake_clock) == 0
    totals = [r for r in records(data_dir, "org_totals") if r.period_start.isoformat() == DAY]
    assert len(totals) == 2  # Copilot and copilot are one product
    gpt = next(r for r in totals if r.model == "GPT-5")
    assert gpt.quantities == {"ai_credit": Decimal("150"), "minutes": Decimal("10")}
    assert gpt.cost_list == Decimal("1.70")
    claude = next(r for r in totals if r.model == "Claude")
    assert claude.quantities == {"ai_credit": Decimal("5"), "premium_request": Decimal("3")}
    assert manifests(data_dir)[-1]["unknown_units"] == ["minutes"]


def test_done_check_C09_changed_credits_in_the_window_revise(make_api, fake_clock, data_dir, level):
    api = make_api()
    day = "2026-09-17"
    api.users[day] = [user_row(day, 1, "login1", level=level, credits=Decimal("5"))]
    assert daily(data_dir, api, fake_clock, NOW) == 0
    first = sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl"))
    assert len(first) == 1
    assert daily(data_dir, api, fake_clock, at("2026-09-20T07:00:00")) == 0  # unchanged: nothing new
    assert sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl")) == first

    api.users[day] = [user_row(day, 1, "login1", level=level, credits=Decimal("6.25"))]
    assert daily(data_dir, api, fake_clock, at("2026-09-20T08:00:00")) == 0
    files = sorted(p.name for p in (data_dir / "records" / "usage").glob("*.jsonl"))
    assert len(files) == 2
    new = latest(data_dir, "usage")
    key = next(k for k, r in new.items() if r.period_start.isoformat() == day and r.product == "copilot")
    assert new[key].quantities == {"ai_credit": Decimal("6.25")}
    assert new[key].batch_id == files[-1].replace(".jsonl", "")
    assert new[key].is_final is False
    assert len([r for r in records(data_dir, "usage") if r.period_start.isoformat() == day]) == 2


def test_done_check_C10_enterprise_seat_duplicates_and_snapshot_revision(fake_clock, data_dir):
    api = FakeAPI(dataset=None, level="enterprise", slug=SLUG, token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, clock=fake_clock)
    api.seats = [
        seat("octocat", 1, created="2026-09-02T10:00:00+02:00", team="team-b", last_activity="2026-09-10T10:00:00+02:00"),
        seat("octocat", 1, created="2026-09-01T10:00:00+02:00", team="team-a", last_activity="2026-09-12T10:00:00+02:00"),
        seat("login2", 2, plan=None, team=None, cancel="2026-10-01"),
    ]
    api.total_seats = 2
    api.aggregates["2026-09-20"] = aggregate_row("2026-09-20", 5, 6, 7)

    assert daily(data_dir, api, fake_clock, at("2026-09-20T06:00:00")) == 0
    assigns = {r.actor_id: r for r in records(data_dir, "seat_assignments")}
    assert sorted(assigns) == ["1", "2"]
    one = assigns["1"]
    assert one.assigned_at == at("2026-09-01T08:00:00")
    assert one.granted_by == "team-a,team-b"
    assert one.last_active_at == at("2026-09-12T08:00:00")
    assert one.period_start.isoformat() == "2026-09-20" and one.plan == "business"
    assert assigns["2"].plan is None and assigns["2"].granted_by is None
    assert assigns["2"].pending_cancellation_date.isoformat() == "2026-10-01"

    snaps = {r.period_start.isoformat(): r for r in records(data_dir, "seats")}
    first = snaps["2026-09-20"]
    assert first.seats == 2
    assert (first.daily_active, first.weekly_active, first.monthly_active) == (None, None, None)
    assert first.pending_invites is None and first.pending_cancellations is None  # no summary at enterprise level

    assert daily(data_dir, api, fake_clock, at("2026-09-21T06:00:00")) == 0
    cur = latest(data_dir, "seats")
    key = next(k for k in cur if k[-1].isoformat() == "2026-09-20")
    assert cur[key].seats == 2
    assert (cur[key].daily_active, cur[key].weekly_active, cur[key].monthly_active) == (5, 6, 7)
    assert cur[key].batch_id > first.batch_id
    assert len([r for r in records(data_dir, "seats") if r.period_start.isoformat() == "2026-09-20"]) == 2


def test_done_check_C11_rate_limit_headers_and_request_rate(make_api, fake_clock, data_dir, fake_env, level):
    from meterline_copilot.client import CopilotClient

    api = make_api()
    root = ("/orgs/" if level == "organization" else "/enterprises/") + SLUG
    api.seats = [seat("login1", 1)]
    api.fail("/copilot/billing/seats", 403, times=1, headers={"retry-after": "7"})
    reset = int(fake_clock()) + 40  # an absolute time, as the header is: 40 seconds from now
    api.fail("/copilot/billing/seats", 429, times=1, headers={"x-ratelimit-reset": str(reset)})
    with CopilotClient(fake_env, billing_token=FAKE_BILLING_TOKEN, transport=api.transport, clock=fake_clock,
                       sleep=fake_clock.sleep, wall=fake_clock, jitter=lambda: 0) as client:
        body = client.get_json(root + "/copilot/billing/seats", {"page": "1", "per_page": "100"})
    assert body["total_seats"] == 1
    statuses = [e.status for e in api.api_requests("/copilot/billing/seats")]
    assert statuses == [403, 429, 200]
    assert fake_clock.sleeps[0] == 7  # retry-after was waited out first
    assert fake_clock() >= reset  # then the time to x-ratelimit-reset, not an immediate retry

    # 300 or more requests never exceed the configured rate in any hour
    big = make_api()
    clock = fake_clock
    assert backfill(data_dir, big, clock, "2026-06-01", NOW, "--rate-per-hour", "100") == 0
    times = [e.time for e in big.api_requests()]
    assert len(times) >= 300
    for i, t in enumerate(times):
        in_window = [x for x in times[: i + 1] if t - x < 3600]
        assert len(in_window) <= 100


def test_done_check_C12_no_token_and_no_foreign_email_anywhere(make_api, fake_clock, data_dir, level, caplog, capsys):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level, cli=True, app=True)]
    api.aggregates[DAY] = aggregate_row(DAY, 1, 1, 1)
    api.billing_day[DAY] = [item()]
    api.billing_user[("login1", 2026, 9)] = [item()]
    api.seats = [seat("login1", 1)]
    api.fail("/copilot/billing/seats", 429, times=1, headers={"retry-after": "1"})
    with caplog.at_level(logging.DEBUG):
        assert daily(data_dir, api, fake_clock) == 0
    out = capsys.readouterr()
    log_text = caplog.text + out.out + out.err

    package = Path(__file__).resolve().parents[1]
    texts = {}
    for path in list((package / "src").rglob("*.py")) + list((package / "tests" / "fixtures").rglob("*")) \
            + [p for p in (data_dir).rglob("*") if p.is_file()]:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            data = gzip.decompress(data)
        texts[str(path)] = data.decode("utf-8", errors="ignore")
    assert any("/raw/" in p for p in texts) and any("/runs/" in p for p in texts)
    texts["<log output>"] = log_text
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
    for where, text in texts.items():
        for secret in (FAKE_TOKEN, FAKE_BILLING_TOKEN):
            assert secret not in text, where
        for found in email.findall(text):
            assert found.lower().endswith("@example.com"), (where, found)
    assert "login1" not in log_text
