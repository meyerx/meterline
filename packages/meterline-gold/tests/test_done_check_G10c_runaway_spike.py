"""Phase 3 done-check G10 c: the simulator's runaway target has a spike event on the first runaway day."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import yaml

from gold_helpers import person_key, read_dicts

PRESETS = ["tiny", "vertical"]


def _spike_params(example_files):
    policy = yaml.safe_load(example_files[0].read_text(encoding="utf-8"))
    spike = policy["alerts"]["spike"]
    return Decimal(spike["floor"]), int(spike["min_history_days"]), int(spike["trailing_days"])


@pytest.mark.parametrize("preset", PRESETS)
def test_done_check_G10c_runaway_target_has_a_spike_event(sim_runs, sim_builds, example_files, preset):
    gold, _ = sim_builds[preset]
    run = sim_runs[preset]
    floor, min_history, trailing = _spike_params(example_files)
    events = [e for e in run.truth["events"] if e["type"] == "runaway_agent" and e["details"]["applied"]]
    assert events, "the run has no applied runaway_agent event"
    event = events[0]
    pid, first_day = event["actors"][0], date.fromisoformat(sorted(event["dates"])[0])
    key = person_key(pid)

    # preconditions, asserted first
    day_cost = sum(
        (Decimal(r["cost_ref"] or 0) for r in read_dicts(gold / "central" / "person_day.csv")
         if r["person_key"] == key and r["day"] == first_day.isoformat() and r["identity_status"] == "resolved"),
        Decimal(0),
    )
    assert day_cost > floor, (day_cost, floor)
    people = [r for r in read_dicts(run.org / "people.csv") if r["person_id"] == pid]
    assert people
    period_start = min(r["day"] for r in read_dicts(gold / "central" / "person_day.csv"))[:7] + "-01"
    history = 0
    for offset in range(1, trailing + 1):
        d = first_day - timedelta(days=offset)
        if d.isoformat() < period_start:
            continue
        employed = any(
            r["valid_from"] <= d.isoformat() and (r["valid_to"] == "" or r["valid_to"] >= d.isoformat())
            for r in people
        )
        history += int(employed)
    assert history >= min_history, (history, min_history)

    events_out = [
        r for r in read_dicts(gold / "central" / "alert_events.csv")
        if r["event_type"] == "spike" and r["subject_key"] == key
    ]
    assert first_day.isoformat() in {r["day"] for r in events_out}, events_out
    # negative control: the day before the runaway has no spike event for the target
    before = (first_day - timedelta(days=1)).isoformat()
    assert before not in {r["day"] for r in events_out}
