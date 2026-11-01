"""F-26: a pull never replaces an earlier raw file. A second pull in the same
UTC second (same file name) is refused; the raw evidence the first run built
its records on stays as it was. A run that starts in the second of an earlier
finished run is refused before it asks GitHub anything.
"""

import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest

from meterline_copilot.testing import item, user_row
from copilot_helpers import FAKE_BILLING_TOKEN, FAKE_TOKEN, NOW, backfill, bare_daily, run
from meterline_copilot.client import CopilotClient
from meterline_copilot.fetch import Level, RawFileExists, fetch_report

DAY = "2026-09-18"


def _tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _api(make_api, level):
    api = make_api()
    api.users[DAY] = [user_row(DAY, 1, "login1", level=level)]
    api.billing_day[DAY] = [item()]
    return api


def _client(api, clock):
    return CopilotClient(FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, transport=api.transport, clock=clock,
                         sleep=clock.sleep, wall=clock, jitter=lambda: 0)


def test_raw_not_overwritten_a_second_fetch_in_the_same_second_is_refused(make_api, fake_clock, data_dir, level):
    api = _api(make_api, level)
    lvl = Level(level, api.slug)
    with _client(api, fake_clock) as client:
        first = fetch_report(client, data_dir, lvl, "users", date(2026, 9, 18), NOW)
        before = first.path.read_bytes()
        api.users[DAY] = []  # GitHub now answers differently
        with pytest.raises(RawFileExists, match="never replaced"):
            fetch_report(client, data_dir, lvl, "users", date(2026, 9, 18), NOW)
    assert first.path.read_bytes() == before
    assert not list(data_dir.rglob("*.tmp"))


def test_raw_not_overwritten_a_later_second_writes_a_new_file(make_api, fake_clock, data_dir, level):
    api = _api(make_api, level)
    lvl = Level(level, api.slug)
    with _client(api, fake_clock) as client:
        first = fetch_report(client, data_dir, lvl, "users", date(2026, 9, 18), NOW)
        second = fetch_report(client, data_dir, lvl, "users", date(2026, 9, 18), NOW + timedelta(seconds=1))
    assert first.path != second.path and first.path.exists() and second.path.exists()


@pytest.mark.parametrize("command", ["backfill", "daily"])
def test_raw_not_overwritten_a_run_in_the_second_of_a_finished_run_is_refused(command, make_api, fake_clock, data_dir, level, capsys):
    api = _api(make_api, level)

    def go(now):
        return backfill(data_dir, api, fake_clock, since=DAY, now=now) if command == "backfill" else bare_daily(
            data_dir, api, fake_clock, now, "--since", DAY)

    assert go(NOW) == 0
    before = _tree_hashes(data_dir)
    seen = len(api.log)
    capsys.readouterr()

    assert go(NOW) == 2
    err = capsys.readouterr().err
    assert "same second" in err and "Traceback" not in err
    assert len(api.log) == seen, "nothing is requested"
    assert _tree_hashes(data_dir) == before, "raw files, records, manifest and state are as they were"

    assert go(NOW + timedelta(seconds=1)) == 0


def test_raw_not_overwritten_rebuild_is_not_blocked(make_api, fake_clock, data_dir, level):
    api = _api(make_api, level)
    assert backfill(data_dir, api, fake_clock, since=DAY) == 0
    assert run(["rebuild", "--data-dir", str(data_dir)], api, fake_clock) == 0
