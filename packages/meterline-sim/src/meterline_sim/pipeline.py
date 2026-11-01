"""The generation pipeline of section 10."""

import csv
import io
import os
import re
import shutil
from datetime import date, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path

import meterline

from meterline_sim import render, truth
from meterline_sim.behaviour import (
    CopilotLedger, generate_ledger, generate_service_accounts, iter_days, Profiles,
)
from meterline_sim.billing import CopilotBilling
from meterline_sim.draws import Streams
from meterline_sim.events import (
    EventLog, check_reorganisation, late_correction, month_ranges, period_end, price_change, run_months,
    runaway,
)
from meterline_sim.org import CopilotSeats, SeatBook, World, anthropic_actor_id, copilot_actor_id
from meterline_sim.params import (
    COPILOT_MODELS, ParamError, RunError, canonical_sha, load_json_file, load_preset, parse_scale,
    resolve_params,
)
from meterline_sim.prices import amount, load_price_table, per_million_text

PRIVATE_UMASK = 0o077  # files 0600, folders 0700, like the connectors and gold
UTC_PULL = {"anthropic": (6, 0, 0), "github_copilot": (6, 10, 0), "litellm": (6, 20, 0)}


class Ctx:
    """What the rendering and the ground truth need to know about a run."""


def _csv_bytes(header, rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _load_run_params(params):
    if params is None:
        return {}
    if isinstance(params, (str, Path)):
        return load_json_file(params)
    if isinstance(params, dict):
        # a round trip rejects fractional numbers and non-JSON values
        import json

        from meterline_sim.params import parse_json

        return parse_json(json.dumps(params), "params")
    raise ParamError("params must be a dict or a path")


def _price_rows(rows, table, unpriced_zero):
    for row in rows:
        version = table.version(row.provider, row.model, row.day)
        if version is not None:
            row.amount = amount(version, row.uncached, row.read, row.w5, row.w1, row.output)
        elif row.provider == "litellm" and unpriced_zero:
            row.amount = Decimal(0)
        else:
            row.amount = None


def generate(preset, seed, months=6, start="2026-04-01", scale="1", out="./sim-out", params=None):
    """Run the pipeline and return the output folder (``pathlib.Path``).

    Raises ``meterline_sim.params.ParamError`` for bad input (CLI exit 2) and
    ``meterline_sim.params.RunError`` for a failure inside the run (CLI exit 1)."""
    return run(preset, seed, months, start, scale, out, params)[0]


def run(preset, seed, months=6, start="2026-04-01", scale="1", out="./sim-out", params=None):
    """Like ``generate``, but returns (folder, record counts per kind). Runs in Decimal's default context.

    Everything the run writes is readable by its owner only (files 0600, folders 0700), whatever the caller's umask:
    the process umask is 077 for the duration of the call and is put back afterwards."""
    old_umask = os.umask(PRIVATE_UMASK)
    try:
        with localcontext(Context()):
            return _run(preset, seed, months, start, scale, out, params)
    finally:
        os.umask(old_umask)


def _check_run_args(seed, months, start, scale):
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 63:
        raise ParamError("seed must be an integer from 0 to 2^63 - 1")
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 24:
        raise ParamError("months must be an integer from 1 to 24")
    try:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(start)):
            raise ValueError(start)
        start_day = date.fromisoformat(str(start))
    except ValueError as exc:
        raise ParamError("start must be YYYY-MM-DD") from exc
    scale_value = parse_scale(scale)
    return start_day, scale_value


def _check_price_table(params_resolved, table, months):
    prov = params_resolved["providers"]
    if prov["github_copilot"]["enabled"]:
        if prov["github_copilot"]["plan"] not in table.copilot_plans:
            raise ParamError("the price table has no GitHub Copilot plan " + prov["github_copilot"]["plan"])
        for model in COPILOT_MODELS:
            if model not in table.credits_per_interaction:
                raise ParamError("the price table has no credits_per_interaction for " + model)
    ro = params_resolved["events"]["reorganisation"]
    if ro["month_index"] is not None and not 0 <= ro["month_index"] < months:
        raise ParamError("events.reorganisation.month_index is outside the period")
    for model in ("model-a-light", "model-a-standard", "model-a-premium"):
        if not table.has_entry("anthropic", model):
            raise ParamError("the price table has no anthropic entry for " + model)
    if prov["litellm"]["enabled"]:
        for sa_id in prov["litellm"]["accounts"]:
            model = params_resolved["behaviour"]["service_accounts"][sa_id]["model"]
            if not table.has_entry("litellm", model):
                raise ParamError("the price table has no litellm entry for " + model)


def _add_runaway_details(rw_event, rwrows, base_usage):
    if rw_event["details"]["applied"]:
        target, days = rw_event["actors"][0], rw_event["dates"]
        rw_event["details"]["added_cost_list_per_day"] = [
            Decimal(sum((r.amount for r in rwrows if r.day == d), Decimal(0))) for d in days]
        rw_event["details"]["base_requests_per_day"] = [
            sum(r.requests for r in base_usage
                if r.actor == target and r.product == "claude_code" and r.day == d) for d in days]


