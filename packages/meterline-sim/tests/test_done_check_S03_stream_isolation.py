"""S03 streams are isolated (spec section 15)."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import meterline_sim  # noqa: F401
from sim_helpers import (
    COPILOT_OFF, all_files, anthropic_actor_id, by_key, d, gen, main_batch_id, pull_batch_ids, read_json,
)

ANTHROPIC_KINDS = ("usage", "activity", "seats", "org_totals")


def _differs(a, b, name):
    pa, pb = Path(a) / name, Path(b) / name
    return not (pa.is_file() and pb.is_file()) or pa.read_bytes() != pb.read_bytes()


def _diff_keys(a, b, kind):
    ra, rb = by_key(a, kind), by_key(b, kind)
    return [(key, ra.get(key), rb.get(key)) for key in sorted(set(ra) | set(rb), key=repr)
            if ra.get(key) != rb.get(key)]


def _event(folder, kind):
    events = [e for e in read_json(folder, "ground_truth.json")["events"] if e["type"] == kind]
    assert events, "no %s event" % kind
    assert events[0]["details"]["applied"] is True
    return events[0]


def _files_outside(a, b, main_batch):
    """Names of files, other than the manifest, the ground truth and the Anthropic main batch, that differ."""
    def keep(name):
        return name not in ("manifest.json", "ground_truth.json") and not name.endswith(main_batch + ".jsonl")

    names_a = [n for n in all_files(a) if keep(n)]
    names_b = [n for n in all_files(b) if keep(n)]
    if names_a != names_b:
        return ["file lists differ"]
    return [n for n in names_a if (a / n).read_bytes() != (b / n).read_bytes()]


def runaway_failures(on, off):
    """The assertions of S03 for the runaway agent, between a run with the event and one without."""
    failures = []
    event = _event(on, "runaway_agent")
    days = {d(x) for x in event["dates"]}
    target = anthropic_actor_id(on, event["actors"][0])
    model = event["details"]["model"]
    window = read_json(on, "ground_truth.json")["parameters"]["providers"]["anthropic"]["active_windows"][1]
    last_snapshot = max(days) + timedelta(days=window - 1)
    failures += ["file differs outside the Anthropic main batch: " + n
                 for n in _files_outside(on, off, main_batch_id(on, "anthropic"))]
    target_model_days, total_days = set(), set()
    for kind in ANTHROPIC_KINDS:
        for _key, on_rec, off_rec in _diff_keys(on, off, kind):
            rec = on_rec or off_rec
            day = d(rec["period_start"])
            if kind == "usage":
                if not (rec["actor_id"] == target and rec["product"] == "claude_code" and day in days):
                    failures.append("usage record outside the target's runaway days: %r" % (rec["period_start"],))
                elif rec["model"] == model:
                    target_model_days.add(day)
            elif kind == "org_totals":
                if day not in days:
                    failures.append("org total outside the runaway days: %s" % day)
                total_days.add(day)
            elif kind == "seats":
                changed = None if on_rec is None or off_rec is None else {k for k in on_rec if on_rec[k] != off_rec[k]}
                if not (min(days) <= day <= last_snapshot):
                    failures.append("seat snapshot outside the window: %s" % day)
                if not changed or not changed <= {"daily_active", "weekly_active", "monthly_active"}:
                    failures.append("seat snapshot differs in more than the active counts: %s" % day)
            else:
                failures.append("activity record differs: %s" % rec["period_start"])
    if target_model_days != days:
        failures.append("the target's records with the event's model differ on %s, not on every runaway day" % sorted(target_model_days))
    if total_days != days:
        failures.append("the organisation totals differ on %s, not on every runaway day" % sorted(total_days))
    return failures


def price_change_failures(on, off):
    failures = []
    event = _event(on, "price_change")
    when = d(event["dates"][0])
    model = event["details"]["model"]
    provider = event["details"]["provider"]
    for name in all_files(on):
        if not name.startswith("records/") and name not in ("manifest.json", "ground_truth.json"):
            if (on / name).read_bytes() != (off / name).read_bytes():
                failures.append("organisation file differs: " + name)
    for name in ("manifest.json", "ground_truth.json"):
        if (on / name).read_bytes() == (off / name).read_bytes():
            failures.append(name + " does not differ")
    litellm = main_batch_id(on, "litellm")
    for name in all_files(on):
        if name.endswith(litellm + ".jsonl") and (on / name).read_bytes() != (off / name).read_bytes():
            failures.append("a litellm file differs: " + name)
    copilot = {p["batch_id"] for p in read_json(on, "ground_truth.json")["pulls"] if p["provider"] == "github_copilot"}
    for name in all_files(on):
        if Path(name).stem in copilot and name.startswith("records/") and _differs(on, off, name):
            failures.append("a Copilot file differs: " + name)
    total = 0
    for kind in ANTHROPIC_KINDS:
        for _key, on_rec, off_rec in _diff_keys(on, off, kind):
            total += 1
            if kind not in ("usage", "org_totals") or on_rec is None or off_rec is None:
                failures.append("%s record differs" % kind)
                continue
            if on_rec["provider"] != provider:
                failures.append("a record of another provider differs")
            if d(on_rec["period_start"]) < when:
                failures.append("a record before the change date differs: %s" % on_rec["period_start"])
            if kind == "usage" and on_rec["model"] != model:
                failures.append("a usage record of another model differs")
            if not {k for k in on_rec if on_rec[k] != off_rec[k]} <= {"cost", "cost_list"}:
                failures.append("fields other than cost and cost_list differ")
    if total < 1:
        failures.append("no record differs")
    return failures


def test_done_check_S03_runaway_on_and_off(tiny_run, tmp_path):
    off = gen(tmp_path, "tiny", 7, 6, {"events": {"runaway_agent": {"enabled": False}}})
    assert runaway_failures(tiny_run, off) == []


def test_done_check_S03_price_change_on_and_off(tiny_run, tmp_path):
    off = gen(tmp_path, "tiny", 7, 6, {"events": {"price_change": {"enabled": False}}})
    assert price_change_failures(tiny_run, off) == []


def test_done_check_S03_negative_control_unrelated_difference_is_reported(tiny_run, tmp_path):
    # a run that also changes another stream (the seed) must not pass the isolation assertions
    other_seed = gen(tmp_path / "seed", "tiny", 8, 6, {"events": {"runaway_agent": {"enabled": False}}})
    assert runaway_failures(tiny_run, other_seed) != []
    other_price = gen(tmp_path / "price", "tiny", 8, 6, {"events": {"price_change": {"enabled": False}}})
    assert price_change_failures(tiny_run, other_price) != []
    # and a run with the event on in both is reported as "nothing differs"
    same = gen(tmp_path / "same", "tiny", 7, 6)
    assert price_change_failures(tiny_run, same) != []


# ---- Copilot on and off (D9: a new feature never changes the bytes of the others) -------------------------

REFERENCE = Path(__file__).with_name("phase1_reference_tiny_s7_m6.json")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def copilot_isolation_failures(folder, copilot_on):
    """A tiny run (seed 7, 6 months) against the bytes the phase 1 build wrote for it with Copilot off.

    Copilot off: every file is the phase 1 file. Copilot on: every Anthropic and LiteLLM batch file and every
    organisation file but identities.csv is the phase 1 file, identities.csv is the phase 1 file plus
    github_copilot rows, and the ground truth and the manifest differ."""
    folder = Path(folder)
    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    expected = dict(ref["files"], **{"manifest.json": ref["manifest_sha256"]})
    failures = []
    for name, want in sorted(expected.items()):
        if not (folder / name).is_file():
            failures.append("missing: " + name)
        elif name == "org/identities.csv" and copilot_on:
            kept = [line for line in (folder / name).read_text(encoding="utf-8").splitlines(keepends=True)
                    if ",github_copilot," not in line]
            if hashlib.sha256("".join(kept).encode("utf-8")).hexdigest() != want:
                failures.append("identities.csv differs by more than github_copilot rows")
        elif name in ("ground_truth.json", "manifest.json") and copilot_on:
            if _sha(folder / name) == want:
                failures.append(name + " does not differ with Copilot on")
        elif _sha(folder / name) != want:
            failures.append("differs from phase 1: " + name)
    extra = sorted(set(all_files(folder)) - set(expected))
    if not copilot_on and extra:
        failures.append("files that phase 1 did not write: %s" % extra)
    if copilot_on and not any(n.startswith("records/") for n in extra):
        failures.append("no Copilot file was written")
    return failures


def test_done_check_S03_copilot_on_and_off(tiny_run, tmp_path):
    assert copilot_isolation_failures(tiny_run, copilot_on=True) == []
    off = gen(tmp_path, "tiny", 7, 6, COPILOT_OFF)
    assert copilot_isolation_failures(off, copilot_on=False) == []
    # the Copilot files are the whole difference: they carry the Copilot pulls and nothing else
    on_files = set(all_files(tiny_run)) - set(all_files(off))
    batches = set(pull_batch_ids(tiny_run, "github_copilot", "main")) | set(
        pull_batch_ids(tiny_run, "github_copilot", "seat_list")) | set(
        pull_batch_ids(tiny_run, "github_copilot", "correction"))
    assert on_files and {Path(n).stem for n in on_files} <= batches


def test_done_check_S03_negative_control_copilot_isolation_can_fail(tiny_run, tmp_path):
    # another seed changes the Anthropic and LiteLLM bytes: reported
    other = gen(tmp_path / "seed", "tiny", 8, 6, COPILOT_OFF)
    assert copilot_isolation_failures(other, copilot_on=False) != []
    # a run with Copilot on is not a phase 1 run: reported when judged as one
    assert copilot_isolation_failures(tiny_run, copilot_on=False) != []
    # and a run with Copilot off does not satisfy the Copilot-on expectations
    off = gen(tmp_path / "off", "tiny", 7, 6, COPILOT_OFF)
    assert copilot_isolation_failures(off, copilot_on=True) != []
