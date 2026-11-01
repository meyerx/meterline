"""The five events (spec 7): joiners and leavers, the reorganisation, the runaway agent,
the late correction of Copilot credits and the price change."""

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from meterline import quantity_str

from meterline_sim.behaviour import UsageRow, tokens_from_medians
from meterline_sim.draws import bern, pick, round_half_up, shuffle, stoch_round, uniform_int
from meterline_sim.params import PROFILES, ParamError

D = Decimal


def add_months(day, months):
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return year, month + 1


def period_end(start, months):
    year, month = add_months(start, months)
    last_dom = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_dom)) - timedelta(days=1)


def month_ranges(start, end):
    """[(first, last)] of the calendar months that intersect [start, end] (6.1)."""
    out = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        first = max(date(year, month, 1), start)
        last = min(date(year, month, calendar.monthrange(year, month)[1]), end)
        out.append((first, last))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


class EventLog:
    def __init__(self):
        self.items = []

    def add(self, kind, dates, actors, details):
        self.items.append({"type": kind, "dates": list(dates), "actors": list(actors), "details": details})


def _heads(world, day):
    return {world.units[u]["head_person_id"] for u in world.line_units(day)
            if world.units[u]["head_person_id"] is not None}


def _leaver_candidates(world, f):
    heads = _heads(world, f)
    return [p.pid for p in world.employed_on(f) if p.pid not in heads and p.last_day is None]


def _joiner_profile(world, profiles, stream, team_uid, period_start):
    """Weights: the people holding each profile on the period start among those with the team's function."""
    function = world.units[team_uid]["function"]
    held = {name: 0 for name in PROFILES}
    for p in world.employed_on(period_start):
        if p.function == function:
            held[profiles.on(p.pid, period_start)] += 1
    if sum(held.values()) == 0:
        held = dict(profiles.targets)
    return pick(stream, PROFILES, [held[name] for name in PROFILES])


def run_months(world, profiles, params, streams, ranges, months, log, seats):
    """Step 3 of section 7: drift, reorganisation, leavers, joiners and the forced events, month by month.

    ``seats`` is the SeatBook: it names the providers that seat a person and learns of joiners and of
    the reorganisation, so that Copilot seats follow 4.2."""
    ev = params["events"]
    jl, ro = ev["joiners_leavers"], ev["reorganisation"]
    stream = streams.get("event|joiners_leavers")
    contractor_share = params["contractor_share"]
    reorg_month = ro["month_index"] if ro["month_index"] is not None else months // 2
    forced_month = (months - 1) // 2
    leavers_drawn = joiners_drawn = 0
    reorg_logged = False

    def make_joiner(day, team, population, invite_lead):
        profile = _joiner_profile(world, profiles, stream, team, world.start)
        person = world.add_joiner(day, team, population, day - timedelta(days=invite_lead))
        profiles.hist[person.pid] = [(day, profile)]
        seats.joined(person)
        log.add("joiner", [day], [person.pid], {
            "applied": True, "unit_id": team, "profile": profile, "population": population,
            "invite_date": person.invite_date, "seats": seats.providers(person.pid)})
        return person

    def make_leaver(pid, day):
        world.people[pid].last_day = day
        log.add("leaver", [day], [pid], {
            "applied": True, "unit_id": world.people[pid].unit_id, "profile": profiles.on(pid, day),
            "seats": seats.providers(pid)})

    for k, (f, last) in enumerate(ranges):
        length = (last - f).days + 1
        if k >= 1:
            profiles.drift(f)
        if ro["enabled"] and k == reorg_month and not reorg_logged:
            reorg_logged = True
            details = None
            if months >= 2 and ro["unit"] is not None:
                details = world.reorganise(ro["unit"], f)
            if details is None:
                log.add("reorganisation", [], [], {"applied": False, "old_unit_id": None, "new_unit_ids": None,
                                                   "moved_units": None, "new_heads": None})
            else:
                changed = details.pop("changed_people")
                log.add("reorganisation", [f], changed, dict(details, applied=True))
                seats.reorganised(details)
        if not jl["enabled"]:
            continue
        employed = world.employed_on(f)
        n = len(employed)
        n_leave = stoch_round(stream, D(n) * D(jl["leaver_rate"]))
        n_join = stoch_round(stream, D(n) * D(jl["joiner_rate"]))
        candidates = _leaver_candidates(world, f)
        for _ in range(n_leave):
            if not candidates:
                break
            i = uniform_int(stream, 0, len(candidates) - 1)
            pid = candidates.pop(i)
            make_leaver(pid, f + timedelta(days=uniform_int(stream, 0, length - 1)))
            leavers_drawn += 1
        for _ in range(n_join):
            day = f + timedelta(days=uniform_int(stream, 0, length - 1))
            teams = [u for u in world.line_units(day) if world.units[u]["kind"] == "team"]
            weights = [sum(1 for p in world.employed_on(day) if p.unit_id == u) for u in teams]
            team = pick(stream, teams, weights)
            if team is None:
                team = teams[0]
            population = "contractor" if bern(stream, contractor_share) else "employee"
            make_joiner(day, team, population, jl["invite_lead_days"])
            joiners_drawn += 1
        if jl["min_per_period"] == 1 and k == forced_month:
            forced_day = min(max(date(f.year, f.month, 15), f), last)
            if leavers_drawn == 0:
                candidates = _leaver_candidates(world, f)
                if candidates:
                    make_leaver(candidates[0], forced_day)
                    leavers_drawn += 1
            if joiners_drawn == 0:
                teams = [u for u in world.line_units(forced_day) if world.units[u]["kind"] == "team"]
                population = "contractor" if bern(stream, contractor_share) else "employee"
                make_joiner(forced_day, teams[0], population, jl["invite_lead_days"])
                joiners_drawn += 1


