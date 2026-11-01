"""S07 finality follows the rules of 4.6."""

import calendar
import importlib
from datetime import datetime, time, timedelta, timezone

import meterline

import meterline_sim  # noqa: F401
from sim_helpers import COPILOT_ENABLED, copy_run, jsonl_files, load_records, mutate_jsonl

vmod = importlib.import_module("meterline_sim.verify")


def expected_final(rec):
    pulled = rec.pulled_at
    start = rec.period_start
    month_end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    month_rule = (pulled.date() - month_end).days > 7
    if rec.provider == "anthropic":
        return month_rule
    ended = datetime.combine(start + timedelta(days=1), time(0, 0), tzinfo=timezone.utc)
    if rec.provider == "litellm":
        return pulled >= ended + timedelta(days=2)
    if rec.provider == "github_copilot":
        if isinstance(rec, meterline.OrgTotal) or rec.grain == "month":
            return month_rule
        return pulled >= ended + timedelta(days=4)
    raise AssertionError(rec.provider)


def finality_failures(folder):
    bad = []
    for kind in ("usage", "org_totals"):
        for _batch, rec in load_records(folder, kind):
            if rec.is_final != expected_final(rec):
                bad.append((kind, rec.provider, str(rec.period_start), rec.is_final))
    return bad


def test_done_check_S07_finality(tiny_run, vertical_run):
    for folder in (tiny_run, vertical_run):
        assert finality_failures(folder) == []
        assert vmod.v4_finality(folder) == []


def test_done_check_S07_existence_clause_tiny(tiny_run):
    main_ids = {"anthropic": set()}
    from sim_helpers import main_batch_id

    anthropic_main = main_batch_id(tiny_run, "anthropic")
    records = [r for b, r in load_records(tiny_run) if b == anthropic_main and hasattr(r, "is_final")]
    assert any(r.is_final for r in records)
    assert any(not r.is_final for r in records)
    if COPILOT_ENABLED:
        cop = main_batch_id(tiny_run, "github_copilot")
        days = [r for b, r in load_records(tiny_run, "usage") if b == cop and r.provider == "github_copilot" and r.grain == "day"]
        assert any(r.is_final for r in days) and any(not r.is_final for r in days)


def test_done_check_S07_negative_control_flipped_flag_fails(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    _kind, path = next((k, p) for k, p in jsonl_files(broken) if k == "usage")
    mutate_jsonl(path, lambda lines: [dict(lines[0], is_final=not lines[0]["is_final"])] + lines[1:])
    assert finality_failures(broken) != []
    assert vmod.v4_finality(broken) != []


def test_done_check_S07_negative_control_existence_clause_fails_when_every_copilot_day_is_final(tmp_path):
    from sim_helpers import gen, main_batch_id

    late = gen(tmp_path, "tiny", 7, 6, {"pull": {"lag_days": 30}})
    cop = main_batch_id(late, "github_copilot")
    days = [r for b, r in load_records(late, "usage") if b == cop and r.provider == "github_copilot" and r.grain == "day"]
    # the clause needs a final and a not-final Copilot day record; with a long lag there is no not-final one
    assert days and all(r.is_final for r in days)
    assert not (any(r.is_final for r in days) and any(not r.is_final for r in days))
