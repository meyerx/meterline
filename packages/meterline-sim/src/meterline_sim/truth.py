"""The ground truth and the manifest (spec 11.3, 11.4). Totals come from the ledger, never from silver."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from meterline import amount_str, quantity_str

from meterline_sim import __version__
from meterline_sim.billing import seat_fees, seats_billed

D = Decimal


def dumps(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _iso(day):
    return None if day is None else day.isoformat()


def totals_from_ledger(usage_rows, days):
    """Expected sums over the latest record per key of kinds usage and org_totals (11.3)."""
    groups = {}

    def group(kind, provider, basis, month):
        key = (kind, provider, basis, "day", month)
        if key not in groups:
            groups[key] = {"cost": None, "cost_list": None, "uncached": 0, "read": 0, "write": 0, "total": 0,
                           "output": 0, "requests": 0, "seen": False}
        return groups[key]

    def accumulate(g, row, with_list):
        g["uncached"] += row.uncached
        g["read"] += row.read
        g["write"] += row.w5 + row.w1
        g["total"] += row.uncached + row.read + row.w5 + row.w1
        g["output"] += row.output
        g["requests"] += row.requests
        if row.amount is not None:
            g["cost"] = (g["cost"] or D(0)) + row.amount
            if with_list:
                g["cost_list"] = (g["cost_list"] or D(0)) + row.amount
        g["seen"] = True

    for day in days:  # an organisation total exists for every day of the period
        month = day.strftime("%Y-%m")
        g = group("org_totals", "anthropic", "consumption", month)
        if g["cost"] is None:
            g["cost"] = D(0)
            g["cost_list"] = D(0)
    for row in usage_rows:
        month = row.day.strftime("%Y-%m")
        if row.provider == "anthropic":
            for kind in ("usage", "org_totals"):
                accumulate(group(kind, "anthropic", "consumption", month), row, True)
        else:
            basis = "estimate" if row.amount is not None else None
            for kind in ("usage", "org_totals"):
                accumulate(group(kind, "litellm", basis, month), row, False)
    out = []
    for (kind, provider, basis, grain, month), g in groups.items():
        out.append({
            "kind": kind, "provider": provider, "cost_basis": basis, "grain": grain, "month": month,
            "cost": None if g["cost"] is None else amount_str(g["cost"]),
            "cost_list": None if g["cost_list"] is None else amount_str(g["cost_list"]),
            "tokens_input_uncached": g["uncached"], "tokens_cache_read": g["read"],
            "tokens_cache_write": g["write"], "tokens_input_total": g["total"],
            "tokens_output": g["output"], "requests": g["requests"], "quantities": {},
        })
    out.sort(key=lambda t: (t["kind"], t["provider"], t["cost_basis"] or "", t["grain"], t["month"]))
    return out


def copilot_totals(rows, bill):
    """The expected sums of the Copilot records of one state of the ledger (11.3).

    Credits and tokens are summed from the ledger rows, and the month and billing groups from the
    billing of the same rows; no silver is read. The groups are those the rendering rules of 4.2 give:
    day credits (consumption), CLI and app tokens (no cost basis), month records (consumption) and the
    organization's billing (billed)."""
    price = bill.price
    groups = {}

    def group(kind, basis, grain, month):
        key = (kind, "github_copilot", basis, grain, month)
        if key not in groups:
            groups[key] = {"credits": D(0), "cost": D(0), "cost_list": D(0), "tokens_total": 0, "tokens_output": 0,
                           "requests": 0}
        return groups[key]

    for row in rows:
        month = row.day.strftime("%Y-%m")
        g = group("usage", "consumption", "day", month)
        g["credits"] += row.credits
        g["cost"] += row.credits * price
        g["cost_list"] += row.credits * price
        for prompts, tokens_in, tokens_out in ((row.cli_prompts, row.cli_in, row.cli_out),
                                               (row.app_prompts, row.app_in, row.app_out)):
            if prompts:
                t = group("usage", None, "day", month)
                requests = prompts * 2
                t["tokens_total"] += requests * tokens_in
                t["tokens_output"] += requests * tokens_out
                t["requests"] += requests
    for (_person, month, _model), (gross, _discount, net) in bill.person.items():
        g = group("usage", "consumption", "month", month)
        g["credits"] += gross
        g["cost"] += net * price
        g["cost_list"] += gross * price
    for (day, _model), (gross, _discount, net) in bill.org.items():
        g = group("org_totals", "billed", "day", day.strftime("%Y-%m"))
        g["credits"] += gross
        g["cost"] += net * price
        g["cost_list"] += gross * price

    out = []
    for (kind, provider, basis, grain, month), g in groups.items():
        tokens_only = basis is None
        out.append({
            "kind": kind, "provider": provider, "cost_basis": basis, "grain": grain, "month": month,
            "cost": None if tokens_only else amount_str(g["cost"]),
            "cost_list": None if tokens_only else amount_str(g["cost_list"]),
            "tokens_input_uncached": None, "tokens_cache_read": None, "tokens_cache_write": None,
            "tokens_input_total": g["tokens_total"] if tokens_only else None,
            "tokens_output": g["tokens_output"] if tokens_only else None,
            "requests": g["requests"] if tokens_only else None,
            "quantities": {} if tokens_only else {"ai_credit": quantity_str(g["credits"])},
        })
    return out


def _sort_totals(rows):
    rows.sort(key=lambda t: (t["kind"], t["provider"], t["cost_basis"] or "", t["grain"], t["month"]))
    return rows


