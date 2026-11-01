"""S04 every record validates, S05 silver totals equal the ground truth."""

import importlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import meterline

import meterline_sim  # noqa: F401
from sim_helpers import (
    cents, compare_totals, copy_run, jsonl_files, latest_by_key, load_records, mutate_json, mutate_jsonl, parse_dt,
    read_json, silver_totals,
)

vmod = importlib.import_module("meterline_sim.verify")


def s04_failures(folder):
    folder = Path(folder)
    failures = []
    pulled = {parse_dt(p["pulled_at"]) for p in read_json(folder, "ground_truth.json")["pulls"]}
    stems = {}
    for kind, path in jsonl_files(folder):
        records = meterline.read_jsonl(path, meterline.KINDS[kind])
        stems.setdefault(path.stem, {})[kind] = records
        for rec in records:
            if rec.batch_id != path.stem:
                failures.append("batch_id differs from file name: " + path.name)
            if rec.pulled_at not in pulled:
                failures.append("pulled_at is not a pull of the ground truth: " + path.name)
        if records:
            today = records[0].pulled_at.date()
            failures += ["%s: %s" % (path.name, f) for f in meterline.batch_failures(records, today)]
    for stem, kinds in sorted(stems.items()):
        usage, totals = kinds.get("usage", []), kinds.get("org_totals", [])
        if usage and totals and usage[0].provider in ("anthropic", "litellm"):
            failures += ["%s: %s" % (stem, f) for f in meterline.actor_cost_failures(usage, totals)]
    return failures


def copilot_batch_kinds(folder):
    """{batch_id: set of kinds} of the files that belong to a GitHub Copilot pull."""
    ids = {p["batch_id"] for p in read_json(folder, "ground_truth.json")["pulls"] if p["provider"] == "github_copilot"}
    found = {}
    for kind, path in jsonl_files(folder):
        if path.stem in ids:
            found.setdefault(path.stem, set()).add(kind)
    return ids, found


def test_done_check_S04_every_record_validates(tiny_run, vertical_run):
    for folder in (tiny_run, vertical_run):
        assert jsonl_files(folder)
        assert s04_failures(folder) == []
        assert vmod.v1_records(folder) == []
        # Copilot: the files just validated include its main batch (four kinds) and its daily seat lists
        ids, found = copilot_batch_kinds(folder)
        assert ids and set(found) == ids
        assert {"usage", "activity", "seats", "org_totals"} <= set().union(*found.values())
        assert any(kinds == {"seat_assignments"} for kinds in found.values())


def test_done_check_S04_negative_control_broken_records_fail(tiny_run, tmp_path):
    broken = copy_run(tiny_run, tmp_path)
    kind, path = next((k, p) for k, p in jsonl_files(broken) if k == "usage")

    def corrupt(lines):
        lines[0]["tokens_input_total"] = lines[0]["tokens_input_total"] + 1
        return lines

    mutate_jsonl(path, corrupt)
    assert s04_failures(broken) != []
    assert vmod.v1_records(broken) != []

    stale = copy_run(tiny_run, tmp_path / "stale")
    _kind, path2 = next((k, p) for k, p in jsonl_files(stale) if k == "org_totals")
    mutate_jsonl(path2, lambda lines: [dict(x, batch_id="20200101T000000Z") for x in lines])
    assert s04_failures(stale) != []
    assert vmod.v1_records(stale) != []


def test_done_check_S05_silver_totals_equal_the_ground_truth(tiny_run, vertical_run):
    for folder in (tiny_run, vertical_run):
        truth = read_json(folder, "ground_truth.json")["totals"]
        assert truth
        assert compare_totals(silver_totals(folder), truth) == []
        assert vmod.v2_totals(folder) == []
        # Copilot: its day credits, month records, token-only records and billing are among the groups compared
        copilot = {(t["kind"], t["cost_basis"], t["grain"]) for t in truth if t["provider"] == "github_copilot"}
        assert {("usage", "consumption", "day"), ("usage", "consumption", "month"), ("usage", None, "day"),
                ("org_totals", "billed", "day")} <= copilot


def test_done_check_S05_negative_control_one_cent_off_fails(tiny_run, tmp_path):
    truth = read_json(tiny_run, "ground_truth.json")["totals"]
    broken_rows = [dict(t) for t in truth]
    victim = next(t for t in broken_rows if t["cost"] is not None)
    victim["cost"] = str(Decimal(victim["cost"]) + Decimal("0.01"))
    assert compare_totals(silver_totals(tiny_run), broken_rows) != []

    broken = copy_run(tiny_run, tmp_path)

    def bump(gt):
        row = next(t for t in gt["totals"] if t["cost"] is not None)
        row["cost"] = str(Decimal(row["cost"]) + Decimal("0.01"))

    mutate_json(broken / "ground_truth.json", bump)
    assert vmod.v2_totals(broken) != []


def test_done_check_S04_negative_control_a_run_without_copilot_has_no_copilot_batches(tmp_path):
    from sim_helpers import COPILOT_OFF, gen

    off = gen(tmp_path, "tiny", 7, 2, COPILOT_OFF)
    ids, found = copilot_batch_kinds(off)
    assert not ids and not found  # the presence assertions of the S04 check do not hold for this folder


def test_done_check_S05_negative_control_a_run_without_copilot_has_no_copilot_groups(tmp_path):
    from sim_helpers import COPILOT_OFF, gen

    off = gen(tmp_path, "tiny", 7, 2, COPILOT_OFF)
    truth = read_json(off, "ground_truth.json")["totals"]
    copilot = {(t["kind"], t["cost_basis"], t["grain"]) for t in truth if t["provider"] == "github_copilot"}
    assert not {("usage", "consumption", "day"), ("usage", "consumption", "month"), ("usage", None, "day"),
                ("org_totals", "billed", "day")} <= copilot
