"""S06 late correction (spec section 15, invariant V3; behaviour of 7.4 and 6.3)."""

import importlib
import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import meterline

import meterline_sim  # noqa: F401
from sim_helpers import (
    COPILOT_OFF, compare_totals, copilot_person_ids, copy_run, credits_of, gen, group_totals, jsonl_files,
    latest_by_key, mutate_json, mutate_jsonl, pull_batch_ids, read_json,
)

vmod = importlib.import_module("meterline_sim.verify")

IGNORED = ("pulled_at", "data_refreshed_at", "batch_id")
KINDS = ("usage", "activity", "seats", "seat_assignments", "org_totals")


def _records(folder, kind, batch_ids):
    """The record dicts of the files of one kind whose batch id is in ``batch_ids``."""
    out = []
    for k, path in jsonl_files(folder):
        if k == kind and path.stem in batch_ids:
            out.extend(r.to_dict() for r in meterline.read_jsonl(path, meterline.KINDS[k]))
    return out


def _key(rec, kind):
    return tuple(rec[f] for f in meterline.KINDS[kind].KEY_FIELDS)


def _day_record(records, provider_actor_id, day):
    hits = [r for r in records if r["provider"] == "github_copilot" and r["product"] == "copilot"
            and r["grain"] == "day" and r["period_start"] == day and r["actor_id"] == provider_actor_id]
    return hits[0] if len(hits) == 1 else None


def s06_failures(folder):
    """The assertions of S06 a, b and c. A failure starts with the letter of its assertion."""
    folder = Path(folder)
    gt = read_json(folder, "ground_truth.json")
    events = [e for e in gt["events"] if e["type"] == "late_correction"]
    if len(events) != 1 or not events[0]["details"]["applied"]:
        return ["precondition: the late correction is not applied"]
    det = events[0]["details"]
    correction_id = det["correction_batch_id"]
    main_ids = set(pull_batch_ids(folder, "github_copilot", "main"))
    failures = []

    # a. the correction batch exists, and each of its records differs from the latest earlier record of its key
    if {p["batch_id"]: p["purpose"] for p in gt["pulls"]}.get(correction_id) != "correction":
        failures.append("a: the correction batch is not a pull of purpose correction")
    found = False
    for kind in KINDS:
        batch = _records(folder, kind, {correction_id})
        if not batch:
            continue
        found = True
        earlier_ids = {path.stem for k, path in jsonl_files(folder) if k == kind and path.stem < correction_id}
        earlier = {}
        for rec in sorted(_records(folder, kind, earlier_ids), key=lambda r: r["batch_id"]):
            earlier[_key(rec, kind)] = rec
        for rec in batch:
            prev = earlier.get(_key(rec, kind))
            if prev is None:
                failures.append("a: a %s record has no earlier record of its key" % kind)
            elif all(rec[f] == prev[f] for f in rec if f not in IGNORED):
                failures.append("a: a %s record equals the latest earlier record of its key" % kind)
    if not found:
        failures.append("a: the correction batch has no file")

    # b. each corrected row: the main batch has credits_before, the latest record has credits_after
    actor_of = {person: actor for actor, person in copilot_person_ids(folder).items()}
    main_usage = _records(folder, "usage", main_ids)
    latest_usage = list(latest_by_key(folder, "usage").values())
    for row in det["rows"]:
        actor = actor_of.get(row["person_id"])
        before, after = _day_record(main_usage, actor, row["day"]), _day_record(latest_usage, actor, row["day"])
        if before is None or credits_of(before) != Decimal(row["credits_before"]):
            failures.append("b: main batch of %s on %s does not hold credits_before" % (row["person_id"], row["day"]))
        if after is None or credits_of(after) != Decimal(row["credits_after"]):
            failures.append("b: latest record of %s on %s does not hold credits_after" % (row["person_id"], row["day"]))
        if row["credits_before"] == row["credits_after"]:
            failures.append("b: %s on %s is listed as corrected and did not change" % (row["person_id"], row["day"]))

    # c. all batches give totals, the main batches alone give totals_before_correction
    latest = {kind: list(latest_by_key(folder, kind).values()) for kind in ("usage", "org_totals")}
    failures += ["c: " + f for f in compare_totals(group_totals(latest), gt["totals"])]
    main_only = {kind: _records(folder, kind, main_ids) for kind in ("usage", "org_totals")}
    failures += ["c: before correction: " + f
                 for f in compare_totals(group_totals(main_only), gt["totals_before_correction"])]
    return failures


