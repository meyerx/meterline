"""The publish step end to end on real data: the simulator's ``tiny`` preset over two months, real gold builds on the days
that matter (end of April + 3, + 4, a rerun the same day, + 14, a change of the hashing key), one module-scoped fixture.

The connectors are stood in for (their data is the simulator's, cut at the day by ``sim_silver_through``); silver, gold,
the freeze and the publish are the real ones. Every check below reads files with ``csv`` and ``json`` and compares them
with the files of a build, never with a number the code under test computed.
"""
import hashlib
import io
import json
import shutil
import stat
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from meterline_gold import servable
from meterline_job import job as job_module
from meterline_job import publish
from meterline_job.job import run_job
from publish_helpers import EXAMPLES, count_non_final, month_rows, read_csv, sim_silver_through

pytestmark = pytest.mark.slow
meterline_sim = pytest.importorskip("meterline_sim")

KEY_ONE = "a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
KEY_TWO = "another-key-of-at-least-32-bytes-kept-out-of-git"
NAMES = ["anthropic", "github_copilot", "litellm"]


def correct_april(record):
    """A late correction of April: more output tokens and a higher cost on every April usage record."""
    if record.get("period_end", "") < "2026-05-01" and "tokens_output" in record and record["tokens_output"] is not None:
        record["tokens_output"] = int(record["tokens_output"]) + 1000
    if record.get("period_end", "") < "2026-05-01" and record.get("cost") is not None and "model" in record:
        record["cost"] = str(Decimal(record["cost"]) + Decimal("0.25"))
    return record


def nonempty(rows_by_file):
    """A file that has no row of the month says nothing (a person who joined after the freeze has a folder with no April)."""
    return {rel: rows for rel, rows in rows_by_file.items() if rows}


