"""Ledger to silver records per provider, and the batches (spec 4, 6.3)."""

import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from meterline import (
    ActivityRecord,
    OrgTotal,
    SeatAssignment,
    SeatSnapshot,
    UsageRecord,
    actor_cost_failures,
    batch_failures,
)

from meterline_sim.behaviour import CORE8, COUNTERS5
from meterline_sim.org import anthropic_actor_id, anthropic_provider_account_id, copilot_actor_id
from meterline_sim.params import RunError

UTC = timezone.utc
D = Decimal
UNASSIGNED = "(unassigned)"


class ValidationFailure(RunError):
    """A batch failed validation before writing (exit 1)."""


def utc(day, hh, mm, ss=0):
    return datetime.combine(day, time(hh, mm, ss), tzinfo=UTC)


def batch_id_of(pulled_at):
    return pulled_at.strftime("%Y%m%dT%H%M%SZ")


def month_last_day(day):
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def final_by_month_rule(period_start, pulled_at):
    return (pulled_at.date() - month_last_day(period_start)).days > 7


def final_by_day_rule(day, pulled_at, extra_days):
    ended = utc(day + timedelta(days=1), 0, 0)
    return pulled_at >= ended + timedelta(days=extra_days)


class Batch:
    def __init__(self, provider, purpose, pulled_at):
        self.provider = provider
        self.purpose = purpose
        self.pulled_at = pulled_at
        self.batch_id = batch_id_of(pulled_at)
        self.records = {}

    def add(self, kind, record):
        self.records.setdefault(kind, []).append(record)


def _sum_rows(rows):
    out = {"requests": 0, "uncached": 0, "read": 0, "w5": 0, "w1": 0, "output": 0, "web": 0, "failed": 0,
           "amount": D(0), "priced": False}
    for r in rows:
        out["requests"] += r.requests
        out["uncached"] += r.uncached
        out["read"] += r.read
        out["w5"] += r.w5
        out["w1"] += r.w1
        out["output"] += r.output
        out["web"] += r.web
        out["failed"] += r.failed
        if r.amount is not None:
            out["amount"] += r.amount
            out["priced"] = True
    return out