# ---- the check on a tiny run ---------------------------------------------------------------------------


def test_done_check_S06_late_correction(tiny_run):
    assert s06_failures(tiny_run) == []
    assert vmod.v3_correction(tiny_run) == []


def test_done_check_S06_the_correction_follows_7_4(tiny_run):
    gt = read_json(tiny_run, "ground_truth.json")
    params = gt["parameters"]
    lc = params["events"]["late_correction"]
    end = date.fromisoformat(gt["period"]["end"])
    event = next(e for e in gt["events"] if e["type"] == "late_correction")
    det = event["details"]
    # window: end - offset - window_days + 1 .. end - offset
    last = end - timedelta(days=lc["window_end_offset_days"])
    first = last - timedelta(days=lc["window_days"] - 1)
    assert event["dates"] == [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    # the pull: C = P + after_days at 06:10:00
    pulled = next(p for p in gt["pulls"] if p["purpose"] == "correction")
    assert pulled["provider"] == "github_copilot" and pulled["batch_id"] == det["correction_batch_id"]
    main = next(p for p in gt["pulls"] if p["provider"] == "github_copilot" and p["purpose"] == "main")
    assert main["pulled_at"].endswith("T06:10:00+00:00")
    gap = date.fromisoformat(pulled["pulled_at"][:10]) - date.fromisoformat(main["pulled_at"][:10])
    assert gap == timedelta(days=lc["after_days"]) and pulled["pulled_at"].endswith("T06:10:00+00:00")
    # rows: sorted by (day, person), inside the window, credits_after = (before x factor) to one decimal, half up
    rows = det["rows"]
    assert rows and rows == sorted(rows, key=lambda r: (r["day"], r["person_id"]))
    assert event["actors"] == sorted({r["person_id"] for r in rows})
    for row in rows:
        assert event["dates"][0] <= row["day"] <= event["dates"][-1]
        want = (Decimal(row["credits_before"]) * Decimal(lc["factor"])).quantize(Decimal("0.1"), ROUND_HALF_UP)
        assert Decimal(row["credits_after"]) == want
    # every corrected day was final at the main pull (4.6): the correction revises facts marked final
    main_usage = _records(tiny_run, "usage", {main["batch_id"]})
    actor_of = {person: actor for actor, person in copilot_person_ids(tiny_run).items()}
    for row in rows:
        assert _day_record(main_usage, actor_of[row["person_id"]], row["day"])["is_final"] is True
    # the correction batch holds revisions only: day records of corrected days, billing, month records
    batch = _records(tiny_run, "usage", {pulled["batch_id"]}) + _records(tiny_run, "org_totals", {pulled["batch_id"]})
    assert batch and {r["provider"] for r in batch} == {"github_copilot"}
    assert not _records(tiny_run, "seat_assignments", {pulled["batch_id"]})


# ---- negative controls: each mutation must make S06 fail for the stated reason --------------------------


def _correction_id(folder):
    return next(p["batch_id"] for p in read_json(folder, "ground_truth.json")["pulls"] if p["purpose"] == "correction")


def test_done_check_S06_negative_control_a_unchanged_record_in_the_correction_batch(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    correction = _correction_id(broken)
    main_id = pull_batch_ids(broken, "github_copilot", "main")[0]
    main_lines = [json.loads(x) for x in (broken / "records" / "usage" / (main_id + ".jsonl")).read_text().splitlines()]
    fields = meterline.KINDS["usage"].KEY_FIELDS

    def restate(lines):
        first = lines[0]
        same = next(x for x in main_lines if all(x[f] == first[f] for f in fields))
        lines[0] = dict(same, batch_id=correction, pulled_at=first["pulled_at"])
        return lines

    mutate_jsonl(broken / "records" / "usage" / (correction + ".jsonl"), restate)
    failures = s06_failures(broken)
    assert any(f.startswith("a:") and "equals the latest earlier record" in f for f in failures)
    assert any(f.startswith("late_correction") for f in vmod.v3_correction(broken))


def test_done_check_S06_negative_control_a_missing_correction_batch(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    correction = _correction_id(broken)
    for _kind, path in jsonl_files(broken):
        if path.stem == correction:
            path.unlink()
    assert any(f.startswith("a:") and "no file" in f for f in s06_failures(broken))
    assert vmod.v3_correction(broken) != []


def test_done_check_S06_negative_control_b_wrong_credits(tiny_run, tmp_path):
    # the ground truth claims a different credits_after than the latest record holds
    after = copy_run(tiny_run, tmp_path / "after")

    def claim_more(gt):
        event = next(e for e in gt["events"] if e["type"] == "late_correction")
        row = event["details"]["rows"][0]
        row["credits_after"] = str(Decimal(row["credits_after"]) + 1)

    mutate_json(after / "ground_truth.json", claim_more)
    assert any(f.startswith("b:") and "credits_after" in f for f in s06_failures(after))
    assert vmod.v3_correction(after) != []

    # the main batch does not hold credits_before
    before = copy_run(tiny_run, tmp_path / "before")
    gt = read_json(before, "ground_truth.json")
    row = next(e for e in gt["events"] if e["type"] == "late_correction")["details"]["rows"][0]
    actor = {p: a for a, p in copilot_person_ids(before).items()}[row["person_id"]]
    main_path = before / "records" / "usage" / (pull_batch_ids(before, "github_copilot", "main")[0] + ".jsonl")

    def tamper(lines):
        for line in lines:
            if (line["actor_id"] == actor and line["product"] == "copilot" and line["grain"] == "day"
                    and line["period_start"] == row["day"]):
                line["quantities"] = {"ai_credit": str(Decimal(row["credits_before"]) + 1)}
                return lines
        raise AssertionError("no record to tamper")

    mutate_jsonl(main_path, tamper)
    assert any(f.startswith("b:") and "credits_before" in f for f in s06_failures(before))
    assert vmod.v3_correction(before) != []


def test_done_check_S06_negative_control_c_totals_do_not_match(tiny_run, tmp_path):
    def add_a_cent(rows):
        victim = next(t for t in rows if t["provider"] == "github_copilot" and t["cost"] is not None)
        victim["cost"] = str(Decimal(victim["cost"]) + Decimal("0.01"))

    before = copy_run(tiny_run, tmp_path / "before")
    mutate_json(before / "ground_truth.json", lambda gt: add_a_cent(gt["totals_before_correction"]))
    failures = s06_failures(before)
    assert any(f.startswith("c: before correction") for f in failures)
    assert vmod.v3_correction(before) != []

    final = copy_run(tiny_run, tmp_path / "final")
    mutate_json(final / "ground_truth.json", lambda gt: add_a_cent(gt["totals"]))
    failures = s06_failures(final)
    assert any(f.startswith("c:") and "before correction" not in f for f in failures)
    assert vmod.v3_correction(final) != []


def test_done_check_S06_negative_control_no_correction_is_reported(tmp_path):
    # the precondition ("the correction is applied") can fail: event off, or Copilot off
    off = gen(tmp_path / "event_off", "tiny", 7, 6, {"events": {"late_correction": {"enabled": False}}})
    assert s06_failures(off) == ["precondition: the late correction is not applied"]
    no_copilot = gen(tmp_path / "copilot_off", "tiny", 7, 6, COPILOT_OFF)
    assert s06_failures(no_copilot) == ["precondition: the late correction is not applied"]
