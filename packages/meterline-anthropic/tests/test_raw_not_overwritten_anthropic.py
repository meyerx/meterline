"""F-26: a pull never replaces an earlier raw file. A second pull in the same
UTC second (same file name) is refused; the raw evidence the first run built
its records on stays as it was. A run that starts in the second of an earlier
finished run is refused before it asks the API anything.
"""

import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from meterline_anthropic.cli import main
from meterline_anthropic.client import AnalyticsClient
from meterline_anthropic.fetch import RawFileExists, fetch_activity_day

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)


def _tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _run(api, clock, data_dir, *argv, now=NOW):
    return main([*argv, "--data-dir", str(data_dir)], transport=api.transport, clock=clock, sleep=clock.sleep,
                now=lambda: now, jitter=lambda: 0)


def test_raw_not_overwritten_a_second_fetch_in_the_same_second_is_refused(fake_api, fake_clock, tmp_path, fake_key_env):
    with AnalyticsClient(fake_key_env, transport=fake_api.transport, clock=fake_clock, sleep=fake_clock.sleep,
                         jitter=lambda: 0) as client:
        first = fetch_activity_day(client, tmp_path, date(2026, 10, 1), NOW, provider_account_id="org-synthetic-0001")
        before = first.path.read_bytes()
        fake_api.mutate(lambda ds: ds["activity"]["2026-10-01"].pop())  # the API now answers differently
        with pytest.raises(RawFileExists, match="never replaced"):
            fetch_activity_day(client, tmp_path, date(2026, 10, 1), NOW, provider_account_id="org-synthetic-0001")
    assert first.path.read_bytes() == before
    assert not list(tmp_path.rglob("*.tmp"))


def test_raw_not_overwritten_a_later_second_writes_a_new_file(fake_api, fake_clock, tmp_path, fake_key_env):
    with AnalyticsClient(fake_key_env, transport=fake_api.transport, clock=fake_clock, sleep=fake_clock.sleep,
                         jitter=lambda: 0) as client:
        first = fetch_activity_day(client, tmp_path, date(2026, 10, 1), NOW, provider_account_id="org-synthetic-0001")
        second = fetch_activity_day(client, tmp_path, date(2026, 10, 1), NOW + timedelta(seconds=1), provider_account_id="org-synthetic-0001")
    assert first.path != second.path and first.path.exists() and second.path.exists()


@pytest.mark.parametrize("command", [("backfill", "--since", "2026-10-01"), ("daily", "--since", "2026-10-01")])
def test_raw_not_overwritten_a_run_in_the_second_of_a_finished_run_is_refused(command, fake_api, fake_clock, tmp_path, capsys):
    assert _run(fake_api, fake_clock, tmp_path, *command) == 0
    before = _tree_hashes(tmp_path)
    seen = len(fake_api.log)
    capsys.readouterr()

    assert _run(fake_api, fake_clock, tmp_path, *command) == 2
    err = capsys.readouterr().err
    assert "same second" in err and "Traceback" not in err
    assert len(fake_api.log) == seen, "nothing is requested"
    assert _tree_hashes(tmp_path) == before, "raw files, records, manifest and state are as they were"

    assert _run(fake_api, fake_clock, tmp_path, *command, now=NOW + timedelta(seconds=1)) == 0


def test_raw_not_overwritten_rebuild_is_not_blocked(fake_api, fake_clock, tmp_path):
    assert _run(fake_api, fake_clock, tmp_path, "backfill", "--since", "2026-10-01") == 0
    assert main(["rebuild", "--data-dir", str(tmp_path)]) == 0