def render_anthropic(ctx, usage_rows, activity_rows, pulled_at):
    """The Anthropic main batch (4.1)."""
    world, seed = ctx.world, ctx.seed
    batch = Batch("anthropic", "main", pulled_at)
    bid = batch.batch_id
    provider_account_id = anthropic_provider_account_id(seed)
    refreshed = pulled_at - timedelta(minutes=30)
    pulled_date = pulled_at.date()

    grouped = {}
    for row in usage_rows:
        grouped.setdefault((row.actor, row.day, row.product, row.model), []).append(row)
    day_sums = {}
    for (pid, day, product, model), rows in grouped.items():
        s = _sum_rows(rows)
        person = world.people[pid]
        deleted = person.last_day is not None and person.last_day <= pulled_date
        total_in = s["uncached"] + s["read"] + s["w5"] + s["w1"]
        batch.add("usage", UsageRecord(
            provider="anthropic", provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day,
            actor_kind="user", actor_id=anthropic_actor_id(seed, pid), actor_email=person.email,
            actor_deleted=deleted, product=product, model=model, model_provider="anthropic",
            tokens_input_uncached=s["uncached"], tokens_cache_read=s["read"],
            tokens_cache_write=s["w5"] + s["w1"], tokens_cache_write_5m=s["w5"], tokens_cache_write_1h=s["w1"],
            tokens_input_total=total_in, tokens_output=s["output"], requests=s["requests"],
            extras={"web_search_requests": s["web"]},
            cost=s["amount"], cost_list=s["amount"], currency="USD", cost_basis="consumption",
            is_final=final_by_month_rule(day, pulled_at), pulled_at=pulled_at, data_refreshed_at=refreshed,
            batch_id=bid))
        t = day_sums.setdefault(day, {"uncached": 0, "read": 0, "w5": 0, "w1": 0, "output": 0, "requests": 0,
                                      "web": 0, "amount": D(0)})
        t["uncached"] += s["uncached"]
        t["read"] += s["read"]
        t["w5"] += s["w5"]
        t["w1"] += s["w1"]
        t["output"] += s["output"]
        t["requests"] += s["requests"]
        t["web"] += s["web"]
        t["amount"] += s["amount"]

    for act in activity_rows:
        person = world.people[act.person]
        core = {name: act.core[name] for name in CORE8}
        batch.add("activity", ActivityRecord(
            provider="anthropic", provider_account_id=provider_account_id, grain="day", period_start=act.day, period_end=act.day,
            actor_kind="user", actor_id=anthropic_actor_id(seed, act.person), actor_email=person.email,
            product=act.product, counters={name: act.counters[name] for name in COUNTERS5},
            pulled_at=pulled_at, batch_id=bid, **core))

    active_by_day = {}
    for (pid, day, _p, _m) in grouped:
        active_by_day.setdefault(day, set()).add(pid)
    wk, mo = ctx.params["providers"]["anthropic"]["active_windows"]
    for day in ctx.days:
        zero = {"uncached": 0, "read": 0, "w5": 0, "w1": 0, "output": 0, "requests": 0, "web": 0, "amount": D(0)}
        t = day_sums.get(day, zero)
        total_in = t["uncached"] + t["read"] + t["w5"] + t["w1"]
        batch.add("org_totals", OrgTotal(
            provider="anthropic", provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day,
            product="", model="", model_provider="anthropic",
            tokens_input_uncached=t["uncached"], tokens_cache_read=t["read"],
            tokens_cache_write=t["w5"] + t["w1"], tokens_cache_write_5m=t["w5"], tokens_cache_write_1h=t["w1"],
            tokens_input_total=total_in, tokens_output=t["output"], requests=t["requests"],
            extras={"web_search_requests": t["web"]},
            cost=t["amount"], cost_list=t["amount"], currency="USD", cost_basis="consumption",
            is_final=final_by_month_rule(day, pulled_at), pulled_at=pulled_at, data_refreshed_at=refreshed,
            batch_id=bid))
        seats = sum(1 for pid in world.person_order if world.people[pid].employed(day))
        pending = sum(1 for pid in world.joiners
                      if world.people[pid].invite_date <= day < world.people[pid].start)
        weekly, monthly = set(), set()
        for back in range(wk):
            d = day - timedelta(days=back)
            if d >= ctx.start:
                weekly |= active_by_day.get(d, set())
        for back in range(mo):
            d = day - timedelta(days=back)
            if d >= ctx.start:
                monthly |= active_by_day.get(d, set())
        batch.add("seats", SeatSnapshot(
            provider="anthropic", provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day,
            seats=seats, pending_invites=pending, pending_cancellations=None,
            daily_active=len(active_by_day.get(day, ())), weekly_active=len(weekly), monthly_active=len(monthly),
            pulled_at=pulled_at, batch_id=bid))
    return batch


def render_litellm(ctx, rows, pulled_at):
    """The LiteLLM main batch (4.3)."""
    params = ctx.params["providers"]["litellm"]
    batch = Batch("litellm", "main", pulled_at)
    bid = batch.batch_id
    provider_account_id = params["proxy_name"]
    grouped = {}
    for row in rows:
        grouped.setdefault((row.day, row.model), []).append(row)
    for (day, model), group in grouped.items():
        s = _sum_rows(group)
        if s["priced"]:
            cost, currency, basis = s["amount"], "USD", "estimate"
        else:
            cost, currency, basis = None, "", None
        common = dict(
            provider="litellm", provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day, model=model,
            tokens_input_uncached=s["uncached"], tokens_cache_read=s["read"], tokens_cache_write=s["w5"],
            tokens_input_total=s["uncached"] + s["read"] + s["w5"], tokens_output=s["output"],
            requests=s["requests"],
            extras={"successful_requests": s["requests"] - s["failed"], "failed_requests": s["failed"]},
            cost=cost, cost_list=None, currency=currency, cost_basis=basis,
            is_final=final_by_day_rule(day, pulled_at, 2), pulled_at=pulled_at, batch_id=bid)
        batch.add("usage", UsageRecord(actor_kind="unassigned", actor_id=UNASSIGNED, product="", **common))
        batch.add("org_totals", OrgTotal(product="", **common))
    return batch


# ---- GitHub Copilot (4.2) -----------------------------------------------------------------------------

_NOT_A_CHANGE = ("pulled_at", "data_refreshed_at", "batch_id")
COPILOT = "github_copilot"
NO_FEATURE_COUNTERS = {"lines_suggested_to_add": 0, "lines_suggested_to_delete": 0}


