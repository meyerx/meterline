"""S08 every event is findable (V5)."""

import importlib
from datetime import timedelta
from decimal import Decimal

import meterline_sim  # noqa: F401
from sim_helpers import (
    COPILOT_ENABLED, anthropic_actor_id, copilot_actor_id, copy_run, d, gen, jsonl_files, load_records,
    main_batch_id, mutate_csv, mutate_json, mutate_jsonl, pull_batch_ids, read_json,
)

vmod = importlib.import_module("meterline_sim.verify")

PHASE1_TYPES = ("joiner", "leaver", "reorganisation", "runaway_agent", "price_change")
COPILOT_TYPES = ("late_correction",)
BOTH_TEAMS_GRANTED = {"providers": {"github_copilot": {"granted_units": ["alpha", "beta"]}}}


def _events(folder, kind):
    return [e for e in read_json(folder, "ground_truth.json")["events"] if e["type"] == kind]


def _starts_with(failures, kind):
    return any(f.startswith(kind) for f in failures)


def test_done_check_S08_every_event_is_findable(tiny_run, vertical_run):
    applied = {e["type"] for e in read_json(tiny_run, "ground_truth.json")["events"] if e["details"]["applied"]}
    assert set(PHASE1_TYPES) <= applied
    if COPILOT_ENABLED:
        assert set(COPILOT_TYPES) <= applied
    for folder in (tiny_run, vertical_run):
        assert vmod.v5_events(folder) == []


def test_done_check_S08_negative_control_runaway(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)

    def bump(gt):
        for e in gt["events"]:
            if e["type"] == "runaway_agent":
                e["details"]["added_requests_per_day"] += 1

    mutate_json(broken / "ground_truth.json", bump)
    assert _starts_with(vmod.v5_events(broken), "runaway_agent")