def snapshot(root):
    """Copy of a folder's files as {relative path: bytes}."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    base = tmp_path_factory.mktemp("publish_slow")
    sim = Path(meterline_sim.generate(preset="tiny", seed=7, months=2, out=base / "sim"))
    cfg = {
        "data_root": str(base / "data"), "gold_root": str(base / "gold"),
        "gold": {"org": str(sim / "org"), "policy": str(EXAMPLES / "policy.example.yaml"),
                 "prices": str(EXAMPLES / "prices.example.yaml"), "parquet": True},
        "connector": [{"name": "anthropic", "kind": "anthropic", "since": "2026-04-01"},
                      {"name": "github_copilot", "kind": "copilot", "since": "2026-04-01"},
                      {"name": "litellm", "kind": "litellm", "since": "2026-04-01"}],
        "publish": {"keep_builds": 20},  # these tests read earlier builds; pruning has its own tests
    }
    mp = pytest.MonkeyPatch()
    ok = SimpleNamespace(ok=True, exit_code=0, failure_kind="none", http_status=None, batch_id=None, records={}, skipped=[])
    mp.setattr(job_module, "run_connector", lambda spec, data_root, transports, now, sleep=None: ("daily", ok))

    def done(spec, data_dir):
        path = Path(data_dir) / "complete_through.json"
        return date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["through"]) if path.is_file() else None

    mp.setattr(publish, "connector_complete_through", done)
    states = {}

    def day_run(label, day, key=KEY_ONE, correct=None, hour=6, down=()):
        mp.setenv("METERLINE_GOLD_KEY", key)
        sim_silver_through(sim, base / "data", day, non_final_days=3, correct=correct)
        for name in down:  # a connector that is down has no final data past the middle of April
            (base / "data" / name / "complete_through.json").write_text(json.dumps({"through": "2026-04-20"}), encoding="utf-8")
        out = io.StringIO()
        code = run_job(cfg, now=lambda: datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc), out=out,
                       today=day, sleep=lambda _s: None)
        lines = {json.loads(line)["step"]: json.loads(line) for line in out.getvalue().splitlines()}
        gold = base / "gold"
        states[label] = {"code": code, "lines": lines, "published": snapshot(gold / "published"),
                         "frozen": snapshot(gold / "frozen") if (gold / "frozen").is_dir() else {},
                         "builds": sorted(p.name for p in (gold / "builds").iterdir()),
                         "frozen_json": json.loads((gold / "frozen.json").read_text(encoding="utf-8")) if (gold / "frozen.json").is_file() else {}}
        return states[label]

    day_run("end+3", date(2026, 5, 3))
    day_run("end+4 down", date(2026, 5, 4), down=("litellm",))
    day_run("end+4", date(2026, 5, 4), hour=9)
    day_run("rerun", date(2026, 5, 4), hour=12)
    day_run("end+14", date(2026, 5, 14), correct=correct_april)
    day_run("key change", date(2026, 5, 15), key=KEY_TWO, correct=correct_april)
    yield SimpleNamespace(base=base, gold=base / "gold", states=states, config=cfg)
    mp.undo()


def published_tree(state, tmp_path, name="p"):
    root = tmp_path / name
    for rel, data in state["published"].items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def test_every_run_of_the_scenario_succeeded(scenario):
    for label, state in scenario.states.items():
        assert state["code"] == 0, (label, state["lines"])
        assert state["lines"]["publish"]["exit_code"] == 0 and state["lines"]["gold"]["exit_code"] == 0, label


def test_one_build_per_run_and_a_same_day_rerun_gets_its_own_folder(scenario):
    assert scenario.states["end+3"]["builds"] == ["2026-05-03"]
    assert scenario.states["end+4 down"]["builds"] == ["2026-05-03", "2026-05-04"]
    assert scenario.states["end+4"]["builds"] == ["2026-05-03", "2026-05-04", "2026-05-04-2"]
    assert scenario.states["rerun"]["builds"] == ["2026-05-03", "2026-05-04", "2026-05-04-2", "2026-05-04-3"]
    assert scenario.states["key change"]["builds"][-1] == "2026-05-15" and len(scenario.states["key change"]["builds"]) == 6


def test_three_days_after_the_end_nothing_is_frozen_and_april_follows_the_build(scenario, tmp_path):
    state = scenario.states["end+3"]
    assert state["frozen"] == {} and state["lines"]["publish"]["frozen"] == []
    first = scenario.gold / "builds" / "2026-05-03"
    assert nonempty(month_rows(published_tree(state, tmp_path), "2026-04")) == nonempty(month_rows(first, "2026-04"))


def test_a_connector_that_is_down_on_the_fourth_day_delays_the_freeze(scenario, tmp_path):
    state = scenario.states["end+4 down"]
    assert state["code"] == 0 and state["lines"]["publish"]["frozen"] == [] and state["frozen"] == {}
    build = scenario.gold / "builds" / "2026-05-04"
    assert nonempty(month_rows(published_tree(state, tmp_path), "2026-04")) == nonempty(month_rows(build, "2026-04"))


def test_april_is_frozen_when_the_connector_is_back_from_that_runs_build(scenario, tmp_path):
    state = scenario.states["end+4"]
    assert state["lines"]["publish"]["frozen"] == ["2026-04"] and state["lines"]["publish"]["warnings"] == []
    build = scenario.gold / "builds" / "2026-05-04-2"
    # the freeze rule read directly: no April row of that build may still change, and the build does hold May rows that may
    assert count_non_final(build, "2026-04") == 0 and count_non_final(build, "2026-05") > 0
    assert state["frozen_json"]["2026-04"]["source_build"] == "2026-05-04-2"
    frozen = tmp_path / "frozen"
    for rel, data in state["frozen"].items():
        if rel.startswith("2026-04/"):
            (frozen / rel).parent.mkdir(parents=True, exist_ok=True)
            (frozen / rel).write_bytes(data)
    assert nonempty(month_rows(frozen / "2026-04", "2026-04")) == nonempty(month_rows(build, "2026-04"))
    assert nonempty(month_rows(published_tree(state, tmp_path), "2026-04")) == nonempty(month_rows(build, "2026-04"))
    assert not any(rel.startswith("2026-05") for rel in state["frozen"])


def test_a_second_rerun_of_the_freeze_day_gets_its_own_build_and_leaves_the_frozen_month_alone(scenario):
    before, after = scenario.states["end+4"]["frozen"], scenario.states["rerun"]["frozen"]
    assert before == after and before
    assert scenario.states["rerun"]["lines"]["publish"]["frozen"] == []
    assert scenario.states["rerun"]["lines"]["gold"]["build"] == "2026-05-04-3"


def test_a_late_correction_changes_the_latest_build_and_not_the_published_april(scenario, tmp_path):
    state = scenario.states["end+14"]
    latest = scenario.gold / "builds" / "2026-05-14"
    first_freeze = scenario.gold / "builds" / "2026-05-04-2"
    served = published_tree(state, tmp_path)
    assert month_rows(latest, "2026-04") != month_rows(first_freeze, "2026-04"), "the planted correction changed nothing"
    assert nonempty(month_rows(served, "2026-04")) == nonempty(month_rows(first_freeze, "2026-04"))
    assert month_rows(served, "2026-05") != {} and state["frozen_json"] == scenario.states["end+4"]["frozen_json"]
    june_free = month_rows(latest, "2026-05")
    assert nonempty(june_free) == nonempty(month_rows(served, "2026-05"))  # the month in progress: the latest build's, row for row


def test_after_a_key_change_the_keyed_folders_restart_and_exec_keeps_its_frozen_month(scenario, tmp_path):
    state = scenario.states["key change"]
    served = published_tree(state, tmp_path)
    first_freeze = scenario.gold / "builds" / "2026-05-04-2"
    for kind in ("person", "manager"):
        months = set()
        for path in (served / kind).rglob("*.csv"):
            header, rows = read_csv(path)
            months.update(r[header.index("month")] for r in rows)
        assert months == {"2026-05"}, kind
    old_keys = {p.name for p in (first_freeze / "person").iterdir()}
    new_keys = {p.name for p in (served / "person").iterdir()}
    assert old_keys and new_keys and not old_keys & new_keys
    assert nonempty(month_rows(served / "exec", "2026-04")) == nonempty(month_rows(first_freeze / "exec", "2026-04"))
    assert nonempty(month_rows(served / "exec", "2026-04"))
    header, rows = read_csv(served / "central" / "person_month.csv")
    april_keys = {r[header.index("person_key")] for r in rows if r[header.index("month")] == "2026-04"}
    may_keys = {r[header.index("person_key")] for r in rows if r[header.index("month")] == "2026-05"}
    assert april_keys and may_keys and april_keys <= old_keys and may_keys <= new_keys and not april_keys & may_keys  # two keys in central
    data = json.loads((served / "central" / "published.json").read_text(encoding="utf-8"))
    assert data["key_rotations"] == ["2026-04"] and data["frozen_months"] == ["2026-04"] and data["in_progress_months"] == ["2026-05"]
    assert json.loads((served / "person" / sorted(new_keys)[0] / "freshness.json").read_text(encoding="utf-8"))["frozen_months"] == []


def test_the_last_published_folder_is_private_and_servable_and_holds_no_delivery(scenario):
    published = scenario.gold / "published"
    folder = scenario.gold / published.readlink()
    assert not any(p.name == "delivery" for p in folder.rglob("*"))
    bad = [p for p in [folder, *folder.rglob("*")]
           if stat.S_IMODE(p.lstat().st_mode) != (0o700 if p.is_dir() else 0o600)]
    assert not bad
    for path in folder.rglob("*"):
        if path.is_file():
            rel = path.relative_to(folder).as_posix()
            first = rel.split("/")[0]
            audience = "central" if first == "manifest.json" else first
            key = rel.split("/")[1] if first in ("manager", "person") else None
            assert servable(rel, audience + (":" + key if key else "")), rel
    assert sorted(p.name for p in scenario.gold.glob("published-*")) == sorted(
        [published.readlink().name, scenario.states["end+14"]["lines"]["publish"]["published"]])


def test_the_frozen_checksums_hold_after_every_run(scenario):
    frozen = scenario.states["key change"]["frozen"]
    sums = frozen["2026-04/SHA256SUMS"].decode().splitlines()
    assert sums
    for line in sums:
        digest, _, rel = line.partition("  ")
        assert hashlib.sha256(frozen["2026-04/" + rel]).hexdigest() == digest
    shutil.rmtree(scenario.base / "unused", ignore_errors=True)