def copilot_provider_account_id(ctx):
    return "org:" + ctx.org_slug


def _month_bounds(month_text):
    year, month = int(month_text[:4]), int(month_text[5:7])
    first = date(year, month, 1)
    return first, date(year, month, calendar.monthrange(year, month)[1])


def _feature(batch, base, product, counters=None, **core):
    """The activity record of one feature (5.7): the five integer fields are 0 unless given, and the
    two feature counters are always present. ``sessions`` and ``requests`` stay null off the CLI and app."""
    fields = dict(sessions=None, requests=None, messages=0, suggestions_shown=0, suggestions_accepted=0,
                  lines_added=0, lines_removed=0)
    fields.update(core)
    batch.add("activity", ActivityRecord(
        product=product, counters=dict(NO_FEATURE_COUNTERS, **(counters or {})), **fields, **base))


def render_copilot(ctx, rows, bill, pulled_at, purpose):
    """Every Copilot record of the period except seat assignments (4.2), as one batch at ``pulled_at``.

    ``rows`` are the Copilot ledger rows of one state, ``bill`` the CopilotBilling of the same state."""
    world, seats = ctx.world, ctx.copilot
    batch = Batch(COPILOT, purpose, pulled_at)
    bid = batch.batch_id
    provider_account_id = copilot_provider_account_id(ctx)
    price = bill.price

    def who(pid):
        person = world.people[pid]
        return dict(actor_kind="user", actor_id=copilot_actor_id(person.n), actor_login=person.login)

    active = {}
    for row in rows:
        active.setdefault(row.day, set()).add(row.person)
        day, final = row.day, final_by_day_rule(row.day, pulled_at, 4)
        base = dict(provider=COPILOT, provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day,
                    pulled_at=pulled_at, batch_id=bid, **who(row.person))
        cost = row.credits * price
        batch.add("usage", UsageRecord(
            product="copilot", model="", quantities={"ai_credit": row.credits}, cost=cost, cost_list=cost,
            currency="USD", cost_basis="consumption", is_final=final, **base))
        for product, prompts, tokens_in, tokens_out in (
                ("copilot_cli", row.cli_prompts, row.cli_in, row.cli_out),
                ("copilot_app", row.app_prompts, row.app_in, row.app_out)):
            if prompts:
                requests = prompts * 2
                batch.add("usage", UsageRecord(
                    product=product, model="", tokens_input_total=requests * tokens_in,
                    tokens_output=requests * tokens_out, requests=requests, is_final=final, **base))

        _feature(batch, base, "code_completion", counters={"lines_suggested_to_add": row.shown * 3},
                 suggestions_shown=row.shown, suggestions_accepted=row.accepted, lines_added=row.completion_lines)
        if row.ask > 0:
            _feature(batch, base, "chat_panel_ask_mode", messages=row.ask)
        if row.agent > 0:
            _feature(batch, base, "chat_panel_agent_mode", messages=row.agent, lines_added=row.agent_lines,
                     lines_removed=row.agent_deleted,
                     counters={"lines_suggested_to_add": row.agent_lines,
                               "lines_suggested_to_delete": row.agent_deleted})
        for product, prompts in (("copilot_cli", row.cli_prompts), ("copilot_app", row.app_prompts)):
            if prompts:
                _feature(batch, base, product, messages=prompts, sessions=(prompts + 2) // 3, requests=prompts * 2)
        batch.add("activity", ActivityRecord(
            product="copilot", counters={
                "custom_agents_used": row.custom_agents, "mcp_servers_used": row.mcp, "plugins_used": row.plugins,
                "skills_used": row.skills, "slash_commands_used": row.slash}, **base))

    for (pid, month, model), (gross, discount, net) in sorted(bill.person.items()):
        first, last = _month_bounds(month)
        batch.add("usage", UsageRecord(
            provider=COPILOT, provider_account_id=provider_account_id, grain="month", period_start=first, period_end=last,
            product="copilot", model=model, quantities={"ai_credit": gross}, cost=net * price,
            cost_list=gross * price, currency="USD", cost_basis="consumption",
            extras={"discount_quantity": discount, "net_quantity": net, "net_amount": net * price,
                    "price_per_unit": price},
            is_final=final_by_month_rule(first, pulled_at), pulled_at=pulled_at, batch_id=bid, **who(pid)))

    for (day, model), (gross, discount, net) in sorted(bill.org.items()):
        batch.add("org_totals", OrgTotal(
            provider=COPILOT, provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day, product="copilot",
            model=model, quantities={"ai_credit": gross}, cost=net * price, cost_list=gross * price,
            currency="USD", cost_basis="billed",
            extras={"discount_quantity": discount, "net_quantity": net, "net_amount": net * price,
                    "price_per_unit": price},
            is_final=final_by_month_rule(day, pulled_at), pulled_at=pulled_at, batch_id=bid))

    wk, mo = ctx.params["providers"]["github_copilot"]["active_windows"]
    intervals = seats.intervals()
    for day in ctx.days:
        # seat holders of the day, in person-id order; the active counts are those of holders only
        holders = [pid for pid, start, end in intervals if start <= day and (end is None or day <= end)]
        holder_set = set(holders)
        weekly, monthly = set(), set()
        for back in range(wk):
            d = day - timedelta(days=back)
            if d >= ctx.start:
                weekly |= active.get(d, set())
        for back in range(mo):
            d = day - timedelta(days=back)
            if d >= ctx.start:
                monthly |= active.get(d, set())
        left = sum(1 for pid in holders
                   if world.people[pid].last_day is not None and world.people[pid].last_day <= day)
        batch.add("seats", SeatSnapshot(
            provider=COPILOT, provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day, seats=len(holders),
            pending_invites=0, pending_cancellations=left,
            daily_active=len(active.get(day, set()) & holder_set), weekly_active=len(weekly & holder_set),
            monthly_active=len(monthly & holder_set), pulled_at=pulled_at, batch_id=bid))
    return batch


def render_copilot_seat_lists(ctx, rows):
    """One batch per day with at least one seat holder: its SeatAssignments (4.2, 6.3)."""
    world, seats = ctx.world, ctx.copilot
    provider_account_id = copilot_provider_account_id(ctx)
    active_days = {}
    for row in rows:
        active_days.setdefault(row.person, []).append(row.day)
    batches = []
    intervals = seats.intervals()
    for day in ctx.days:
        batch = Batch(COPILOT, "seat_list", utc(day, 6, 10))
        for pid, start, end in intervals:
            if not (start <= day and (end is None or day <= end)):
                continue
            person, seat = world.people[pid], seats.seats[pid]
            before = [d for d in active_days.get(pid, ()) if d < day]
            last_active = utc(max(before), 12, 0) if before else None
            cancel = seats.cancellation_date(pid)
            visible = cancel if person.last_day is not None and person.last_day <= day else None
            batch.add("seat_assignments", SeatAssignment(
                provider=COPILOT, provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day,
                actor_kind="user", actor_id=copilot_actor_id(person.n), actor_login=person.login,
                plan=seats.plan, assigned_at=utc(seat.assigned, 0, 0), last_active_at=last_active,
                last_authenticated_at=last_active, pending_cancellation_date=visible, granted_by=seat.granted_by,
                pulled_at=batch.pulled_at, batch_id=batch.batch_id))
        if batch.records:
            batches.append(batch)
    return batches


def _content(record):
    return {k: v for k, v in record.to_dict().items() if k not in _NOT_A_CHANGE}


def revisions(correction, earlier):
    """The records of ``correction`` that differ from the latest earlier record of their key (7.4),
    comparing every field except pulled_at, data_refreshed_at and batch_id. Returns a new batch."""
    out = Batch(correction.provider, correction.purpose, correction.pulled_at)
    for kind in sorted(correction.records):
        before = {rec.key(): rec for rec in earlier.records.get(kind, ())}
        for rec in correction.records[kind]:
            prev = before.get(rec.key())
            if prev is None or _content(prev) != _content(rec):
                out.add(kind, rec)
    return out


def validate_batches(batches):
    """Section 4.5: every file passes ``batch_failures``; main batches also the actor-cost check."""
    for batch in batches:
        today = batch.pulled_at.date()
        for kind in sorted(batch.records):
            failures = batch_failures(batch.records[kind], today)
            if failures:
                raise ValidationFailure("%s %s: %s" % (batch.batch_id, kind, "; ".join(failures)))
        if batch.provider in ("anthropic", "litellm") and batch.purpose == "main":
            failures = actor_cost_failures(batch.records.get("usage", []), batch.records.get("org_totals", []))
            if failures:
                raise ValidationFailure("%s: %s" % (batch.batch_id, "; ".join(failures)))
