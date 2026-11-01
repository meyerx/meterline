"""F-26: a pull never replaces an earlier raw file. A second pull in the same
UTC second (same file name) is refused; the raw evidence the first run built
its records on stays as it was. A run that starts in the second of an earlier
finished run is refused before it asks the proxy anything.
"""

import hashlib
from datetime import date, timedelta
from pathlib import Path

import pytest

from litellm_helpers import NOW, backfill, basic_rows, daily, run, users
from meterline_litellm.client import LitellmClient
from meterline_litellm.fetch import RawFileExists, fetch_activity


def _tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _client(proxy, clock):
    return LitellmClient("sk-synthetic-not-a-real-key", base_url="https://proxy.example.com", transport=proxy.transport,
                         clock=clock, sleep=clock.sleep, jitter=lambda: 0)


def test_raw_not_overwritten_a_second_fetch_in_the_same_second_is_refused(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    kw = dict(customer=False, first=date(2026, 9, 14), last=date(2026, 9, 19), entity=None, pulled_at=NOW, proxy_name="p")
    with _client(proxy, fake_clock) as client:
        first = fetch_activity(client, data_dir, **kw)
        before = first.path.read_bytes()
        proxy.rows.pop()  # the proxy now answers differently
        with pytest.raises(RawFileExists, match="never replaced"):
            fetch_activity(client, data_dir, **kw)
    assert first.path.read_bytes() == before
    assert not list(data_dir.rglob("*.tmp"))


def test_raw_not_overwritten_a_later_second_writes_a_new_file(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    kw = dict(customer=False, first=date(2026, 9, 14), last=date(2026, 9, 19), entity=None, proxy_name="p")
    with _client(proxy, fake_clock) as client:
        first = fetch_activity(client, data_dir, pulled_at=NOW, **kw)
        second = fetch_activity(client, data_dir, pulled_at=NOW + timedelta(seconds=1), **kw)
    assert first.path != second.path and first.path.exists() and second.path.exists()


@pytest.mark.parametrize("command", ["backfill", "daily"])
def test_raw_not_overwritten_a_run_in_the_second_of_a_finished_run_is_refused(command, make_proxy, fake_clock, data_dir, capsys):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))

    def go(now):
        return backfill(data_dir, proxy, fake_clock, now=now) if command == "backfill" else daily(
            data_dir, proxy, fake_clock, now, "--since", "2026-09-14")

    assert go(NOW) == 0
    before = _tree_hashes(data_dir)
    seen = len(proxy.log)
    capsys.readouterr()

    assert go(NOW) == 2
    err = capsys.readouterr().err
    assert "same second" in err and "Traceback" not in err
    assert len(proxy.log) == seen, "nothing is requested"
    assert _tree_hashes(data_dir) == before, "raw files, records, manifest and state are as they were"

    assert go(NOW + timedelta(seconds=1)) == 0


def test_raw_not_overwritten_rebuild_is_not_blocked(make_proxy, fake_clock, data_dir):
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    assert backfill(data_dir, proxy, fake_clock) == 0
    assert run(["rebuild", "--data-dir", str(data_dir)], proxy, fake_clock) == 0
