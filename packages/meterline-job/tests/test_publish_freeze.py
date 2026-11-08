"""P1 and P2 of the publish spec: dated builds that are never overwritten, and months frozen by copy with checksums.

The builds here are synthetic gold folders (``publish_helpers.make_build``) so a test can set exactly what the freeze
rule reads: the months, the is_final column, the key. The connectors' ``complete_through`` is replaced by a table
(``DONE``); one test further down reads the real function of a real connector.
"""
import hashlib
import json
import os
import stat
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from meterline_job import publish
from meterline_job.config import ConfigError, parse_config
from publish_helpers import config, count_non_final, make_build, month_rows, read_csv

NOW = datetime(2026, 5, 4, 6, 0, tzinfo=timezone.utc)
APRIL_END, MAY_END = date(2026, 4, 30), date(2026, 5, 31)
DONE = {}


@pytest.fixture(autouse=True)
def connectors(monkeypatch):
    """Both connectors have final data through the end of April, unless a test changes ``DONE``."""
    DONE.clear()
    DONE.update(claude=APRIL_END, proxy=APRIL_END)
    monkeypatch.setattr(publish, "connector_complete_through", lambda spec, data_dir: DONE[spec["name"]])
    monkeypatch.setenv("METERLINE_GOLD_KEY", "a-key-of-at-least-32-bytes-that-you-keep-out-of-git")


def run(tmp_path, today=date(2026, 5, 4), complete=True, **settings):
    cfg = config(tmp_path, publish=settings) if settings else config(tmp_path)
    return publish.run_publish(cfg, publish.Context(today=today, now=lambda: NOW, run_complete=complete))


def frozen_json(tmp_path):
    return json.loads((tmp_path / "gold" / "frozen.json").read_text(encoding="utf-8"))


# ---- P1: builds -----------------------------------------------------------------------------------------------------


def test_the_first_build_of_a_day_is_the_date_and_later_ones_count_on(tmp_path):
    gold = tmp_path / "gold"
    names = []
    for _ in range(12):
        folder = publish.next_build(gold, date(2026, 5, 4))
        folder.mkdir(parents=True)
        names.append(folder.name)
    assert names[:3] == ["2026-05-04", "2026-05-04-2", "2026-05-04-3"]
    assert names[-1] == "2026-05-04-12"  # 10, 11 and 12 count as numbers, not as text
    assert publish.latest_build(gold).name == "2026-05-04-12"


def test_the_latest_build_is_the_highest_date_then_number(tmp_path):
    for name in ("2026-05-04-9", "2026-05-04-10", "2026-05-03-30", "2026-05-04", "notes", "2026-05-04.tmp"):
        (tmp_path / "gold" / "builds" / name).mkdir(parents=True)
    assert publish.latest_build(tmp_path / "gold").name == "2026-05-04-10"
    assert publish.latest_build(tmp_path / "empty") is None


def test_leftover_tmp_folders_of_builds_are_cleared(tmp_path):
    leftover = tmp_path / "gold" / "builds" / "2026-05-04.tmp"
    leftover.mkdir(parents=True)
    (leftover / "half.csv").write_text("x\n", encoding="utf-8")
    make_build(tmp_path / "gold", "2026-05-03")
    publish.clear_leftovers(tmp_path / "gold")
    assert not leftover.exists() and (tmp_path / "gold" / "builds" / "2026-05-03").is_dir()


# ---- P2: freezing ---------------------------------------------------------------------------------------------------


