"""F-11: a damaged state file stops the run with exit 2 and a message that
names the file and the way out; a missing one on ``daily`` is a warning that
needs ``--since``. Neither case silently restarts the month or ends in a
traceback.
"""

import json

import pytest

from meterline_copilot.testing import item, user_row
from copilot_helpers import at, backfill, bare_daily as daily, run

DAY = "2026-09-18"


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
    if how == "days_is_a_number":
        state["days_loaded"] = 5
    elif how == "bad_date":
        state["days_loaded"] = ["2026-13-45"]
    elif how == "date_is_a_number":
        state["days_loaded"] = [20260918]
    elif how == "provider_account_id_is_a_number":
        state["provider_account_id"] = 7
    elif how == "pending_is_a_string":
        state["pending"] = "x"
    elif how == "pending_steps_wrong":
        state["pending"] = {"command": "daily", "steps": {"users:a": ["raw/x.json.gz"]}}
    elif how == "pending_path_escapes":
        state["pending"] = {"command": "daily", "steps": {"users:a": "../../etc/passwd"}}
    elif how == "key_removed":
        del state["days_loaded"]
    return json.dumps(state)


CASES = [
    "truncated", "empty_file", "binary_garbage", "not_an_object", "empty_object", "days_is_a_number", "bad_date",
    "date_is_a_number", "provider_account_id_is_a_number", "pending_is_a_string", "pending_steps_wrong", "pending_path_escapes",
    "key_removed",
]


def _api(make_api, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    api.billing_day[DAY] = [item()]
    return api


@pytest.fixture
def loaded(make_api, fake_clock, data_dir, level):
    api = _api(make_api, level)
    assert backfill(data_dir, api, fake_clock, since=DAY) == 0
    return api, json.loads((data_dir / "state.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("how", CASES)
@pytest.mark.parametrize("command", ["daily", "backfill"])
def test_state_file_damaged_stops_with_exit_2(how, command, loaded, fake_clock, data_dir, capsys):
    api, state = loaded
    path = data_dir / "state.json"
    path.write_text(_damage(state, how), encoding="utf-8")
    before = path.read_bytes()
    records = sorted(p.name for p in data_dir.rglob("*.jsonl"))
    seen = len(api.log)
    capsys.readouterr()

    later = at("2026-09-20T07:00:00")
    rc = daily(data_dir, api, fake_clock, later) if command == "daily" else backfill(data_dir, api, fake_clock, since=DAY, now=later)
    err = capsys.readouterr().err
    assert rc == 2
    assert str(path) in err, "the message names the file"
    assert "damaged" in err
    assert "restore" in err and "--since" in err, "the message says how to recover"
    assert "Traceback" not in err
    assert path.read_bytes() == before, "the damaged file is left for the owner to look at"
    assert len(api.log) == seen, "no request is made"
    assert sorted(p.name for p in data_dir.rglob("*.jsonl")) == records


def test_state_file_damaged_rebuild_does_not_read_it(loaded, fake_clock, data_dir):
    api, state = loaded
    (data_dir / "state.json").write_text(_damage(state, "truncated"), encoding="utf-8")
    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 0


def test_state_file_missing_daily_needs_since(make_api, fake_clock, data_dir, level, capsys):
    api = _api(make_api, level)
    rc = daily(data_dir, api, fake_clock)
    err = capsys.readouterr().err
    assert rc == 2
    assert str(data_dir / "state.json") in err
    assert "--since" in err
    assert "Traceback" not in err
    assert api.log == [], "no request is made"
    assert not list(data_dir.rglob("*.jsonl"))


def test_state_file_missing_after_a_loaded_month_does_not_restart_the_month(loaded, fake_clock, data_dir, capsys):
    api, _state = loaded
    (data_dir / "state.json").unlink()
    seen = len(api.log)
    assert daily(data_dir, api, fake_clock, at("2026-09-20T07:00:00")) == 2
    assert len(api.log) == seen
    assert "--since" in capsys.readouterr().err


def test_state_file_missing_daily_with_since_warns_and_starts_there(make_api, fake_clock, data_dir, level, capsys):
    api = _api(make_api, level)
    rc = daily(data_dir, api, fake_clock, at("2026-09-20T06:00:00"), "--since", "2026-09-10")
    err = capsys.readouterr().err
    assert rc == 0
    assert "no state file" in err and "2026-09-10" in err
    days = sorted(e.params["day"] for e in api.api_requests("users-1-day"))
    assert days and days[0] == "2026-09-10"
    assert (data_dir / "state.json").is_file()


def test_state_file_missing_since_is_ignored_when_a_state_file_exists(loaded, fake_clock, data_dir, capsys):
    api, _state = loaded
    capsys.readouterr()
    assert daily(data_dir, api, fake_clock, at("2026-09-20T07:00:00"), "--since", "2026-08-01") == 0
    assert "ignored" in capsys.readouterr().err
    days = [e.params["day"] for e in api.api_requests("users-1-day")]
    assert min(days[1:]) >= "2026-09-14"  # the first entry is the backfill's


def test_state_file_missing_is_fine_for_backfill(make_api, fake_clock, data_dir, level):
    api = _api(make_api, level)
    assert backfill(data_dir, api, fake_clock, since=DAY) == 0
