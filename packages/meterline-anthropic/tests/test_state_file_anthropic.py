"""F-11: a damaged state file stops the run with exit 2 and a message that
names the file and the way out; a missing one on ``daily`` is a warning that
needs ``--since``. Neither case silently restarts the month or ends in a
traceback.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meterline_anthropic.cli import main

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 5, 7, 0, 0, tzinfo=timezone.utc)


def _run(transport, clock, data_dir, *argv, now=NOW):
    return main(
        [*argv, "--data-dir", str(data_dir)],
        transport=transport, clock=clock, sleep=clock.sleep, now=lambda: now, jitter=lambda: 0,
    )


def _good_state(fake_api, fake_clock, data_dir):
    assert _run(fake_api.transport, fake_clock, data_dir, "backfill", "--since", "2026-10-01") == 0
    return json.loads((Path(data_dir) / "state.json").read_text(encoding="utf-8"))


def _damage(state, how):
    if how == "truncated":
        return json.dumps(state)[: len(json.dumps(state)) // 2]
    if how == "empty_file":
        return ""
    if how == "binary_garbage":
        return "\x00\x01\x02\xff"
    if how == "not_an_object":
        return "[]"
    if how == "empty_object":
        return "{}"
    state = json.loads(json.dumps(state))
    if how == "list_is_a_number":
        state["activity_loaded"] = 5
    elif how == "bad_date":
        state["summaries_loaded"] = ["2026-13-45"]
    elif how == "date_is_a_number":
        state["activity_loaded"] = [20261001]
    elif how == "refreshed_at_is_a_list":
        state["refreshed_at"] = []
    elif how == "pending_is_a_string":
        state["pending"] = "x"
    elif how == "pending_steps_wrong":
        state["pending"] = {"command": "daily", "steps": {"usage:a": "raw/x.json.gz"}}
    elif how == "pending_path_escapes":
        state["pending"] = {"command": "daily", "steps": {"usage:a": ["../../etc/passwd"]}}
    elif how == "key_removed":
        del state["activity_loaded"]
    return json.dumps(state)


CASES = [
    "truncated", "empty_file", "binary_garbage", "not_an_object", "empty_object", "list_is_a_number", "bad_date",
    "date_is_a_number", "refreshed_at_is_a_list", "pending_is_a_string", "pending_steps_wrong",
    "pending_path_escapes", "key_removed",
]


@pytest.mark.parametrize("how", CASES)
@pytest.mark.parametrize("command", [("daily",), ("backfill", "--since", "2026-10-01")])
def test_state_file_damaged_stops_with_exit_2(how, command, fake_api, fake_clock, tmp_path, capsys):
    state = _good_state(fake_api, fake_clock, tmp_path)
    path = tmp_path / "state.json"
    path.write_text(_damage(state, how), encoding="utf-8")
    before = path.read_bytes()
    records = sorted(p.name for p in tmp_path.rglob("*.jsonl"))
    seen = len(fake_api.log)
    capsys.readouterr()

    rc = _run(fake_api.transport, fake_clock, tmp_path, *command, now=LATER)
    err = capsys.readouterr().err
    assert rc == 2
    assert str(path) in err, "the message names the file"
    assert "damaged" in err
    assert "restore" in err and "--since" in err, "the message says how to recover"
    assert "Traceback" not in err
    assert path.read_bytes() == before, "the damaged file is left for the owner to look at"
    assert len(fake_api.log) == seen, "no request is made"
    assert sorted(p.name for p in tmp_path.rglob("*.jsonl")) == records


def test_state_file_damaged_rebuild_does_not_read_it(fake_api, fake_clock, tmp_path):
    state = _good_state(fake_api, fake_clock, tmp_path)
    (tmp_path / "state.json").write_text(_damage(state, "truncated"), encoding="utf-8")
    assert main(["rebuild", "--data-dir", str(tmp_path)]) == 0


def test_state_file_missing_daily_needs_since(fake_api, fake_clock, tmp_path, capsys):
    rc = _run(fake_api.transport, fake_clock, tmp_path, "daily")
    err = capsys.readouterr().err
    assert rc == 2
    assert str(tmp_path / "state.json") in err
    assert "--since" in err
    assert "Traceback" not in err
    assert fake_api.log == [], "no request is made"
    assert not list(tmp_path.rglob("*.jsonl"))


def test_state_file_missing_after_a_loaded_month_does_not_restart_the_month(fake_api, fake_clock, tmp_path, capsys):
    _good_state(fake_api, fake_clock, tmp_path)
    (tmp_path / "state.json").unlink()
    seen = len(fake_api.log)
    assert _run(fake_api.transport, fake_clock, tmp_path, "daily", now=LATER) == 2
    assert len(fake_api.log) == seen
    assert "--since" in capsys.readouterr().err


def test_state_file_missing_daily_with_since_warns_and_starts_there(fake_api, fake_clock, tmp_path, capsys):
    rc = _run(fake_api.transport, fake_clock, tmp_path, "daily", "--since", "2026-10-03")
    err = capsys.readouterr().err
    assert rc == 0
    assert "no state file" in err and "2026-10-03" in err
    days = sorted(e.params["date"] for e in fake_api.requests_for("users"))
    assert days and days[0] == "2026-10-03"
    assert (tmp_path / "state.json").is_file()


def test_state_file_missing_since_is_ignored_when_a_state_file_exists(fake_api, fake_clock, tmp_path, capsys):
    _good_state(fake_api, fake_clock, tmp_path)
    capsys.readouterr()
    assert _run(fake_api.transport, fake_clock, tmp_path, "daily", "--since", "2026-09-01", now=LATER) == 0
    assert "ignored" in capsys.readouterr().err
    assert min(e.params["date"] for e in fake_api.requests_for("users")) >= "2026-10-01"


def test_state_file_missing_is_fine_for_backfill(fake_api, fake_clock, tmp_path):
    assert _run(fake_api.transport, fake_clock, tmp_path, "backfill", "--since", "2026-10-01") == 0