def test_done_check_S08_negative_control_price_change(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    gt = read_json(broken, "ground_truth.json")
    event = next(e for e in gt["events"] if e["type"] == "price_change")
    when = d(event["dates"][0])
    model = event["details"]["model"]
    path = next(p for k, p in jsonl_files(broken) if k == "usage" and p.stem == main_batch_id(broken, "anthropic"))

    def bump(lines):
        for line in lines:
            if line["model"] == model and d(line["period_start"]) >= when and line["cost_list"] is not None:
                line["cost_list"] = "999.00"
                break
        else:
            raise AssertionError("no record to tamper")
        return lines

    mutate_jsonl(path, bump)
    assert _starts_with(vmod.v5_events(broken), "price_change")


def test_done_check_S08_negative_control_leaver_and_joiner(tiny_run, tmp_path):
    gt = read_json(tiny_run, "ground_truth.json")
    path_name = "records/usage/%s.jsonl" % main_batch_id(tiny_run, "anthropic")

    broken = copy_run(tiny_run, tmp_path / "leaver")
    users = {(r.actor_id) for _b, r in load_records(tiny_run, "usage") if r.provider == "anthropic"}
    leaver = next(
        e for e in gt["events"]
        if e["type"] == "leaver" and anthropic_actor_id(tiny_run, e["actors"][0]) in users
    )
    actor = anthropic_actor_id(tiny_run, leaver["actors"][0])

    def undelete(lines):
        for line in lines:
            if line["actor_id"] == actor:
                line["actor_deleted"] = False
                return lines
        raise AssertionError("no record")

    mutate_jsonl(broken / path_name, undelete)
    assert _starts_with(vmod.v5_events(broken), "leaver")

    broken2 = copy_run(tiny_run, tmp_path / "joiner")
    joiner = next(
        e for e in gt["events"]
        if e["type"] == "joiner" and d(e["dates"][0]) > d(gt["period"]["start"])
        and anthropic_actor_id(tiny_run, e["actors"][0]) in users
    )
    jactor = anthropic_actor_id(tiny_run, joiner["actors"][0])
    before = (d(joiner["dates"][0]) - timedelta(days=1)).isoformat()

    def early(lines):
        for line in lines:
            if line["actor_id"] == jactor:
                line = dict(line, period_start=before, period_end=before)
                return lines + [line]
        raise AssertionError("no record")

    mutate_jsonl(broken2 / path_name, early)
    assert _starts_with(vmod.v5_events(broken2), "joiner")


def test_done_check_S08_negative_control_reorganisation(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    event = next(e for e in _events(broken, "reorganisation"))
    old = event["details"]["old_unit_id"]

    def stretch(rows):
        for row in rows:
            if row["unit_id"] == old and row["valid_to"]:
                row["valid_to"] = (d(row["valid_to"]) + timedelta(days=1)).isoformat()
        return rows

    mutate_csv(broken / "org" / "units.csv", stretch)
    assert _starts_with(vmod.v5_events(broken), "reorganisation")


def _anthropic_main(folder, kind):
    return folder / "records" / kind / ("%s.jsonl" % main_batch_id(folder, "anthropic"))


def test_done_check_S08_negative_control_seat_delta(tiny_run, tmp_path):
    gt = read_json(tiny_run, "ground_truth.json")
    joiner = next(e for e in gt["events"] if e["type"] == "joiner" and d(e["dates"][0]) > d(gt["period"]["start"]))
    day = joiner["dates"][0]
    broken = copy_run(tiny_run, tmp_path)

    def more_seats(lines):
        for line in lines:
            if line["period_start"] == day:
                line["seats"] = int(line["seats"]) + 5
        return lines

    mutate_jsonl(_anthropic_main(broken, "seats"), more_seats)
    failures = vmod.v5_events(broken)
    assert any(f.startswith("joiner") and "seats on" in f for f in failures)


def test_done_check_S08_negative_control_record_after_the_last_day(tiny_run, tmp_path):
    gt = read_json(tiny_run, "ground_truth.json")
    end = d(gt["period"]["end"])
    users = {r.actor_id for _b, r in load_records(tiny_run, "usage") if r.provider == "anthropic"}
    leaver = next(e for e in gt["events"] if e["type"] == "leaver" and d(e["dates"][0]) < end
                  and anthropic_actor_id(tiny_run, e["actors"][0]) in users)
    actor = anthropic_actor_id(tiny_run, leaver["actors"][0])
    after = (d(leaver["dates"][0]) + timedelta(days=1)).isoformat()
    broken = copy_run(tiny_run, tmp_path)

    def late(lines):
        for line in lines:
            if line["actor_id"] == actor:
                return lines + [dict(line, period_start=after, period_end=after)]
        raise AssertionError("no record")

    mutate_jsonl(_anthropic_main(broken, "usage"), late)
    failures = vmod.v5_events(broken)
    assert any(f.startswith("leaver") and "after the last day" in f for f in failures)


def test_done_check_S08_negative_control_reorganisation_person_and_headcount(tiny_run, tmp_path):
    event = next(e for e in _events(tiny_run, "reorganisation") if e["details"]["applied"])
    f = event["dates"][0]
    old = event["details"]["old_unit_id"]
    new_ids = set(event["details"]["new_unit_ids"])

    moved = copy_run(tiny_run, tmp_path / "moved")

    def keep_in_old_unit(rows):
        for row in rows:
            if row["valid_from"] == f and row["unit_id"] in new_ids:
                row["unit_id"] = old
                break
        else:
            raise AssertionError("no moved person")
        return rows

    mutate_csv(moved / "org" / "people.csv", keep_in_old_unit)
    assert any("is not under a new unit" in x for x in vmod.v5_events(moved))

    gone = copy_run(tiny_run, tmp_path / "gone")

    def drop_one(rows):
        victim = next(r for r in rows if r["valid_from"] == f and r["unit_id"] in new_ids)
        return [r for r in rows if r is not victim]

    mutate_csv(gone / "org" / "people.csv", drop_one)
    assert any("headcount" in x for x in vmod.v5_events(gone))


# ---- Copilot clauses of S08 ----------------------------------------------------------------------------------


def _first_of_next_month(day):
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


def _late_correction_row(folder):
    event = next(e for e in read_json(folder, "ground_truth.json")["events"]
                 if e["type"] == "late_correction" and e["details"]["applied"])
    return event["details"]["rows"][0]


def test_done_check_S08_negative_control_late_correction(tiny_run, tmp_path):
    # as S06 (b): the latest record must hold credits_after
    broken = copy_run(tiny_run, tmp_path / "after")

    def claim_more(gt):
        row = next(e for e in gt["events"] if e["type"] == "late_correction")["details"]["rows"][0]
        row["credits_after"] = str(Decimal(row["credits_after"]) + 1)

    mutate_json(broken / "ground_truth.json", claim_more)
    assert _starts_with(vmod.v5_events(broken), "late_correction")

    # and the main batch must hold credits_before
    before = copy_run(tiny_run, tmp_path / "before")
    row = _late_correction_row(before)
    actor = copilot_actor_id(before, row["person_id"])
    path = before / "records" / "usage" / (pull_batch_ids(before, "github_copilot", "main")[0] + ".jsonl")

    def tamper(lines):
        for line in lines:
            if (line["actor_id"] == actor and line["product"] == "copilot" and line["grain"] == "day"
                    and line["period_start"] == row["day"]):
                line["quantities"] = {"ai_credit": str(Decimal(row["credits_before"]) + 1)}
                return lines
        raise AssertionError("no record to tamper")

    mutate_jsonl(path, tamper)
    assert _starts_with(vmod.v5_events(before), "late_correction")


def _leaver_with_a_seat(folder):
    gt = read_json(folder, "ground_truth.json")
    for e in gt["events"]:
        if e["type"] == "leaver" and e["details"]["applied"] and copilot_actor_id(folder, e["actors"][0]):
            return e
    raise AssertionError("no leaver held a Copilot seat")


def test_done_check_S08_leaver_seat_assignments_carry_the_cancellation_date(tmp_path):
    folder = gen(tmp_path, "tiny", 7, 6, BOTH_TEAMS_GRANTED)
    leaver = _leaver_with_a_seat(folder)
    person, last = leaver["actors"][0], d(leaver["dates"][0])
    actor = copilot_actor_id(folder, person)
    want = _first_of_next_month(last).isoformat()
    seen = [r for _b, r in load_records(folder, "seat_assignments") if r.actor_id == actor]
    assert seen
    for rec in seen:
        if rec.period_start >= last:
            assert rec.pending_cancellation_date is not None and rec.pending_cancellation_date.isoformat() == want
        else:
            assert rec.pending_cancellation_date is None
    # the seat stays listed until the day before the date (7.1)
    assert max(r.period_start for r in seen) == min(_first_of_next_month(last) - timedelta(days=1),
                                                    d(read_json(folder, "ground_truth.json")["period"]["end"]))
    assert vmod.v5_events(folder) == []


def test_done_check_S08_negative_control_leaver_cancellation_date(tmp_path):
    folder = gen(tmp_path / "run", "tiny", 7, 6, BOTH_TEAMS_GRANTED)
    leaver = _leaver_with_a_seat(folder)
    last = d(leaver["dates"][0])
    actor = copilot_actor_id(folder, leaver["actors"][0])
    broken = copy_run(folder, tmp_path / "broken")

    def no_date(lines):
        for line in lines:
            if line["actor_id"] == actor and d(line["period_start"]) >= last:
                line["pending_cancellation_date"] = None
        return lines

    for _kind, path in jsonl_files(broken):
        if path.parent.name == "seat_assignments":
            mutate_jsonl(path, no_date)
    failures = vmod.v5_events(broken)
    assert any(f.startswith("leaver") and "pending_cancellation_date" in f for f in failures)


def test_done_check_S08_negative_control_joiner_seat_before_the_start_date(tmp_path):
    folder = gen(tmp_path / "run", "tiny", 7, 6, BOTH_TEAMS_GRANTED)
    gt = read_json(folder, "ground_truth.json")
    joiner = next(e for e in gt["events"] if e["type"] == "joiner" and d(e["dates"][0]) > d(gt["period"]["start"])
                  and copilot_actor_id(folder, e["actors"][0]))
    actor = copilot_actor_id(folder, joiner["actors"][0])
    before = (d(joiner["dates"][0]) - timedelta(days=1)).isoformat()
    broken = copy_run(folder, tmp_path / "broken")
    # a seat assignment of the joiner dated the day before the start date, copied from a seat list
    path = next(p for k, p in jsonl_files(broken) if k == "seat_assignments")

    def early(lines):
        return lines + [dict(lines[0], actor_id=actor, period_start=before, period_end=before)]

    mutate_jsonl(path, early)
    assert any(f.startswith("joiner") and "before the start date" in f for f in vmod.v5_events(broken))


MANY_JOINERS = {"events": {"joiners_leavers": {"joiner_rate": "0.3"}},
                "providers": {"github_copilot": {"granted_units": ["alpha", "beta"]}}}


def test_done_check_S08_negative_control_joiner_copilot_usage_and_activity_before_the_start_date(tmp_path):
    folder = gen(tmp_path / "run", "tiny", 7, 6, MANY_JOINERS)
    assert vmod.v5_events(folder) == []
    gt = read_json(folder, "ground_truth.json")
    main = pull_batch_ids(folder, "github_copilot", "main")[0]
    used = {r.actor_id for _b, r in load_records(folder, "usage")
            if r.provider == "github_copilot" and r.product == "copilot"}
    # a joiner that has Copilot usage of its own (a developer or a power user), found in the run itself
    joiner = next(e for e in gt["events"] if e["type"] == "joiner" and d(e["dates"][0]) > d(gt["period"]["start"])
                  and copilot_actor_id(folder, e["actors"][0]) in used)
    actor = copilot_actor_id(folder, joiner["actors"][0])
    before = (d(joiner["dates"][0]) - timedelta(days=1)).isoformat()
    for kind in ("usage", "activity"):
        broken = copy_run(folder, tmp_path / kind)

        def early(lines, actor=actor, before=before):
            for line in lines:
                if line["actor_id"] == actor and line["grain"] == "day":
                    return lines + [dict(line, period_start=before, period_end=before)]
            raise AssertionError("no Copilot record of the joiner")

        mutate_jsonl(broken / "records" / kind / (main + ".jsonl"), early)
        failures = vmod.v5_events(broken)
        assert any(f.startswith("joiner") and "%s record" % kind in f and "before the start date" in f
                   for f in failures), kind