def _late_correction_stage(ctx, params_resolved, streams, table, cop_ledger, copilot_on, P):
    """Billing of the base and the corrected Copilot rows; returns the late-correction event and the pull time."""
    prov = params_resolved["providers"]
    C = P + timedelta(days=params_resolved["events"]["late_correction"]["after_days"])
    ctx.cop_rows_base = ctx.cop_rows_final = ctx.bill_base = ctx.bill_final = None
    correction_pulled = render.utc(C, *UTC_PULL["github_copilot"])
    if copilot_on:
        gh = prov["github_copilot"]
        included = Decimal(gh["included_credits_per_seat"] if gh["included_credits_per_seat"] is not None
                           else table.copilot_plans[gh["plan"]]["included_credits_per_seat"])
        ctx.cop_rows_base = cop_ledger.rows

        def bill_of(rows):
            return CopilotBilling(rows, ctx.ranges, ctx.copilot.intervals(), included, table.credit_price,
                                  table.budget_usd)

        ctx.bill_base = bill_of(ctx.cop_rows_base)
    ctx.cop_rows_final, lc_event = late_correction(
        params_resolved, streams, ctx.cop_rows_base or [], ctx.start, ctx.end,
        render.batch_id_of(correction_pulled), copilot_on)
    if copilot_on:
        if lc_event is not None and lc_event["details"]["applied"]:
            ctx.bill_final = bill_of(ctx.cop_rows_final)
            ctx.bill_final.keep_revised_keys(ctx.bill_base)
        else:
            ctx.cop_rows_final, ctx.bill_final = ctx.cop_rows_base, ctx.bill_base
    return lc_event, correction_pulled


def _price_change_texts(pc_event):
    pc_event["details"]["prices_before"] = (
        None if pc_event["details"]["prices_before"] is None
        else per_million_text(pc_event["details"]["prices_before"]))
    pc_event["details"]["prices_after"] = (
        None if pc_event["details"]["prices_after"] is None
        else per_million_text(pc_event["details"]["prices_after"]))


def _render_batches(ctx, prov, usage, activity, sa_rows, P, lc_event, correction_pulled):
    copilot_on = prov["github_copilot"]["enabled"]
    batches = []
    anth_usage = [r for r in usage if r.provider == "anthropic"]
    if prov["anthropic"]["enabled"]:
        batches.append(render.render_anthropic(ctx, anth_usage, activity, render.utc(P, *UTC_PULL["anthropic"])))
    main_copilot = None
    if copilot_on:
        main_copilot = render.render_copilot(
            ctx, ctx.cop_rows_base, ctx.bill_base, render.utc(P, *UTC_PULL["github_copilot"]), "main")
        batches.append(main_copilot)
        batches.extend(render.render_copilot_seat_lists(ctx, ctx.cop_rows_base))
    if prov["litellm"]["enabled"] and sa_rows:
        batches.append(render.render_litellm(ctx, sa_rows, render.utc(P, *UTC_PULL["litellm"])))
    render.validate_batches(batches)
    if copilot_on and lc_event is not None and lc_event["details"]["applied"]:
        rerendered = render.render_copilot(ctx, ctx.cop_rows_final, ctx.bill_final, correction_pulled, "correction")
        correction = render.revisions(rerendered, main_copilot)
        if not correction.records:  # nothing differs from the main pull: no correction batch
            raise RunError("the late correction changed no record")
        batches.append(correction)
        render.validate_batches([correction])
    return batches


