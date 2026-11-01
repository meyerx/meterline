"""Done-checks F01 to F07 of the v2 spec (section 8, format, package meterline).

The names of the v2 types are read from the package at test time
(``ml.SeatAssignment``), so each check fails for its own reason against code
that has no v2 format yet.
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import meterline as ml

T0 = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 9)
BATCH = "20261009T080000Z"

V1_RECORDS = (
    Path(__file__).resolve().parents[2]
    / "meterline-anthropic" / "tests" / "fixtures" / "v1_published" / "records"
)


def usage(**over):
    base = dict(
        record_version=2, provider="anthropic", provider_account_id="org-1", scope="", grain="day",
        period_start=date(2026, 9, 15), period_end=date(2026, 9, 15),
        actor_kind="user", actor_id="user-001", actor_email="a.b@example.com",
        actor_login=None, actor_deleted=False, product="claude_code", model="model-a",
        model_provider="anthropic",
        tokens_input_uncached=10, tokens_cache_read=5, tokens_cache_write=1,
        tokens_cache_write_5m=1, tokens_cache_write_1h=0, tokens_input_total=16,
        tokens_output=20, tokens_reasoning=None, requests=3, quantities={},
        cost=Decimal("412.80"), cost_list=Decimal("450.00"), currency="USD",
        cost_basis="consumption", is_final=False, cost_only=False,
        extras={"web_search_requests": 0},
        pulled_at=T0, data_refreshed_at=T0, batch_id=BATCH,
    )
    base.update(over)
    return ml.UsageRecord(**base)


def activity(**over):
    base = dict(
        record_version=2, provider="anthropic", provider_account_id="org-1", scope="", grain="day",
        period_start=date(2026, 9, 15), period_end=date(2026, 9, 15),
        actor_kind="user", actor_id="user-001", actor_email=None, actor_login=None,
        product="chat", sessions=None, messages=2, requests=None, suggestions_shown=None,
        suggestions_accepted=None, lines_added=None, lines_removed=None,
        edits_accepted=None, edits_rejected=None, commits=None, pull_requests=None,
        counters={"conversations": 1}, pulled_at=T0, data_refreshed_at=None, batch_id=BATCH,
    )
    base.update(over)
    return ml.ActivityRecord(**base)


def seat_snapshot(**over):
    base = dict(
        record_version=2, provider="github_copilot", provider_account_id="org:acme", scope="", grain="day",
        period_start=date(2026, 9, 15), period_end=date(2026, 9, 15), seats=50,
        pending_invites=1, pending_cancellations=None, daily_active=10, weekly_active=20,
        monthly_active=30, pulled_at=T0, data_refreshed_at=None, batch_id=BATCH,
    )
    base.update(over)
    return ml.SeatSnapshot(**base)


def seat_assignment(**over):
    base = dict(
        record_version=2, provider="github_copilot", provider_account_id="org:acme", scope="", grain="day",
        period_start=date(2026, 10, 9), period_end=date(2026, 10, 9), actor_kind="user",
        actor_id="1001", actor_email=None, actor_login="login-a", plan="business",
        assigned_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        last_active_at=datetime(2026, 10, 8, 17, 30, tzinfo=timezone.utc),
        last_authenticated_at=None, pending_cancellation_date=date(2026, 11, 1),
        granted_by="team-a,team-b", pulled_at=T0, data_refreshed_at=None, batch_id=BATCH,
    )
    base.update(over)
    return ml.SeatAssignment(**base)


def org_total(**over):
    base = dict(
        record_version=2, provider="anthropic", provider_account_id="org-1", scope="", grain="day",
        period_start=date(2026, 9, 15), period_end=date(2026, 9, 15), product="", model="",
        model_provider="anthropic", tokens_input_uncached=100, tokens_cache_read=50,
        tokens_cache_write=10, tokens_cache_write_5m=10, tokens_cache_write_1h=0,
        tokens_input_total=160, tokens_output=70, tokens_reasoning=None, requests=None,
        quantities={}, cost=Decimal("1.5"), cost_list=None, currency="USD",
        cost_basis="consumption", is_final=True, extras={"web_search_requests": 4},
        pulled_at=T0, data_refreshed_at=T0, batch_id=BATCH,
    )
    base.update(over)
    return ml.OrgTotal(**base)


EXAMPLES = (
    ("usage", usage, "UsageRecord"),
    ("activity", activity, "ActivityRecord"),
    ("seats", seat_snapshot, "SeatSnapshot"),
    ("seat_assignments", seat_assignment, "SeatAssignment"),
    ("org_totals", org_total, "OrgTotal"),
)


def _rejected(record, today=TODAY):
    with pytest.raises(ml.RecordError):
        ml.validate(record, today)


# ---- F01 ---------------------------------------------------------------------


@pytest.mark.parametrize("kind,make,cls_name", EXAMPLES, ids=[e[0] for e in EXAMPLES])
def test_done_check_F01_records_round_trip(tmp_path, kind, make, cls_name):
    cls = getattr(ml, cls_name)
    rec = make()
    path = tmp_path / "records" / kind / (BATCH + ".jsonl")
    assert ml.write_jsonl([rec], path) == 1
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw
    parsed = json.loads(raw.decode("utf-8").splitlines()[0])
    assert parsed["record_version"] == 2
    assert list(parsed) == sorted(parsed)
    ml.validate(rec, TODAY)
    assert ml.read_jsonl(path, cls) == [rec]


# ---- F02 ---------------------------------------------------------------------

# v1 compatibility history: the v1 files call the provider account org_id; convert_v1 writes provider_account_id
V1_KEYS = {
    "usage": ("provider", "org_id", "period_start", "actor_id", "product", "model"),
    "activity": ("provider", "org_id", "period_start", "actor_id", "product"),
    "seats": ("provider", "org_id", "period_start"),
    "org_totals": ("provider", "org_id", "period_start"),
}
V2_NAME = {"org_id": "provider_account_id"}

V1_EXTRA_LINES = {
    "usage": [
        # a cost row with no usage row (v1 cost-only), a null cost, a deleted actor, null requests
        {"actor_deleted": True, "actor_email": None, "actor_id": "user-x1", "batch_id": BATCH,
         "cost": "3.25", "cost_list": "3.50", "cost_only": True, "currency": "USD",
         "data_refreshed_at": None, "is_final": False, "model": "model-a", "org_id": "org-1",
         "period_end": "2026-10-02", "period_start": "2026-10-02", "product": "chat",
         "provider": "anthropic", "pulled_at": "2026-10-09T08:00:00+00:00", "requests": None,
         "tokens_cache_read": 0, "tokens_cache_write_1h": 0, "tokens_cache_write_5m": 0,
         "tokens_input": 0, "tokens_output": 0, "web_search_requests": 0},
        {"actor_deleted": False, "actor_email": "x@example.com", "actor_id": "user-x2",
         "batch_id": BATCH, "cost": None, "cost_list": None, "cost_only": False, "currency": "",
         "data_refreshed_at": "2026-10-09T07:00:00+00:00", "is_final": False, "model": "model-b",
         "org_id": "org-1", "period_end": "2026-10-02", "period_start": "2026-10-02",
         "product": "claude_code", "provider": "anthropic",
         "pulled_at": "2026-10-09T08:00:00+00:00", "requests": 4, "tokens_cache_read": 7,
         "tokens_cache_write_1h": 2, "tokens_cache_write_5m": 3, "tokens_input": 11,
         "tokens_output": 5, "web_search_requests": 2},
    ],
}


def _v1_lines():
    out = {kind: [] for kind in V1_KEYS}
    for kind in V1_KEYS:
        for path in sorted((V1_RECORDS / kind).glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out[kind].append(json.loads(line))
        out[kind].extend(V1_EXTRA_LINES.get(kind, []))
    return out


def _check_table(kind, v1, v2):
    """One assertion per row of the section 4 table that applies to ``kind``."""
    assert v2["record_version"] == 2
    assert (v2["scope"], v2["grain"]) == ("", "day")
    for name in ("provider", "org_id", "period_start", "period_end", "pulled_at",
                 "data_refreshed_at", "batch_id"):
        assert v2[V2_NAME.get(name, name)] == v1[name], name
    assert "org_id" not in v2
    if kind in ("usage", "org_totals"):
        assert v2["tokens_input_uncached"] == v1["tokens_input"]
        assert v2["tokens_cache_write_5m"] == v1["tokens_cache_write_5m"]
        assert v2["tokens_cache_write_1h"] == v1["tokens_cache_write_1h"]
        assert v2["tokens_cache_write"] == v1["tokens_cache_write_5m"] + v1["tokens_cache_write_1h"]
        assert v2["tokens_cache_read"] == v1["tokens_cache_read"]
        assert v2["tokens_input_total"] == (
            v1["tokens_input"] + v1["tokens_cache_read"]
            + v1["tokens_cache_write_5m"] + v1["tokens_cache_write_1h"]
        )
        assert v2["tokens_output"] == v1["tokens_output"] and v2["tokens_reasoning"] is None
        assert v2["extras"] == {"web_search_requests": v1["web_search_requests"]}
        for name in ("cost", "cost_list", "currency", "requests", "is_final"):
            assert v2[name] == v1[name], name
        has_cost = v1["cost"] is not None or v1["cost_list"] is not None
        assert v2["cost_basis"] == ("consumption" if has_cost else None)
        assert v2["quantities"] == {} and v2["model_provider"] == "anthropic"
    if kind == "usage":
        assert v2["actor_deleted"] == v1["actor_deleted"]
        assert (v2["actor_kind"], v2["actor_login"]) == ("user", None)
        for name in ("actor_id", "actor_email", "product", "model", "cost_only"):
            assert v2[name] == v1[name], name
    if kind == "org_totals":
        assert (v2["product"], v2["model"]) == ("", "")
    if kind == "activity":
        assert (v2["actor_kind"], v2["actor_login"]) == ("user", None)
        for name in ("actor_id", "actor_email", "product"):
            assert v2[name] == v1[name], name
        five = ("conversations", "artifacts", "skills_used", "connectors_used", "web_searches")
        assert v2["counters"] == {name: v1[name] for name in five}
        for name in ("sessions", "messages", "commits", "pull_requests", "lines_added",
                     "lines_removed", "edits_accepted", "edits_rejected"):
            assert v2[name] == v1[name], name
        for name in ("requests", "suggestions_shown", "suggestions_accepted"):
            assert v2[name] is None, name
        for name in five:
            assert name not in v2
    if kind == "seats":
        assert v2["pending_cancellations"] is None
        for name in ("seats", "pending_invites", "daily_active", "weekly_active", "monthly_active"):
            assert v2[name] == v1[name], name


def test_done_check_F02_convert_v1_holds_every_v1_line():
    lines = _v1_lines()
    assert all(lines[kind] for kind in lines)
    for kind, v1_lines in lines.items():
        cls = ml.KINDS[kind]
        converted = []
        for v1 in v1_lines:
            v2 = ml.convert_v1(v1, kind)
            assert v2 != v1
            _check_table(kind, v1, v2)
            rec = cls.from_dict(v2)
            ml.validate(rec, rec.pulled_at.date())
            converted.append((v1, v2))
        assert len(converted) == len(v1_lines)
        v1_keys = {tuple(v1[n] for n in V1_KEYS[kind]) for v1, _ in converted}
        back = {tuple(v2[V2_NAME.get(n, n)] for n in V1_KEYS[kind]) for _, v2 in converted}
        assert back == v1_keys


# ---- F03 ---------------------------------------------------------------------


def test_done_check_F03_token_sums():
    ml.validate(usage(), TODAY)
    _rejected(usage(tokens_input_total=17))
    ml.validate(usage(tokens_cache_write=None, tokens_cache_write_5m=None,
                      tokens_cache_write_1h=None, tokens_input_total=999), TODAY)
    ml.validate(usage(tokens_input_uncached=None, tokens_input_total=0), TODAY)
    _rejected(usage(tokens_cache_write=2, tokens_input_total=17))
    _rejected(usage(tokens_cache_write_5m=1, tokens_cache_write_1h=1, tokens_cache_write=1,
                    tokens_input_total=16))


# ---- F04 ---------------------------------------------------------------------


def test_done_check_F04_grain_matches_dates():
    _rejected(usage(grain="day", period_start=date(2026, 9, 14), period_end=date(2026, 9, 15)))
    month = dict(grain="month", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30))
    ml.validate(usage(**month), TODAY)
    _rejected(usage(grain="month", period_start=date(2026, 9, 1), period_end=date(2026, 9, 29)))
    pulled = date(2026, 10, 15)
    ml.validate(usage(grain="month", period_start=date(2026, 10, 1),
                      period_end=date(2026, 10, 31)), pulled)
    _rejected(usage(grain="day", period_start=date(2026, 10, 16), period_end=date(2026, 10, 16)),
              pulled)
    ml.validate(usage(grain="window", period_start=date(2026, 9, 3), period_end=date(2026, 9, 9)),
                TODAY)
    _rejected(usage(grain="window", period_start=date(2026, 9, 3), period_end=date(2026, 9, 3)))


# ---- F05 ---------------------------------------------------------------------


def test_done_check_F05_missing_nullable_counter_is_null_not_zero(tmp_path):
    line = activity().to_dict()
    for name in ("sessions", "requests", "suggestions_shown", "commits"):
        line.pop(name, None)
    rec = ml.ActivityRecord.from_dict(line)
    assert rec.sessions is None and rec.requests is None and rec.commits is None
    path = tmp_path / "a.jsonl"
    ml.write_jsonl([rec], path)
    back = json.loads(path.read_text(encoding="utf-8"))
    for name in ("sessions", "requests", "suggestions_shown", "commits"):
        assert name in back and back[name] is None
    assert back["messages"] == 2


# ---- F06 ---------------------------------------------------------------------


def test_done_check_F06_keys_and_duplicates():
    base = usage()
    assert base.key() != usage(grain="window", period_end=date(2026, 9, 16)).key()
    assert base.key() != usage(actor_kind="api_key").key()
    assert base.key() != usage(scope="project-a").key()
    twin = usage(tokens_output=21, tokens_input_total=16, batch_id="20261009T090000Z")
    assert base.key() == twin.key()
    assert ml.batch_failures([base, usage(actor_kind="api_key")], TODAY) == []
    failures = ml.batch_failures([base, twin], TODAY)
    assert failures and any("duplicate" in f for f in failures)
    with pytest.raises(ml.RecordError):
        ml.validate_batch([base, twin], TODAY)


# ---- F07 ---------------------------------------------------------------------


def test_done_check_F07_cost_basis_and_quantity_names():
    _rejected(usage(cost_basis=None))
    _rejected(usage(cost=None, cost_list=None, currency=""))
    _rejected(usage(cost=None, cost_list=None, currency="", cost_basis="billed"))
    _rejected(usage(quantities={"AI Credit": Decimal("1")}))
    ml.validate(usage(quantities={"ai_credit": Decimal("12.5")}), TODAY)
    ml.validate(usage(cost=None, cost_list=None, currency="", cost_basis=None), TODAY)


# ---- F08 ---------------------------------------------------------------------

FIT = Path(__file__).resolve().parents[3] / "docs" / "api-shapes" / "fit"


def test_done_check_F08_five_more_tools_fit_the_format():
    import fit_mapping

    mapped = fit_mapping.map_all(FIT)
    assert set(mapped) == {"cursor", "openai", "augment", "kiro", "windsurf"}
    for tool, records in mapped.items():
        assert records, tool
        for kind in {type(r) for r in records}:
            same = [r for r in records if type(r) is kind]
            assert ml.batch_failures(same, TODAY) == [], (tool, kind.__name__)
    # two units on one record: the reason ``quantities`` is a map
    windsurf = mapped["windsurf"]
    assert set(windsurf[0].quantities) == {"prompt_credit", "flex_credit"}
    assert all(r.tokens_output is None and r.cost is None for r in windsurf)
    cursor_usage = [r for r in mapped["cursor"] if isinstance(r, ml.UsageRecord)]
    dana = next(r for r in cursor_usage if r.actor_email == "dana.cole@example.com")
    assert dana.cost_list == Decimal("20.25") / 100 and dana.cost == Decimal("16.75") / 100
    assert dana.quantities == {"request_unit": Decimal("2.5")}
