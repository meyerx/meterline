"""The invariants V1-V8 of section 15, as functions over an output folder.

Each function reads only the folder and returns a list of failure strings; an
empty list means the invariant holds. A failure of an event check starts with
the event type.

Some checks recompute the reference price of a usage record (V5) and the credit
pool of a seat plan (V6), which ``meterline-gold`` also computes. That is on
purpose: the simulator is an independent oracle for gold, and a mistake in one
side shows up as a disagreement with the other. Never import gold's arithmetic
here, and never share a helper with gold: a shared helper turns the oracle into
a copy. See "Gold and the simulator: an independent oracle" in the package
README."""

import calendar
import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path

import meterline

D = Decimal
_RECORD_KINDS = ("usage", "activity", "seats", "seat_assignments", "org_totals")
_TOKEN_FIELDS = ("tokens_input_uncached", "tokens_cache_read", "tokens_cache_write", "tokens_cache_write_5m",
                 "tokens_cache_write_1h", "tokens_input_total", "tokens_output", "requests")
_PRICE_TOLERANCE = D("0.00000002")


class _Run:
    """The folder, loaded once."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.gt = json.loads((self.folder / "ground_truth.json").read_text(encoding="utf-8"))
        self.start = date.fromisoformat(self.gt["period"]["start"])
        self.end = date.fromisoformat(self.gt["period"]["end"])
        self._records = {}
        self._latest = {}
        self._csv = {}

    def files(self, kind):
        return sorted((self.folder / "records" / kind).glob("*.jsonl"))

    def records(self, kind):
        """[(batch_id, record)] over every batch file of a kind."""
        if kind not in self._records:
            cls = meterline.KINDS[kind]
            out = []
            for path in self.files(kind):
                for rec in meterline.read_jsonl(path, cls):
                    out.append((path.stem, rec))
            self._records[kind] = out
        return self._records[kind]

    def latest(self, kind):
        if kind not in self._latest:
            self._latest[kind] = list(meterline.latest_per_key(r for _b, r in self.records(kind)).values())
        return self._latest[kind]

    def csv(self, name):
        if name not in self._csv:
            with open(self.folder / "org" / name, newline="", encoding="utf-8") as handle:
                self._csv[name] = list(csv.DictReader(handle))
        return self._csv[name]

    def events(self, kind):
        return [e for e in self.gt["events"] if e["type"] == kind and e["details"].get("applied")]

    def actor_ids(self, person_id):
        return {(row["provider"], row["actor_id"]) for row in self.csv("identities.csv")
                if row["person_id"] == person_id}


def _d(text):
    return date.fromisoformat(text)


def _cents(value):
    return None if value is None else D(value).quantize(D("0.01"), ROUND_HALF_EVEN)


# ---- V1: every record validates (S04) --------------------------------------------------------------


def v1_records(folder):
    run = folder if isinstance(folder, _Run) else _Run(folder)
    failures = []
    pulled = {datetime.fromisoformat(p["pulled_at"]) for p in run.gt["pulls"]}
    per_stem = {}
    for kind in _RECORD_KINDS:
        cls = meterline.KINDS[kind]
        for path in run.files(kind):
            try:
                records = meterline.read_jsonl(path, cls)
            except Exception as exc:  # unreadable or malformed line
                failures.append("%s/%s: unreadable: %s" % (kind, path.name, exc))
                continue
            per_stem.setdefault(path.stem, {})[kind] = records
            for rec in records:
                if rec.batch_id != path.stem:
                    failures.append("%s/%s: batch_id %s differs from the file name" % (kind, path.name, rec.batch_id))
                    break
            for rec in records:
                if rec.pulled_at not in pulled:
                    failures.append("%s/%s: pulled_at is not one of the pulls" % (kind, path.name))
                    break
            if records:
                today = records[0].pulled_at.date()
                for text in meterline.batch_failures(records, today):
                    failures.append("%s/%s: %s" % (kind, path.name, text))
    for stem in sorted(per_stem):
        usage = per_stem[stem].get("usage", [])
        totals = per_stem[stem].get("org_totals", [])
        if usage and totals and usage[0].provider in ("anthropic", "litellm"):
            for text in meterline.actor_cost_failures(usage, totals):
                failures.append("%s: %s" % (stem, text))
    return failures


# ---- V2: silver totals equal the ground truth (S05) -----------------------------------------------------


def silver_totals(run, records_by_kind=None):
    """The sums of 11.3 over the latest record per key, or over ``records_by_kind`` when given."""
    groups = {}
    for kind in ("usage", "org_totals"):
        for rec in (run.latest(kind) if records_by_kind is None else records_by_kind[kind]):
            key = (kind, rec.provider, rec.cost_basis, rec.grain, rec.period_start.strftime("%Y-%m"))
            groups.setdefault(key, []).append(rec)
    out = {}
    for key, recs in groups.items():
        row = {}
        for field in ("cost", "cost_list", "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write",
                      "tokens_input_total", "tokens_output", "requests"):
            values = [getattr(r, field) for r in recs if getattr(r, field) is not None]
            row[field] = sum((D(v) for v in values), D(0)) if values else None
        quantities = {}
        for r in recs:
            for unit, value in r.quantities.items():
                quantities[unit] = quantities.get(unit, D(0)) + D(value)
        row["quantities"] = quantities
        out[key] = row
    return out


def _compare_totals(silver, truth_rows, label):
    failures = []
    truth = {(t["kind"], t["provider"], t["cost_basis"], t["grain"], t["month"]): t for t in truth_rows}
    if set(silver) != set(truth):
        for key in sorted(set(silver) ^ set(truth), key=repr):
            failures.append("%s: group %r is only in %s" % (label, key, "silver" if key in silver else "the ground truth"))
    for key in sorted(set(silver) & set(truth), key=repr):
        for field, value in silver[key].items():
            want = truth[key][field]
            if field in ("cost", "cost_list"):
                same = _cents(want) == (None if value is None else _cents(value))
            elif field == "quantities":
                same = {u: D(v) for u, v in want.items()} == value
            elif want is None or value is None:
                same = want is None and value is None
            else:
                same = D(want) == value
            if not same:
                failures.append("%s: %r %s: silver %s, ground truth %s" % (label, key, field, value, want))
    return failures


def v2_totals(folder):
    run = folder if isinstance(folder, _Run) else _Run(folder)
    return _compare_totals(silver_totals(run), run.gt["totals"], "totals")


# ---- V3: late correction (S06) ---------------------------------------------------------------------------


_NOT_A_CHANGE = ("pulled_at", "data_refreshed_at", "batch_id")


def _content(record):
    return {k: v for k, v in record.to_dict().items() if k not in _NOT_A_CHANGE}


def _day_credit(records, actor_id, day):
    """The ai_credit of the daily ``copilot`` usage record of an actor on a day, or None."""
    hits = [r for r in records if r.provider == "github_copilot" and r.product == "copilot" and r.grain == "day"
            and r.actor_id == actor_id and r.period_start == day]
    return None if len(hits) != 1 else D(hits[0].quantities.get("ai_credit", 0))


def _correction_rows(run, event, label):
    """S06 b / S08: for each corrected row the main batch has credits_before and the latest record
    credits_after."""
    failures = []
    main_ids = {p["batch_id"] for p in run.gt["pulls"] if p["provider"] == "github_copilot" and p["purpose"] == "main"}
    main = [r for b, r in run.records("usage") if b in main_ids]
    latest = run.latest("usage")
    for row in event["details"]["rows"]:
        ids = {actor for prov, actor in run.actor_ids(row["person_id"]) if prov == "github_copilot"}
        actor = next(iter(sorted(ids)), None)
        day = _d(row["day"])
        before, after = _day_credit(main, actor, day), _day_credit(latest, actor, day)
        if before is None or before != D(row["credits_before"]):
            failures.append("%s: main batch of %s on %s has %s, credits_before is %s" % (
                label, row["person_id"], row["day"], before, row["credits_before"]))
        if after is None or after != D(row["credits_after"]):
            failures.append("%s: latest record of %s on %s has %s, credits_after is %s" % (
                label, row["person_id"], row["day"], after, row["credits_after"]))
    return failures


def v3_correction(folder):
    run = folder if isinstance(folder, _Run) else _Run(folder)
    events = run.events("late_correction")
    if not events:
        return []
    failures = []
    for event in events:
        det = event["details"]
        batch_id = det["correction_batch_id"]
        pulls = {p["batch_id"]: p["purpose"] for p in run.gt["pulls"]}
        if pulls.get(batch_id) != "correction":
            failures.append("late_correction: %s is not a pull of purpose correction" % batch_id)
        # a. the correction batch exists and each of its records differs from the latest earlier record
        found = False
        for kind in _RECORD_KINDS:
            mine = [rec for b, rec in run.records(kind) if b == batch_id]
            if not mine:
                continue
            found = True
            earlier = {}
            for _b, rec in sorted(((b, r) for b, r in run.records(kind) if b < batch_id), key=lambda x: x[0]):
                earlier[rec.key()] = rec
            for rec in mine:
                prev = earlier.get(rec.key())
                if prev is None:
                    failures.append("late_correction: a %s record of the correction has no earlier record" % kind)
                elif _content(rec) == _content(prev):
                    failures.append("late_correction: a %s record of the correction equals its earlier record" % kind)
        if not found:
            failures.append("late_correction: the correction batch %s has no file" % batch_id)
        # b. the corrected rows
        failures += _correction_rows(run, event, "late_correction")
        # c. totals: all batches give totals, the main batches alone give totals_before_correction
        failures += _compare_totals(silver_totals(run), run.gt["totals"], "late_correction totals")
        main_ids = {p["batch_id"] for p in run.gt["pulls"]
                    if p["provider"] == "github_copilot" and p["purpose"] == "main"}
        only_main = {kind: [rec for b, rec in run.records(kind) if b in main_ids] for kind in ("usage", "org_totals")}
        failures += _compare_totals(silver_totals(run, only_main), run.gt["totals_before_correction"],
                                    "late_correction totals before the correction")
    return failures


# ---- V4: finality (S07) -----------------------------------------------------------------------------------


def _expected_final(rec):
    start = rec.period_start
    month_end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    month_rule = (rec.pulled_at.date() - month_end).days > 7
    ended = datetime.combine(start + timedelta(days=1), time(0, 0), tzinfo=timezone.utc)
    if rec.provider == "anthropic":
        return month_rule
    if rec.provider == "litellm":
        return rec.pulled_at >= ended + timedelta(days=2)
    if rec.provider == "github_copilot":
        if isinstance(rec, meterline.OrgTotal) or rec.grain == "month":
            return month_rule
        return rec.pulled_at >= ended + timedelta(days=4)
    return None


def v4_finality(folder):
    run = folder if isinstance(folder, _Run) else _Run(folder)
    failures = []
    for kind in ("usage", "org_totals"):
        for batch, rec in run.records(kind):
            want = _expected_final(rec)
            if want is None:
                failures.append("finality: unknown provider %s" % rec.provider)
            elif rec.is_final != want:
                failures.append("finality: %s %s %s %s is_final %s, rule says %s" % (
                    kind, rec.provider, rec.period_start, batch, rec.is_final, want))
    return failures


# ---- V5: every event is findable (S08) ----------------------------------------------------------------------


def _units_on(run, day):
    out = {}
    for row in run.csv("units.csv"):
        if row["dimension"] == "line" and _d(row["valid_from"]) <= day and (
                row["valid_to"] == "" or _d(row["valid_to"]) >= day):
            out[row["unit_id"]] = row
    return out


def _people_on(run, day):
    out = {}
    for row in run.csv("people.csv"):
        if _d(row["valid_from"]) <= day and (row["valid_to"] == "" or _d(row["valid_to"]) >= day):
            out[row["person_id"]] = row
    return out


def _day_records(run, person_id, kinds=("usage", "activity")):
    ids = {actor for _prov, actor in run.actor_ids(person_id)}
    out = []
    for kind in kinds:
        for _b, rec in run.records(kind):
            if rec.actor_id in ids and rec.grain == "day":
                out.append((kind, rec))
    return out


def _first_of_next_month(day):
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


def _price_of(rec, version):
    w5 = rec.tokens_cache_write_5m if rec.tokens_cache_write_5m is not None else (rec.tokens_cache_write or 0)
    w1 = rec.tokens_cache_write_1h or 0
    total = (D(rec.tokens_input_uncached or 0) * D(version["input_uncached"])
             + D(rec.tokens_cache_read or 0) * D(version["cache_read"])
             + D(w5) * D(version["cache_write_5m"]) + D(w1) * D(version["cache_write_1h"])
             + D(rec.tokens_output or 0) * D(version["output"])) / D(1000000)
    return total


def _v5_joiner_records(run, joiners):
    failures = []
    for ev in joiners:
        pid, start = ev["actors"][0], _d(ev["dates"][0])
        for kind, rec in _day_records(run, pid, ("usage", "activity", "seat_assignments")):
            if rec.period_start < start:
                failures.append("joiner %s: %s record dated %s before the start date %s" % (
                    ev["event_id"], kind, rec.period_start, start))
                break
    return failures


def _v5_joiner_seats(run, joiners, leavers, seat_snap):
    failures = []
    seen = set()
    for ev in joiners:
        day = _d(ev["dates"][0])
        if day <= run.start or day in seen:
            continue
        seen.add(day)
        if day in seat_snap and day - timedelta(days=1) in seat_snap:
            joined = sum(1 for e in joiners if _d(e["dates"][0]) == day)
            left = sum(1 for e in leavers if _d(e["dates"][0]) == day - timedelta(days=1))
            if seat_snap[day] - seat_snap[day - timedelta(days=1)] != joined - left:
                failures.append("joiner %s: seats on %s changed by %d, expected %d" % (
                    ev["event_id"], day, seat_snap[day] - seat_snap[day - timedelta(days=1)], joined - left))
    return failures


def _v5_leavers(run, leavers):
    failures = []
    for ev in leavers:
        pid, last = ev["actors"][0], _d(ev["dates"][0])
        for kind, rec in _day_records(run, pid):
            if rec.period_start > last:
                failures.append("leaver %s: %s record dated %s after the last day %s" % (
                    ev["event_id"], kind, rec.period_start, last))
                break
        ids = {actor for prov, actor in run.actor_ids(pid) if prov == "anthropic"}
        for rec in run.latest("usage"):
            if rec.provider == "anthropic" and rec.actor_id in ids and rec.actor_deleted is not True:
                failures.append("leaver %s: a usage record of the person has actor_deleted %s" % (
                    ev["event_id"], rec.actor_deleted))
                break
        # a Copilot seat: the seat assignments from the last day on carry the date of 7.1
        seat_ids = {actor for prov, actor in run.actor_ids(pid) if prov == "github_copilot"}
        want = _first_of_next_month(last)
        for rec in run.latest("seat_assignments"):
            if rec.actor_id in seat_ids and rec.period_start >= last and rec.pending_cancellation_date != want:
                failures.append("leaver %s: the seat assignment on %s has pending_cancellation_date %s, expected %s" % (
                    ev["event_id"], rec.period_start, rec.pending_cancellation_date, want))
                break
    return failures


def _v5_runaway(run):
    failures = []
    for ev in run.events("runaway_agent"):
        det = ev["details"]
        target_ids = {actor for prov, actor in run.actor_ids(ev["actors"][0]) if prov == "anthropic"}
        vol = run.gt["parameters"]["behaviour"]["volume"]["developer"]["claude_code"]["req_median"]
        multiplier = run.gt["parameters"]["events"]["runaway_agent"]["multiplier"]
        if det["added_requests_per_day"] != int(((D(multiplier) - 1) * D(vol)).to_integral_value(ROUND_HALF_UP)):
            failures.append("runaway_agent %s: added requests %s differ from (multiplier - 1) x median" % (
                ev["event_id"], det["added_requests_per_day"]))
        for i, day_text in enumerate(ev["dates"]):
            day = _d(day_text)
            total = sum(r.requests for r in run.latest("usage")
                        if r.provider == "anthropic" and r.actor_id in target_ids and r.product == "claude_code"
                        and r.period_start == day)
            want = det["base_requests_per_day"][i] + det["added_requests_per_day"]
            if total != want:
                failures.append("runaway_agent %s: %s has %d requests, expected %d" % (
                    ev["event_id"], day_text, total, want))
    return failures


def _v5_price_change(run):
    failures = []
    for ev in run.events("price_change"):
        det = ev["details"]
        when = _d(ev["dates"][0])
        for rec in run.latest("usage"):
            if rec.provider != det["provider"] or rec.model != det["model"]:
                continue
            shown = rec.cost_list if rec.cost_list is not None else rec.cost
            if shown is None:
                continue
            if rec.period_start >= when:
                version = det["prices_after"]
            elif rec.period_start == when - timedelta(days=1):
                version = det["prices_before"]
            else:
                continue
            if abs(_price_of(rec, version) - D(shown)) > _PRICE_TOLERANCE:
                failures.append("price_change %s: %s %s priced %s, expected %s" % (
                    ev["event_id"], rec.period_start, rec.model, shown, _price_of(rec, version)))
                break
    return failures


def _below(units, uid, ancestor):
    seen_ids = set()
    while uid and uid not in seen_ids:
        if uid == ancestor:
            return True
        seen_ids.add(uid)
        uid = units[uid]["parent_id"] if uid in units else ""
    return False


def _v5_reorganisation(run, ev, joiners, leavers):
    failures = []
    det = ev["details"]
    f = _d(ev["dates"][0])
    old, (new_a, new_b) = det["old_unit_id"], det["new_unit_ids"]
    rows = run.csv("units.csv")
    old_rows = [r for r in rows if r["unit_id"] == old]
    last_row = max(old_rows, key=lambda r: r["valid_from"]) if old_rows else None
    if last_row is None or last_row["valid_to"] != (f - timedelta(days=1)).isoformat():
        failures.append("reorganisation %s: the old unit does not end on the day before %s" % (ev["event_id"], f))
        return failures
    old_parent = last_row["parent_id"]
    for new in (new_a, new_b):
        first = [r for r in rows if r["unit_id"] == new]
        if not first or min(r["valid_from"] for r in first) != f.isoformat() or any(
                r["parent_id"] != old_parent for r in first if r["valid_from"] == f.isoformat()):
            failures.append("reorganisation %s: unit %s does not start on %s under the old parent" % (
                ev["event_id"], new, f))
    if f <= run.start:
        return failures
    before_units = _units_on(run, f - timedelta(days=1))
    after_units = _units_on(run, f)
    before_people = _people_on(run, f - timedelta(days=1))
    after_people = _people_on(run, f)
    for pid, row in sorted(before_people.items()):
        if pid not in after_people:
            continue
        if _below(before_units, row["unit_id"], old) and not any(
                _below(after_units, after_people[pid]["unit_id"], new) for new in (new_a, new_b)):
            failures.append("reorganisation %s: person %s is not under a new unit on %s" % (
                ev["event_id"], pid, f))
            break
    joined = sum(1 for e in joiners if _d(e["dates"][0]) == f)
    left = sum(1 for e in leavers if _d(e["dates"][0]) == f - timedelta(days=1))
    if len(after_people) != len(before_people) + joined - left:
        failures.append("reorganisation %s: headcount on %s is %d, expected %d" % (
            ev["event_id"], f, len(after_people), len(before_people) + joined - left))
    return failures


def v5_events(folder):
    run = folder if isinstance(folder, _Run) else _Run(folder)
    failures = []
    joiners = run.events("joiner")
    leavers = run.events("leaver")
    seat_snap = {}
    for rec in run.latest("seats"):
        if rec.provider == "anthropic":
            seat_snap[rec.period_start] = rec.seats

    failures += _v5_joiner_records(run, joiners)
    failures += _v5_joiner_seats(run, joiners, leavers, seat_snap)
    failures += _v5_leavers(run, leavers)
    failures += _v5_runaway(run)
    failures += _v5_price_change(run)
    for ev in run.events("late_correction"):
        failures += _correction_rows(run, ev, "late_correction %s" % ev["event_id"])
    for ev in run.events("reorganisation"):
        failures += _v5_reorganisation(run, ev, joiners, leavers)
    return failures


# ---- V6: Copilot credits agree with billing (S09) -----------------------------------------------------------


def _v6_credit(rec):
    return D(rec.quantities.get("ai_credit", 0))


def _v6_add(table, key, value):
    table[key] = table.get(key, D(0)) + value


def _v6_credit_relations(org, days, months):
    credit, add = _v6_credit, _v6_add
    failures = []
    # 1. per day: the organization's gross over models = the sum of the persons' daily credits
    org_day, person_day = {}, {}
    for r in org:
        add(org_day, r.period_start, credit(r))
    for r in days:
        add(person_day, r.period_start, credit(r))
    for day in sorted(set(org_day) | set(person_day)):
        if org_day.get(day, D(0)) != person_day.get(day, D(0)):
            failures.append("copilot credits: %s organization %s, persons %s" % (
                day, org_day.get(day, D(0)), person_day.get(day, D(0))))
    # 2. per month and model: the organization's gross = the month records' gross
    org_month, rec_month = {}, {}
    for r in org:
        add(org_month, (r.period_start.strftime("%Y-%m"), r.model), credit(r))
    for r in months:
        add(rec_month, (r.period_start.strftime("%Y-%m"), r.model), credit(r))
    for key in sorted(set(org_month) | set(rec_month)):
        if org_month.get(key, D(0)) != rec_month.get(key, D(0)):
            failures.append("copilot credits: %s organization %s, month records %s" % (
                key, org_month.get(key, D(0)), rec_month.get(key, D(0))))
    # 3. per person and month: the month records' gross = their daily sum
    daily, monthly = {}, {}
    for r in days:
        add(daily, (r.actor_id, r.period_start.strftime("%Y-%m")), credit(r))
    for r in months:
        add(monthly, (r.actor_id, r.period_start.strftime("%Y-%m")), credit(r))
    for key in sorted(set(daily) | set(monthly)):
        if daily.get(key, D(0)) != monthly.get(key, D(0)):
            failures.append("copilot credits: %s daily %s, month records %s" % (
                key, daily.get(key, D(0)), monthly.get(key, D(0))))
    return failures


def _v6_billing(run, org):
    credit, add = _v6_credit, _v6_add
    failures = []
    # 4. per month: the discount is min(pool, gross), the net amounts are the overage (contracts)
    contracts = {c["month"]: c for c in run.gt["contracts"] if c["provider"] == "github_copilot"}
    discount, net_amount, gross = {}, {}, {}
    for r in org:
        month = r.period_start.strftime("%Y-%m")
        add(discount, month, D(r.extras.get("discount_quantity", 0)))
        add(net_amount, month, D(r.extras.get("net_amount", 0)))
        add(gross, month, credit(r))
    for month in sorted(set(contracts) | set(gross)):
        c = contracts.get(month)
        if c is None:
            failures.append("copilot billing: %s has billing records and no contract row" % month)
            continue
        want = min(D(c["pool"]), D(c["gross_credits"]))
        if D(c["gross_credits"]) != gross.get(month, D(0)):
            failures.append("copilot billing: %s contract gross %s, billing gross %s" % (
                month, c["gross_credits"], gross.get(month, D(0))))
        if discount.get(month, D(0)) != want:
            failures.append("copilot billing: %s discount %s, min(pool, gross) %s" % (
                month, discount.get(month, D(0)), want))
        if net_amount.get(month, D(0)) != D(c["overage_amount"]):
            failures.append("copilot billing: %s net amounts %s, overage %s" % (
                month, net_amount.get(month, D(0)), c["overage_amount"]))
    return failures


def v6_copilot(folder):
    """S09 without its existence clause: credits, month records and billing agree (8.3)."""
    run = folder if isinstance(folder, _Run) else _Run(folder)
    usage = [r for r in run.latest("usage") if r.provider == "github_copilot"]
    org = [r for r in run.latest("org_totals") if r.provider == "github_copilot"]
    days = [r for r in usage if r.product == "copilot" and r.grain == "day"]
    months = [r for r in usage if r.grain == "month"]
    failures = _v6_credit_relations(org, days, months)
    failures += _v6_billing(run, org)
    credit = _v6_credit
    # 5. every Copilot cost_list is gross x 0.01
    for r in days + months + org:
        if r.cost_list is None or D(r.cost_list) != credit(r) * D("0.01"):
            failures.append("copilot cost_list: %s %s %s is %s, credits %s" % (
                r.grain, r.period_start, r.product, r.cost_list, credit(r)))
    return failures


# ---- V7: organisation totals cover the people (S10) ------------------------------------------------------------


def _v7_anthropic(usage, totals):
    failures = []
    day_sums = {}
    for rec in usage:
        if rec.provider != "anthropic":
            continue
        sums = day_sums.setdefault(rec.period_start, {})
        for field in _TOKEN_FIELDS + ("cost", "cost_list"):
            value = getattr(rec, field)
            if value is not None:
                sums[field] = sums.get(field, D(0)) + D(value)
    for tot in totals:
        if tot.provider != "anthropic":
            continue
        for field, value in sorted(day_sums.get(tot.period_start, {}).items()):
            shown = getattr(tot, field)
            if shown is None or D(shown) < value:
                failures.append("org_totals anthropic %s %s: %s is below the sum of people %s" % (
                    tot.period_start, field, shown, value))
    return failures


def _v7_litellm(usage, totals):
    failures = []
    per_model = {}
    for rec in usage:
        if rec.provider == "litellm":
            per_model[(rec.period_start, rec.model)] = rec
    for tot in totals:
        if tot.provider != "litellm":
            continue
        rec = per_model.get((tot.period_start, tot.model))
        if rec is None:
            failures.append("org_totals litellm %s %s has no unassigned record" % (tot.period_start, tot.model))
            continue
        for field in _TOKEN_FIELDS + ("cost", "cost_list"):
            if getattr(tot, field) != getattr(rec, field):
                failures.append("org_totals litellm %s %s %s differs from the unassigned record" % (
                    tot.period_start, tot.model, field))
    return failures


def _v7_copilot(usage, totals):
    failures = []
    org_gross, persons = {}, {}
    for tot in totals:
        if tot.provider == "github_copilot":
            org_gross[tot.period_start] = org_gross.get(tot.period_start, D(0)) + D(tot.quantities.get("ai_credit", 0))
    for rec in usage:
        if rec.provider == "github_copilot" and rec.product == "copilot" and rec.grain == "day":
            persons[rec.period_start] = persons.get(rec.period_start, D(0)) + D(rec.quantities.get("ai_credit", 0))
    for day in sorted(persons):
        if org_gross.get(day, D(0)) < persons[day]:
            failures.append("org_totals github_copilot %s: credits %s are below the sum of people %s" % (
                day, org_gross.get(day, D(0)), persons[day]))
    return failures


def v7_org_totals(folder):
    run = folder if isinstance(folder, _Run) else _Run(folder)
    usage = run.latest("usage")
    totals = run.latest("org_totals")
    failures = _v7_anthropic(usage, totals)
    failures += _v7_litellm(usage, totals)
    failures += _v7_copilot(usage, totals)
    for snap in run.latest("seats"):
        if snap.seats is not None and snap.daily_active is not None and snap.daily_active > snap.seats:
            failures.append("seats %s %s: daily_active %d above seats %d" % (
                snap.provider, snap.period_start, snap.daily_active, snap.seats))
    return failures


# ---- V8: organisation files are consistent (S11) -----------------------------------------------------------------


def _v8_people_rows(run, people_rows):
    failures = []
    by_person = {}
    for row in people_rows:
        by_person.setdefault(row["person_id"], []).append(row)
    for pid, rows in sorted(by_person.items()):
        rows.sort(key=lambda r: r["valid_from"])
        for a, b in zip(rows, rows[1:], strict=False):  # neighbours: one shorter on purpose
            if a["valid_to"] == "" or _d(b["valid_from"]) != _d(a["valid_to"]) + timedelta(days=1):
                failures.append("people.csv: rows of %s are not contiguous" % pid)
                break
    # the ground truth says who is employed: each such person needs rows in people.csv
    for person in run.gt["people"]:
        if person["person_id"] not in by_person:
            failures.append("people.csv: %s is employed from %s and has no row" % (person["person_id"], person["start"]))
        else:
            first = min(r["valid_from"] for r in by_person[person["person_id"]])
            if first != person["start"]:
                failures.append("people.csv: %s starts %s, the ground truth says %s" % (
                    person["person_id"], first, person["start"]))
    return failures


def _v8_manager_chain(day, pid, valid_rows, failures):
    seen, cur = set(), pid
    while True:
        if cur in seen:
            failures.append("%s: manager cycle at %s" % (day, pid))
            break
        seen.add(cur)
        mgr = valid_rows.get(cur, [{}])[0].get("manager_id", "")
        if mgr == "":
            break
        if mgr not in valid_rows:
            failures.append("%s: manager %s of %s is not employed" % (day, mgr, cur))
            break
        cur = mgr


def _v8_day(run, day, people_rows):
    failures = []
    units = _units_on(run, day)
    valid_rows = {}
    for row in people_rows:
        if _d(row["valid_from"]) <= day and (row["valid_to"] == "" or _d(row["valid_to"]) >= day):
            valid_rows.setdefault(row["person_id"], []).append(row)
    for pid, rows in sorted(valid_rows.items()):
        if len(rows) != 1:
            failures.append("%s: %s has %d valid rows" % (day, pid, len(rows)))
            continue
        row = rows[0]
        if row["unit_id"] not in units:
            failures.append("%s: %s is in unit %s, which is not a valid line unit" % (day, pid, row["unit_id"]))
        _v8_manager_chain(day, pid, valid_rows, failures)
    employed = {p["person_id"] for p in run.gt["people"]
                if _d(p["start"]) <= day and (p["last_day"] is None or _d(p["last_day"]) >= day)}
    for pid in sorted(employed - set(valid_rows)):
        failures.append("%s: %s is employed and has no valid row" % (day, pid))
    roots = [pid for pid, rows in valid_rows.items() if len(rows) == 1 and rows[0]["manager_id"] == ""]
    if len(roots) != 1:
        failures.append("%s: %d people without a manager" % (day, len(roots)))
    for uid, row in sorted(units.items()):
        if row["parent_id"] and row["parent_id"] not in units:
            failures.append("%s: unit %s has an invalid parent %s" % (day, uid, row["parent_id"]))
    return failures


def v8_org_files(folder):
    run = folder if isinstance(folder, _Run) else _Run(folder)
    people_rows = run.csv("people.csv")
    unit_rows = run.csv("units.csv")
    failures = _v8_people_rows(run, people_rows)
    days = {run.start}
    for row in people_rows + unit_rows:
        days.add(_d(row["valid_from"]))
        if row["valid_to"]:
            days.add(_d(row["valid_to"]) + timedelta(days=1))
    for day in sorted(d for d in days if run.start <= d <= run.end):
        failures += _v8_day(run, day, people_rows)
    return failures


def verify(folder):
    """Run V1-V8 against an output folder; an empty list means every invariant holds."""
    run = _Run(folder)
    failures = []
    for check in (v1_records, v2_totals, v3_correction, v4_finality, v5_events, v6_copilot, v7_org_totals,
                  v8_org_files):
        failures.extend(check(run))
    return failures