def test_a_complete_month_is_frozen_by_copy_on_the_fourth_day(tmp_path):
    build = make_build(tmp_path / "gold", "2026-05-04")
    result = run(tmp_path)
    assert result.exit_code == 0, result
    # both sources end on 30 April and the run is on 4 May: four days, one more than stale_after_days allows
    assert result.extra["frozen"] == ["2026-04"] and result.extra["warnings"] == ["stale_source claude", "stale_source proxy"]
    frozen = tmp_path / "gold" / "frozen" / "2026-04"
    assert sorted(p.name for p in (tmp_path / "gold" / "frozen").iterdir()) == ["2026-04"]
    # the frozen rows are the rows of the source build, compared as text, file by file
    assert month_rows(frozen, "2026-04") == month_rows(build, "2026-04")
    assert month_rows(frozen, "2026-04"), "nothing was frozen"
    assert not (frozen / "delivery").exists() and not list(frozen.rglob("*.parquet"))
    for path in frozen.rglob("*.csv"):  # every frozen file has a month column and only April
        header, rows = read_csv(path)
        assert "month" in header and {r[header.index("month")] for r in rows} <= {"2026-04"}
    assert not (frozen / "central" / "people.csv").exists() and not (frozen / "central" / "alert_events.csv").exists()
    assert (frozen / "manager" / "mk_a" / "groups_month.csv").is_file()
    assert (frozen / "person" / "pk_a" / "person_month.csv").is_file()