# ---- runaway agent (7.3) --------------------------------------------------------------------------


def _runaway_not_applied():
    return {"type": "runaway_agent", "dates": [], "actors": [],
            "details": {"applied": False, "product": None, "model": None, "added_requests_per_day": None,
                        "added_tokens_per_day": None, "added_cost_list_per_day": None,
                        "base_requests_per_day": None}}


def _correction_not_applied():
    return {"type": "late_correction", "dates": [], "actors": [],
            "details": {"applied": False, "provider": "github_copilot", "correction_batch_id": None, "rows": None}}


def late_correction(params, streams, rows, start, end, correction_batch_id, copilot_enabled):
    """The late provider correction of Copilot credits (7.4).

    ``rows`` are the base Copilot ledger rows. Returns (rows after the correction, event dict or None when
    the event is disabled). Without Copilot, without a candidate, or when no credit would change, the event
    is recorded with applied false and no correction batch is written."""
    lc = params["events"]["late_correction"]
    if not lc["enabled"]:
        return rows, None
    if not copilot_enabled:
        return rows, _correction_not_applied()
    last = end - timedelta(days=lc["window_end_offset_days"])
    first = last - timedelta(days=lc["window_days"] - 1)
    window = [first + timedelta(days=i) for i in range((last - first).days + 1) if start <= first + timedelta(days=i) <= end]
    candidates = sorted((r for r in rows if r.tag == "base" and r.credits > 0 and first <= r.day <= last),
                        key=lambda r: (r.day, r.person))
    if not candidates:
        return rows, _correction_not_applied()
    stream = streams.get("event|correction")
    share, factor = lc["share"], D(lc["factor"])
    selected = [r for r in candidates if bern(stream, share)]
    if not selected:
        selected = [candidates[0]]
    changes = {}
    for r in selected:
        after = (r.credits * factor).quantize(D("0.1"), ROUND_HALF_UP)
        if after != r.credits:
            changes[id(r)] = r.corrected(after)
    if not changes:
        return rows, _correction_not_applied()
    final = [changes.get(id(r), r) for r in rows]
    revised = [(r, changes[id(r)]) for r in selected if id(r) in changes]
    detail_rows = [{"day": old.day.isoformat(), "person_id": old.person, "model": old.model,
                    "credits_before": quantity_str(old.credits), "credits_after": quantity_str(new.credits)}
                   for old, new in revised]
    event = {"type": "late_correction", "dates": window, "actors": sorted({old.person for old, _new in revised}),
             "details": {"applied": True, "provider": "github_copilot",
                         "correction_batch_id": correction_batch_id, "rows": detail_rows}}
    return final, event


