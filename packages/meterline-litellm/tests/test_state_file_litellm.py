"""F-11: a damaged state file stops the run with exit 2 and a message that
names the file and the way out; a missing one on ``daily`` is a warning that
needs ``--since``. Neither case silently restarts the month or ends in a
traceback.
"""

import json

import pytest

from litellm_helpers import at, backfill, basic_rows, daily, run, users


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
        state["days_loaded"] = [20260914]
    elif how == "pending_is_a_string":
        state["pending"] = "x"
    elif how == "pending_steps_wrong":
        state["pending"] = {"command": "daily", "steps": {"global:a": ["raw/x.json.gz"]}}
    elif how == "pending_path_escapes":
        state["pending"] = {"command": "daily", "steps": {"global:a": "../../etc/passwd"}}
    elif how == "key_removed":
        del state["days_loaded"]
    return json.dumps(state)


CASES = [
    "truncated", "empty_file", "binary_garbage", "not_an_object", "empty_object", "days_is_a_number", "bad_date",
    "date_is_a_number", "pending_is_a_string", "pending_steps_wrong", "pending_path_escapes", "key_removed",
]


@pytest.fixture
def loaded(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert backfill(data_dir, proxy, fake_clock) == 0
    return proxy, json.loads((data_dir / "state.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("how", CASES)
@pytest.mark.parametrize("command", ["daily", "backfill"])
def test_state_file_damaged_stops_with_exit_2(how, command, loaded, fake_clock, data_dir, capsys):
    proxy, state = loaded
    path = data_dir / "state.json"
    path.write_text(_damage(state, how), encoding="utf-8")
    before = path.read_bytes()
    records = sorted(p.name for p in data_dir.rglob("*.jsonl"))
    seen = len(proxy.log)
    capsys.readouterr()

    later = at("2026-09-20T07:00:00")
    rc = daily(data_dir, proxy, fake_clock, later) if command == "daily" else backfill(data_dir, proxy, fake_clock, now=later)
    err = capsys.readouterr().err
    assert rc == 2
    assert str(path) in err, "the message names the file"
    assert "damaged" in err
    assert "restore" in err and "--since" in err, "the message says how to recover"
    assert "Traceback" not in err
    assert path.read_bytes() == before, "the damaged file is left for the owner to look at"
    assert len(proxy.log) == seen, "no request is made"
    assert sorted(p.name for p in data_dir.rglob("*.jsonl")) == records


def test_state_file_damaged_rebuild_does_not_read_it(loaded, fake_clock, data_dir):
    proxy, state = loaded
    (data_dir / "state.json").write_text(_damage(state, "truncated"), encoding="utf-8")
    assert run(["rebuild", "--data-dir", str(data_dir)], proxy, fake_clock) == 0


def test_state_file_missing_daily_needs_since(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    rc = daily(data_dir, proxy, fake_clock)
    err = capsys.readouterr().err
    assert rc == 2
    assert str(data_dir / "state.json") in err
    assert "--since" in err
    assert "Traceback" not in err
    assert proxy.log == [], "no request is made"
    assert not list(data_dir.rglob("*.jsonl"))


def test_state_file_missing_after_a_loaded_month_does_not_restart_the_month(loaded, fake_clock, data_dir, capsys):
    proxy, _state = loaded
    (data_dir / "state.json").unlink()
    seen = len(proxy.log)
    assert daily(data_dir, proxy, fake_clock, at("2026-09-20T07:00:00")) == 2
    assert len(proxy.log) == seen
    assert "--since" in capsys.readouterr().err


def test_state_file_missing_daily_with_since_warns_and_starts_there(make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    rc = daily(data_dir, proxy, fake_clock, at("2026-09-20T06:00:00"), "--since", "2026-09-16")
    err = capsys.readouterr().err
    assert rc == 0
    assert "no state file" in err and "2026-09-16" in err
    starts = sorted(e.params["start_date"] for e in proxy.log if "start_date" in e.params)
    assert starts and starts[0] == "2026-09-16"
    assert (data_dir / "state.json").is_file()


def test_state_file_missing_since_is_ignored_when_a_state_file_exists(loaded, fake_clock, data_dir, capsys):
    proxy, _state = loaded
    capsys.readouterr()
    assert daily(data_dir, proxy, fake_clock, at("2026-09-20T07:00:00"), "--since", "2026-08-01") == 0
    assert "ignored" in capsys.readouterr().err
    assert min(e.params["start_date"] for e in proxy.log[-5:] if "start_date" in e.params) >= "2026-09-14"


def test_state_file_missing_is_fine_for_backfill(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert backfill(data_dir, proxy, fake_clock) == 0