def test_the_checksum_file_lists_every_frozen_file(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    frozen = tmp_path / "gold" / "frozen" / "2026-04"
    listed = {}
    for line in (frozen / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, _, rel = line.partition("  ")
        listed[rel] = digest
    on_disk = {p.relative_to(frozen).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in frozen.rglob("*") if p.is_file() and p.name != "SHA256SUMS"}
    assert listed == on_disk and listed


def test_frozen_json_records_where_the_month_came_from(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04", key_check="kc_x")
    run(tmp_path)
    entry = frozen_json(tmp_path)["2026-04"]
    assert entry["source_build"] == "2026-05-04" and entry["key_check"] == "kc_x" and entry["forced"] is False
    assert entry["schema_version"] == "2.1" and entry["frozen_at"].startswith("2026-05-04T06:00:00")
    assert len(entry["policy_sha256"]) == 64 and entry["privacy"] == {"k": 5, "exec_max_depth": 2}
    assert "partial_start" not in entry or entry["partial_start"] is False


def test_nothing_is_frozen_before_the_wait_is_over(tmp_path):
    make_build(tmp_path / "gold", "2026-05-03")
    assert run(tmp_path, today=date(2026, 5, 3)).extra["frozen"] == []  # April ended 3 days ago, the wait is 4
    assert not (tmp_path / "gold" / "frozen").exists() or not list((tmp_path / "gold" / "frozen").iterdir())
    assert run(tmp_path, today=date(2026, 5, 3), freeze_after_days=3).extra["frozen"] == ["2026-04"]


def test_the_month_in_progress_is_never_frozen(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    DONE.update(claude=MAY_END, proxy=MAY_END)
    assert run(tmp_path, today=date(2026, 5, 31)).extra["frozen"] == ["2026-04"]  # May is still running
    assert run(tmp_path, today=date(2026, 6, 4)).extra["frozen"] == ["2026-05"]


def test_a_connector_that_is_behind_blocks_the_freeze(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    DONE["proxy"] = date(2026, 4, 29)
    assert run(tmp_path).extra["frozen"] == []
    DONE["proxy"] = None  # never backfilled
    assert run(tmp_path).extra["frozen"] == []
    DONE["proxy"] = APRIL_END
    assert run(tmp_path).extra["frozen"] == ["2026-04"]


def test_a_failing_complete_through_blocks_the_freeze_without_stopping_the_publish(tmp_path, monkeypatch):
    make_build(tmp_path / "gold", "2026-05-04")

    def broken(spec, data_dir):
        raise OSError("state file unreadable")

    monkeypatch.setattr(publish, "connector_complete_through", broken)
    result = run(tmp_path)
    assert result.exit_code == 0 and result.extra["frozen"] == []


def test_a_run_that_did_not_run_every_connector_does_not_freeze(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    result = run(tmp_path, complete=False)  # --step gold, --step publish, or a connector that failed
    assert result.exit_code == 0 and result.extra["frozen"] == [] and result.extra["warnings"] == ["stale_source claude", "stale_source proxy"]
    assert not (tmp_path / "gold" / "frozen").exists() or not list((tmp_path / "gold" / "frozen").iterdir())


@pytest.mark.parametrize("stem", ["person_day", "person_month", "pools_month", "groups_month"])
def test_a_row_that_may_still_change_blocks_the_freeze(tmp_path, stem):
    build = make_build(tmp_path / "gold", "2026-05-04")
    path = build / "central" / (stem + ".csv")
    header, rows = read_csv(path)
    rows[0][header.index("is_final")] = "false"  # one April row of one of the four datasets that carry the column
    from meterline_gold.write import write_csv

    write_csv(path, header, rows)
    assert count_non_final(build, "2026-04") == 1
    assert run(tmp_path).extra["frozen"] == []
    rows[0][header.index("is_final")] = "true"
    write_csv(path, header, rows)
    assert count_non_final(build, "2026-04") == 0
    assert run(tmp_path).extra["frozen"] == ["2026-04"]


def test_the_cap_forces_the_freeze_with_a_warning_and_exit_0(tmp_path):
    make_build(tmp_path / "gold", "2026-05-14")
    DONE["proxy"] = None  # the connector has been down since before the month ended
    assert run(tmp_path, today=date(2026, 5, 13)).extra["frozen"] == []  # 13 days after the end: one more day to wait
    result = run(tmp_path, today=date(2026, 5, 14))  # 4 + 10 = 14 days after the end of April
    assert result.exit_code == 0 and result.extra["frozen"] == ["2026-04"]
    assert result.extra["warnings"] == ["forced_freeze 2026-04", "stale_source claude"]  # the proxy has no answer: not stale by date
    assert frozen_json(tmp_path)["2026-04"]["forced"] is True


def test_the_cap_also_applies_to_a_run_of_one_step(tmp_path):
    make_build(tmp_path / "gold", "2026-05-14")
    result = run(tmp_path, today=date(2026, 5, 14), complete=False)
    assert result.extra["frozen"] == ["2026-04"] and result.extra["warnings"] == ["forced_freeze 2026-04"] + ["stale_source claude", "stale_source proxy"]


def test_the_cap_follows_the_settings(tmp_path):
    make_build(tmp_path / "gold", "2026-05-14")
    DONE["proxy"] = None
    assert run(tmp_path, today=date(2026, 5, 14), freeze_cap_extra_days=11).extra["frozen"] == []
    assert run(tmp_path, today=date(2026, 5, 15), freeze_cap_extra_days=11).extra["frozen"] == ["2026-04"]


def test_a_month_is_frozen_once_and_the_first_run_freezes_every_past_month(tmp_path):
    make_build(tmp_path / "gold", "2026-07-10", months=("2026-04", "2026-05", "2026-06", "2026-07"))
    DONE.update(claude=date(2026, 6, 30), proxy=date(2026, 6, 30))
    result = run(tmp_path, today=date(2026, 7, 10))
    assert result.extra["frozen"] == ["2026-04", "2026-05", "2026-06"]
    before = {p: p.read_bytes() for p in (tmp_path / "gold" / "frozen").rglob("*") if p.is_file()}
    again = run(tmp_path, today=date(2026, 7, 11))
    assert again.extra["frozen"] == []
    assert before == {p: p.read_bytes() for p in (tmp_path / "gold" / "frozen").rglob("*") if p.is_file()}


def test_the_first_month_of_a_late_start_is_marked_partial(tmp_path):
    cfg = config(tmp_path)
    cfg["connector"][0]["since"] = "2026-04-12"
    cfg["connector"][1]["since"] = "2026-04-20"
    make_build(tmp_path / "gold", "2026-05-04")
    publish.run_publish(cfg, publish.Context(today=date(2026, 5, 4), now=lambda: NOW, run_complete=True))
    assert frozen_json(tmp_path)["2026-04"]["partial_start"] is True


def test_a_month_folder_that_exists_without_an_entry_is_refused(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    (tmp_path / "gold" / "frozen" / "2026-04").mkdir(parents=True)
    result = run(tmp_path)
    assert result.exit_code == 4 and result.failure_kind == "publish"
    assert "frozen/2026-04" in result.error and "has no entry" in result.error and "cannot be recovered" in result.error
    assert not (tmp_path / "gold" / "frozen.json").exists()


def test_a_leftover_tmp_folder_of_a_frozen_month_is_cleared(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    leftover = tmp_path / "gold" / "frozen" / "2026-04.tmp"
    leftover.mkdir(parents=True)
    (leftover / "half.csv").write_text("x\n", encoding="utf-8")
    assert run(tmp_path).extra["frozen"] == ["2026-04"] and not leftover.exists()


def test_a_build_with_another_schema_than_the_frozen_rows_exits_4(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    entry = frozen_json(tmp_path)
    entry["2026-04"]["schema_version"] = "1.1"
    (tmp_path / "gold" / "frozen.json").write_text(json.dumps(entry), encoding="utf-8")
    make_build(tmp_path / "gold", "2026-05-05")
    result = run(tmp_path, today=date(2026, 5, 5))
    assert result.exit_code == 4 and "schema_version" in result.error and "1.1" in result.error


def test_frozen_files_are_private(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    old = os.umask(0o000)
    try:
        run(tmp_path)
    finally:
        os.umask(old)
    root = tmp_path / "gold"
    paths = [root / "frozen", root / "frozen.json", *(root / "frozen").rglob("*")]
    bad = ["%s %o" % (p.relative_to(root), stat.S_IMODE(p.stat().st_mode)) for p in paths
           if stat.S_IMODE(p.stat().st_mode) != (0o700 if p.is_dir() else 0o600)]
    assert not bad, bad


def test_publish_without_any_build_fails_with_exit_4(tmp_path):
    result = run(tmp_path)
    assert result.exit_code == 4 and "no build" in result.error


# ---- the setting [publish] -------------------------------------------------------------------------------------------

BASE = b'''
data_root = "d"
gold_root = "g"
[gold]
org = "o"
policy = "p"
prices = "q"
[[connector]]
name = "claude"
kind = "anthropic"
since = "2026-09-01"
'''


def test_the_settings_default_to_4_and_10():
    assert parse_config(BASE)["publish"] == {"freeze_after_days": 4, "freeze_cap_extra_days": 10, "stale_after_days": 3, "keep_builds": 2}
    cfg = parse_config(BASE + b"[publish]\nfreeze_after_days = 0\n")
    assert cfg["publish"] == {"freeze_after_days": 0, "freeze_cap_extra_days": 10, "stale_after_days": 3, "keep_builds": 2}


@pytest.mark.parametrize("table", [b"freeze_after_days = '4'", b"freeze_after_days = -1", b"freeze_cap_extra_days = 2.5",
                                   b"freeze_cap_extra_days = true", b"freeze_after = 4"])
def test_bad_publish_settings_are_input_errors(table):
    with pytest.raises(ConfigError) as err:
        parse_config(BASE + b"[publish]\n" + table + b"\n")
    assert "publish" in str(err.value)


def test_publish_is_a_reserved_step_name():
    with pytest.raises(ConfigError) as err:
        parse_config(BASE.replace(b'name = "claude"', b'name = "publish"'))
    assert "reserved" in str(err.value)


# ---- v0.3 hardening A-7: a forced freeze says so on stderr ---------------------------------------------------------------


def test_a_forced_freeze_prints_a_warning_line_on_stderr_and_still_exits_0(tmp_path, capsys):
    make_build(tmp_path / "gold", "2026-05-14")
    DONE["proxy"] = None
    result = run(tmp_path, today=date(2026, 5, 14))
    err = capsys.readouterr().err
    assert result.exit_code == 0 and "forced_freeze 2026-04" in result.extra["warnings"]
    assert "warning: forced_freeze 2026-04" in err.splitlines()
    assert "forced_freeze" not in capsys.readouterr().out