def runaway(world, profiles, params, streams, start, end):
    """Returns (rows, event dict or None when the event is disabled)."""
    ra = params["events"]["runaway_agent"]
    if not ra["enabled"]:
        return [], None
    if not params["providers"]["anthropic"]["enabled"]:
        return [], _runaway_not_applied()
    period_days = (end - start).days + 1
    if ra["start"] is not None:
        first = date.fromisoformat(ra["start"])
    else:
        first = start + timedelta(days=int((D("0.6") * period_days).to_integral_value("ROUND_FLOOR")))
        while first.weekday() >= 5:
            first += timedelta(days=1)
    days = [first + timedelta(days=i) for i in range(ra["days"]) if first + timedelta(days=i) <= end]
    candidates = []
    if days:
        for p in world.employed_on(first):
            if profiles.on(p.pid, first) == "developer" and all(p.employed(d) for d in days):
                candidates.append(p.pid)
    shuffled = shuffle(streams.get("event|runaway"), candidates)
    if not shuffled:
        return [], _runaway_not_applied()
    target = shuffled[0]
    beh = params["behaviour"]
    req_median = D(beh["volume"]["developer"]["claude_code"]["req_median"])
    requests = round_half_up((D(ra["multiplier"]) - 1) * req_median)
    tok = beh["tokens"]["claude_code"]
    uncached, read, w5, w1, output = tokens_from_medians(
        requests, D(tok["in_median"]), D(tok["out_median"]), D(tok["f_read"]), D(tok["f_write"]), D(tok["f_1h"]))
    rows = [UsageRow("anthropic", day, target, "claude_code", ra["model"], requests, uncached, read, w5, w1,
                     output, tag="runaway") for day in days]
    event = {"type": "runaway_agent", "dates": days, "actors": [target],
             "details": {"applied": True, "product": "claude_code", "model": ra["model"],
                         "added_requests_per_day": requests,
                         "added_tokens_per_day": {"input_uncached": uncached, "cache_read": read,
                                                  "cache_write_5m": w5, "cache_write_1h": w1, "output": output}}}
    return rows, event


# ---- price change (7.5) ------------------------------------------------------------------------------


def price_change(params, table, ranges, months, enabled_providers):
    """Insert the new price version; returns the event dict (None when disabled)."""
    pc = params["events"]["price_change"]
    if not pc["enabled"]:
        return None
    if pc["date"] is not None:
        when = date.fromisoformat(pc["date"])
    else:
        f, last = ranges[(months - 1) // 2]
        when = min(date(f.year, f.month, 15), last)
        when = max(when, f)
    result = None
    if pc["provider"] in enabled_providers:
        result = table.insert_change(pc["provider"], pc["model"], when, D(pc["factor"]))
    if result is None:
        return {"type": "price_change", "dates": [when], "actors": [pc["provider"]],
                "details": {"applied": False, "provider": pc["provider"], "model": pc["model"],
                            "prices_before": None, "prices_after": None}}
    before, after = result
    return {"type": "price_change", "dates": [when], "actors": [pc["provider"]],
            "details": {"applied": True, "provider": pc["provider"], "model": pc["model"],
                        "prices_before": before, "prices_after": after}}


def check_reorganisation(world, params):
    """Exit-2 checks of the reorganisation target (7.2)."""
    ro = params["events"]["reorganisation"]
    if not ro["enabled"] or ro["unit"] is None:
        return
    if ro["month_index"] == 0:
        raise ParamError("events.reorganisation.month_index must be at least 1: a unit needs a day before it is split")
    uid = world.key_to_unit.get(ro["unit"])
    if uid is None or world.units[uid]["dimension"] != "line":
        raise ParamError("events.reorganisation.unit names an unknown line unit: %s" % ro["unit"])
    if world.units[uid]["kind"] not in ("department", "team"):
        raise ParamError("events.reorganisation.unit must be a department or a team")