def build_ground_truth(ctx, usage_rows, events, pulls, table, preset_name):
    world, profiles, params = ctx.world, ctx.profiles, ctx.params
    people = []
    for pid in sorted(world.person_order):
        p = world.people[pid]
        hist = profiles.hist[pid]
        plist = []
        for i, (from_day, profile) in enumerate(hist):
            to_day = hist[i + 1][0] - timedelta(days=1) if i + 1 < len(hist) else p.last_day
            plist.append({"profile": profile, "from": from_day.isoformat(), "to": _iso(to_day)})
        seats = []
        if params["providers"]["anthropic"]["enabled"]:
            seats.append({"provider": "anthropic", "from": p.start.isoformat(), "to": _iso(p.last_day),
                          "pending_cancellation_date": None})
        if ctx.copilot is not None and ctx.copilot.has(pid):
            end = ctx.copilot.end_of(pid)
            seats.append({"provider": "github_copilot", "from": ctx.copilot.seats[pid].start.isoformat(),
                          "to": _iso(end if end is not None and end <= ctx.end else None),
                          "pending_cancellation_date": _iso(ctx.copilot.cancellation_date(pid))})
        people.append({"person_id": pid, "start": p.start.isoformat(), "last_day": _iso(p.last_day),
                       "profiles": plist, "seats": sorted(seats, key=lambda s: (s["provider"], s["from"]))})
    accounts = []
    if params["providers"]["litellm"]["enabled"]:
        for sa_id in sorted(params["providers"]["litellm"]["accounts"]):
            model = params["behaviour"]["service_accounts"][sa_id]["model"]
            accounts.append({"id": sa_id, "model": model,
                             "priced": table.version("litellm", model, ctx.start) is not None})

    ev_out = []
    for e in events:
        ev_out.append({"type": e["type"], "dates": [_iso(d) for d in e["dates"]], "actors": list(e["actors"]),
                       "details": _jsonable(e["details"])})
    ev_out.sort(key=lambda e: ((e["dates"][0] if e["dates"] else ""), e["type"], (e["actors"][0] if e["actors"] else "")))
    for n, e in enumerate(ev_out, 1):
        e["event_id"] = "E%03d" % n

    contracts = []
    if params["providers"]["anthropic"]["enabled"]:
        intervals = [(pid, world.people[pid].start, world.people[pid].last_day) for pid in world.person_order]
        for first, last in ctx.ranges:
            n = seats_billed(intervals, first, last)
            contracts.append({"provider": "anthropic", "month": first.strftime("%Y-%m"), "seats_billed": n,
                              "seat_fees": amount_str(seat_fees(n, table.seat_per_month)), "pool": None,
                              "gross_credits": None, "discount_credits": None, "overage_amount": None,
                              "budget_crossed_on": None})
    if ctx.copilot is not None:
        gh = params["providers"]["github_copilot"]
        seat_price = table.copilot_plans[gh["plan"]]["seat_per_month"]
        for first, _last in ctx.ranges:
            month = first.strftime("%Y-%m")
            m = ctx.bill_final.months[month]
            contracts.append({
                "provider": "github_copilot", "month": month, "seats_billed": m["seats_billed"],
                "seat_fees": amount_str(seat_fees(m["seats_billed"], seat_price)),
                "pool": quantity_str(m["pool"]), "gross_credits": quantity_str(m["gross"]),
                "discount_credits": quantity_str(m["discount"]), "overage_amount": amount_str(m["overage"]),
                "budget_crossed_on": _iso(m["budget_crossed_on"])})
    contracts.sort(key=lambda c: (c["provider"], c["month"]))

    return {
        "ground_truth_format": 1,
        "generator": {"name": "meterline-sim", "version": __version__},
        "preset": preset_name, "seed": ctx.seed, "months": ctx.months, "scale": quantity_str(ctx.scale),
        "start": ctx.start.isoformat(),
        "period": {"start": ctx.start.isoformat(), "end": ctx.end.isoformat()},
        "parameters": params,
        "price_table_sha256": table.sha256,
        "pulls": sorted(({"provider": b.provider, "purpose": b.purpose, "pulled_at": b.pulled_at.isoformat(),
                          "batch_id": b.batch_id} for b in pulls), key=lambda p: p["batch_id"]),
        "people": people,
        "service_accounts": accounts,
        "events": ev_out,
        "totals": _sort_totals(totals_from_ledger(usage_rows, ctx.days)
                               + ([] if ctx.copilot is None else copilot_totals(ctx.cop_rows_final, ctx.bill_final))),
        "totals_before_correction": ([] if ctx.copilot is None
                                     else _sort_totals(copilot_totals(ctx.cop_rows_base, ctx.bill_base))),
        "contracts": contracts,
    }


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return amount_str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def build_manifest(folder, ctx, preset_sha, params_sha, price_sha, preset_name):
    folder = Path(folder)
    files = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel == "manifest.json":
            continue
        data = path.read_bytes()
        files.append({"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    files.sort(key=lambda f: f["path"])
    return {
        "generator": "meterline-sim", "version": __version__, "preset": preset_name, "seed": ctx.seed,
        "months": ctx.months, "start": ctx.start.isoformat(), "scale": quantity_str(ctx.scale),
        "preset_sha256": preset_sha, "parameters_sha256": params_sha, "price_table_sha256": price_sha,
        "files": files,
    }