def _write_folder(folder, ctx, prov, batches, gt, preset_obj, params_resolved, table):
    world = ctx.world
    tmp = folder.with_name(folder.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        org = tmp / "org"
        org.mkdir(parents=True)
        files = {
            "units.csv": (["unit_id", "valid_from", "valid_to", "dimension", "key", "name", "kind", "parent_id",
                           "function", "cost_center", "head_person_id"], world.unit_rows(ctx.end)),
            "people.csv": (["person_id", "valid_from", "valid_to", "unit_id", "manager_id", "function", "level",
                            "cost_center", "population", "attrs"], world.people_rows(ctx.end)),
            "memberships.csv": (["person_id", "unit_id", "dimension", "role", "valid_from", "valid_to"],
                                world.membership_rows(ctx.end)),
            "identities.csv": (["person_id", "provider", "method", "actor_kind", "actor_id", "actor_login",
                                "actor_email", "valid_from", "valid_to"], _identity_rows(ctx, prov)),
            "directory.csv": (["person_id", "given_name", "family_name", "email"], world.directory_rows()),
        }
        for name, (header, rows) in files.items():
            (org / name).write_bytes(_csv_bytes(header, rows))
        counts = {}
        for batch in batches:
            for kind, records in sorted(batch.records.items()):
                meterline.write_jsonl(records, tmp / "records" / kind / (batch.batch_id + ".jsonl"))
                counts[kind] = counts.get(kind, 0) + len(records)
        (tmp / "ground_truth.json").write_bytes(truth.dumps(gt).encode("utf-8"))
        manifest = truth.build_manifest(
            tmp, ctx, canonical_sha(preset_obj), canonical_sha(params_resolved), table.sha256, preset_obj["name"])
        (tmp / "manifest.json").write_bytes(truth.dumps(manifest).encode("utf-8"))
        if folder.exists():
            folder.rmdir()
        os.replace(tmp, folder)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return counts


def _run(preset, seed, months, start, scale, out, params):
    start_day, scale_value = _check_run_args(seed, months, start, scale)

    preset_obj = load_preset(preset)
    params_resolved = resolve_params(preset_obj.get("parameters", {}), _load_run_params(params))
    table = load_price_table(params_resolved["price_table"])
    prov = params_resolved["providers"]
    _check_price_table(params_resolved, table, months)

    out_dir = Path(out)
    folder = out_dir / ("%s-s%d-m%d" % (preset_obj["name"], seed, months))
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ParamError("output folder exists and is not empty: %s" % folder)

    # -- build -------------------------------------------------------------------------------
    streams = Streams(seed)
    ctx = Ctx()
    ctx.seed, ctx.months, ctx.start, ctx.scale = seed, months, start_day, scale_value
    ctx.end = period_end(start_day, months)
    ctx.ranges = month_ranges(start_day, ctx.end)
    ctx.days = list(iter_days(start_day, ctx.end))
    ctx.params = params_resolved
    ctx.org_slug = preset_obj["org"]["slug"]

    world = World(preset_obj, params_resolved, scale_value, start_day, streams)
    ctx.world = world
    check_reorganisation(world, params_resolved)
    for key in prov["github_copilot"]["granted_units"]:
        if key not in world.key_to_unit:
            raise ParamError("providers.github_copilot.granted_units names an unknown unit: " + key)
    profiles = Profiles(preset_obj, params_resolved, world, streams)
    profiles.assign_initial(start_day)
    ctx.profiles = profiles

    copilot_on = prov["github_copilot"]["enabled"]
    ctx.copilot = None
    if copilot_on:
        ctx.copilot = CopilotSeats(world, params_resolved)
        ctx.copilot.grant_initial(start_day, streams.get("seats"))
    seats = SeatBook(prov["anthropic"]["enabled"], ctx.copilot)

    log = EventLog()
    run_months(world, profiles, params_resolved, streams, ctx.ranges, months, log, seats)

    cop_ledger = None if not copilot_on else CopilotLedger(ctx.copilot, table.credits_per_interaction)
    usage, activity = generate_ledger(world, profiles, params_resolved, streams, ctx.days, cop_ledger)
    base_usage = list(usage)
    rwrows, rw_event = runaway(world, profiles, params_resolved, streams, start_day, ctx.end)
    usage.extend(rwrows)

    enabled = {name for name in ("anthropic", "litellm") if prov[name]["enabled"]}
    pc_event = price_change(params_resolved, table, ctx.ranges, months, enabled)

    sa_rows = []
    if prov["litellm"]["enabled"]:
        sa_rows = generate_service_accounts(params_resolved, streams, ctx.days)
    _price_rows(usage, table, False)
    _price_rows(sa_rows, table, prov["litellm"]["unpriced_cost"] == "zero")

    if rw_event is not None:
        _add_runaway_details(rw_event, rwrows, base_usage)
        log.items.append(rw_event)
    P = ctx.end + timedelta(days=params_resolved["pull"]["lag_days"])
    lc_event, correction_pulled = _late_correction_stage(
        ctx, params_resolved, streams, table, cop_ledger, copilot_on, P)
    if lc_event is not None:
        log.items.append(lc_event)
    if pc_event is not None:
        _price_change_texts(pc_event)
        log.items.append(pc_event)

    # -- render and validate ----------------------------------------------------------------------
    batches = _render_batches(ctx, prov, usage, activity, sa_rows, P, lc_event, correction_pulled)

    # -- write ----------------------------------------------------------------------------------------
    all_rows = [r for r in usage if r.provider == "anthropic" or prov["anthropic"]["enabled"]] + sa_rows
    gt = truth.build_ground_truth(ctx, all_rows, log.items, batches, table, preset_obj["name"])
    if not prov["anthropic"]["enabled"]:
        gt["totals"] = [t for t in gt["totals"] if t["provider"] != "anthropic"]

    counts = _write_folder(folder, ctx, prov, batches, gt, preset_obj, params_resolved, table)
    return folder, counts


def _identity_rows(ctx, prov):
    rows = []
    world = ctx.world
    if prov["anthropic"]["enabled"]:
        for pid in sorted(world.person_order):
            p = world.people[pid]
            rows.append([pid, "anthropic", "email", "user", anthropic_actor_id(ctx.seed, pid), "", p.email,
                         p.start.isoformat(), p.last_day.isoformat() if p.last_day else ""])
    if ctx.copilot is not None:
        for pid in sorted(ctx.copilot.seats):
            p = world.people[pid]
            end = ctx.copilot.end_of(pid)
            rows.append([pid, "github_copilot", "manual", "user", copilot_actor_id(p.n), p.login, "",
                         ctx.copilot.seats[pid].start.isoformat(),
                         end.isoformat() if end is not None and end <= ctx.end else ""])
    rows.sort(key=lambda r: (r[0], r[1], r[7]))
    return rows
