"""P3 of the publish spec: the published folder, assembled under the lock and swapped in at once.

Synthetic builds again (``publish_helpers.make_build``). Rows are compared as text read with ``csv``, never with the
code under test.
"""
import hashlib
import io
import json
import os
import stat
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from meterline.run import file_lock
from meterline_gold import dataset_forms, servable, write
from meterline_job import publish
from meterline_job.job import run_job
from publish_helpers import config, make_build, month_rows, read_csv

NOW = datetime(2026, 5, 4, 6, 0, tzinfo=timezone.utc)
APRIL_END = date(2026, 4, 30)
DONE = {}


@pytest.fixture(autouse=True)
def connectors(monkeypatch):
    DONE.clear()
    DONE.update(claude=APRIL_END, proxy=APRIL_END)
    monkeypatch.setattr(publish, "connector_complete_through", lambda spec, data_dir: DONE[spec["name"]])
    monkeypatch.setenv("METERLINE_GOLD_KEY", "a-key-of-at-least-32-bytes-that-you-keep-out-of-git")


def run(tmp_path, today=date(2026, 5, 4), complete=True, minutes=0, **settings):
    cfg = config(tmp_path, publish=settings) if settings else config(tmp_path)
    clock = NOW + timedelta(minutes=minutes)
    return publish.run_publish(cfg, publish.Context(today=today, now=lambda: clock, run_complete=complete))


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


def published(tmp_path):
    return tmp_path / "gold" / "published"


def target(tmp_path):
    return os.readlink(published(tmp_path))


# ---- the folder ----------------------------------------------------------------------------------------------------


def test_a_first_publish_creates_the_link_and_the_folder_set(tmp_path):
    build = make_build(tmp_path / "gold", "2026-05-03")
    result = run(tmp_path, today=date(2026, 5, 3))
    assert result.exit_code == 0, result
    link = published(tmp_path)
    assert link.is_symlink() and target(tmp_path).startswith("published-2026")
    assert not os.path.isabs(target(tmp_path)) and not (tmp_path / "gold" / "published.new").exists()
    assert sorted(p.name for p in link.iterdir()) == ["central", "exec", "manager", "manifest.json", "person"]
    assert sorted(p.name for p in (link / "manager").iterdir()) == ["mk_a"]
    assert sorted(p.name for p in (link / "person").iterdir()) == ["pk_a", "pk_b"]
    assert not (link / "delivery").exists() and not list(link.rglob("*directory*"))
    # nothing is frozen yet: every file is the file of the latest build, byte for byte
    expected = {rel: data for rel, data in files(build).items() if not rel.startswith("delivery/")}
    served = files(link)
    added = sorted(rel for rel in served if rel not in expected)  # the freshness files (P5), and nothing else
    assert added == ["central/published.json", "exec/freshness.json", "manager/mk_a/freshness.json",
                     "person/pk_a/freshness.json", "person/pk_b/freshness.json"]
    assert {rel: data for rel, data in served.items() if rel not in added} == expected


def test_the_folder_set_is_the_latest_builds_even_for_empty_folders(tmp_path):
    build = make_build(tmp_path / "gold", "2026-05-03", people=("pk_a",), managers=("mk_a", "mk_empty"))
    for name in ("groups_month", "named_exceptions"):  # a manager with no rows at all keeps a folder with headers only
        path = build / "manager" / "mk_empty" / (name + ".csv")
        header, _ = read_csv(path)
        write.write_csv(path, header, [])
    run(tmp_path, today=date(2026, 5, 3))
    folder = published(tmp_path) / "manager" / "mk_empty"
    assert sorted(p.name for p in folder.iterdir()) == ["freshness.json", "groups_month.csv", "named_exceptions.csv"]
    assert read_csv(folder / "groups_month.csv")[1] == []


def test_a_frozen_month_is_served_from_the_freeze_not_from_the_latest_build(tmp_path):
    first = make_build(tmp_path / "gold", "2026-05-04")
    assert run(tmp_path).extra["frozen"] == ["2026-04"]
    april = month_rows(first, "2026-04")
    late = make_build(tmp_path / "gold", "2026-05-05", bumps={"2026-04": 7, "2026-05": 3})  # a late correction
    assert month_rows(late, "2026-04") != april
    assert run(tmp_path, today=date(2026, 5, 5), minutes=60).extra["frozen"] == []
    served = published(tmp_path)
    assert month_rows(served, "2026-04") == april  # the first build's April, not the corrected one
    assert month_rows(served, "2026-05") == month_rows(late, "2026-05")  # the month in progress follows the latest build
    assert month_rows(tmp_path / "gold" / "builds" / "2026-05-05", "2026-04") != month_rows(served, "2026-04")


def test_a_connector_that_lags_keeps_the_month_open_though_the_build_looks_complete(tmp_path):
    """Done-check 1: gold's data_through passed the end of April, but one source has not final data yet; the correction
    that arrives later must still reach the published rows of April."""
    make_build(tmp_path / "gold", "2026-05-04", data_through="2026-05-02")
    DONE["proxy"] = date(2026, 4, 29)
    assert run(tmp_path).extra["frozen"] == []
    late = make_build(tmp_path / "gold", "2026-05-05", bumps={"2026-04": 9}, data_through="2026-05-03")
    assert run(tmp_path, today=date(2026, 5, 5), minutes=60).extra["frozen"] == []
    assert month_rows(published(tmp_path), "2026-04") == month_rows(late, "2026-04")
    DONE["proxy"] = APRIL_END  # the source catches up; from the next complete run the month is frozen as it then is
    assert run(tmp_path, today=date(2026, 5, 6), minutes=120).extra["frozen"] == ["2026-04"]
    corrected_later = make_build(tmp_path / "gold", "2026-05-07", bumps={"2026-04": 40})
    run(tmp_path, today=date(2026, 5, 7), minutes=180)
    assert month_rows(corrected_later, "2026-04") != month_rows(published(tmp_path), "2026-04")
    assert month_rows(published(tmp_path), "2026-04") == month_rows(late, "2026-04")


def test_before_the_freeze_the_month_follows_the_latest_build(tmp_path):
    make_build(tmp_path / "gold", "2026-05-03")
    run(tmp_path, today=date(2026, 5, 3))
    late = make_build(tmp_path / "gold", "2026-05-03-2", bumps={"2026-04": 7})
    assert run(tmp_path, today=date(2026, 5, 3), minutes=5).extra["frozen"] == []
    assert month_rows(published(tmp_path), "2026-04") == month_rows(late, "2026-04")


def test_a_composed_file_is_sorted_by_its_key_and_keeps_gold_s_header(tmp_path):
    first = make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    make_build(tmp_path / "gold", "2026-05-05", bumps={"2026-05": 1})
    run(tmp_path, today=date(2026, 5, 5), minutes=60)
    forms = dataset_forms()
    checked = 0
    for path in sorted(published(tmp_path).rglob("*.csv")):
        rel = path.relative_to(published(tmp_path))
        if rel.parts[0] not in ("central", "exec", "manager", "person"):
            continue
        header, rows = read_csv(path)
        want_header, _ = read_csv(tmp_path / "gold" / "builds" / "2026-05-05" / rel)
        assert header == want_header, rel
        if "month" not in header:
            continue
        stem = path.stem
        keys = [header.index(c) for c in forms[(rel.parts[0], stem)][1]]
        assert rows == sorted(rows, key=lambda r: (tuple(r[i] for i in keys), r)), rel  # an empty cell sorts first as ""
        checked += 1
    assert checked > 20 and {r[0] for r in read_csv(published(tmp_path) / "central" / "person_month.csv")[1]} == {"2.1"}
    # the rows of April are the frozen ones, the file has both months
    months = {r[1] for r in read_csv(published(tmp_path) / "central" / "person_month.csv")[1]}
    assert months == {"2026-04", "2026-05"} and month_rows(first, "2026-04")


def test_files_without_a_month_come_from_the_latest_build(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    late = make_build(tmp_path / "gold", "2026-05-05")
    people = late / "central" / "people.csv"
    header, rows = read_csv(people)
    rows[0][header.index("function")] = "changed-after-the-freeze"
    write.write_csv(people, header, rows)
    run(tmp_path, today=date(2026, 5, 5), minutes=60)
    assert (published(tmp_path) / "central" / "people.csv").read_bytes() == people.read_bytes()
    assert (published(tmp_path) / "manifest.json").read_bytes() == (late / "manifest.json").read_bytes()


def test_every_file_of_a_reader_folder_has_a_month_column():
    for (audience, dataset), (schema_name, _keys) in dataset_forms().items():
        if audience != "central":
            assert "month" in [c for c, _ in write.SCHEMAS[schema_name]], (audience, dataset)


# ---- Parquet ----------------------------------------------------------------------------------------------------------


def test_parquet_twins_follow_the_latest_build_and_keep_gold_s_types(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    make_build(tmp_path / "gold", "2026-05-04", parquet=True)
    run(tmp_path)
    late = make_build(tmp_path / "gold", "2026-05-05", parquet=True, bumps={"2026-05": 2})
    run(tmp_path, today=date(2026, 5, 5), minutes=60)
    served = published(tmp_path)
    twins = sorted(p.relative_to(served).as_posix() for p in served.rglob("*.parquet"))
    wanted = sorted(p.relative_to(late).as_posix() for p in late.rglob("*.parquet") if not p.relative_to(late).parts[0] == "delivery")
    assert twins == wanted and twins
    for rel in twins:
        table, reference = pq.read_table(served / rel), pq.read_table(late / rel)
        assert table.schema.equals(reference.schema), rel  # the same column types as gold wrote
        csv_header, csv_rows = read_csv((served / rel).with_suffix(".csv"))
        assert table.num_rows == len(csv_rows) and table.column_names == csv_header, rel
    # the composed file's twin holds the frozen April row and the corrected May row
    composed = pq.read_table(served / "central" / "person_month.parquet").to_pylist()
    assert sorted({r["month"] for r in composed}) == ["2026-04", "2026-05"]
    cost = {(r["month"], r["person_key"]): str(r["cost_ref"]) for r in composed}
    assert cost[("2026-04", "pk_a")].startswith("10.5") and cost[("2026-05", "pk_a")].startswith("12.5")


def test_no_parquet_is_written_when_the_latest_build_has_none(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04", parquet=False)
    run(tmp_path)
    assert not list(published(tmp_path).rglob("*.parquet"))


# ---- the checks and the swap -----------------------------------------------------------------------------------------


def frozen_april(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    assert run(tmp_path).extra["frozen"] == ["2026-04"]
    return tmp_path / "gold" / "frozen" / "2026-04"


@pytest.mark.parametrize("damage", ["flip", "delete", "add", "no_sums"])
def test_a_damaged_frozen_month_exits_4_and_leaves_published_alone(tmp_path, damage):
    frozen = frozen_april(tmp_path)
    before = target(tmp_path)
    served = files(published(tmp_path))
    victim = frozen / "central" / "person_month.csv"
    if damage == "flip":
        data = bytearray(victim.read_bytes())
        data[-3] ^= 1
        victim.write_bytes(bytes(data))
    elif damage == "delete":
        victim.unlink()
    elif damage == "add":
        (frozen / "central" / "extra.csv").write_text("month\n2026-04\n", encoding="utf-8")
    else:
        (frozen / "SHA256SUMS").unlink()
    make_build(tmp_path / "gold", "2026-05-05")
    result = run(tmp_path, today=date(2026, 5, 5), minutes=60)
    assert result.exit_code == 4 and result.failure_kind == "publish" and "frozen/2026-04" in result.error, result
    assert target(tmp_path) == before and files(published(tmp_path)) == served
    assert sorted(p.name for p in (tmp_path / "gold").glob("published-*")) == [before]


def test_a_failing_swap_leaves_published_on_the_previous_folder(tmp_path, monkeypatch):
    frozen_april(tmp_path)
    before = target(tmp_path)
    real = os.replace

    def refuse(src, dst, *a, **kw):
        if str(dst).endswith("/published"):
            raise OSError("disk on fire")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", refuse)
    make_build(tmp_path / "gold", "2026-05-05")
    result = run(tmp_path, today=date(2026, 5, 5), minutes=60)
    assert result.exit_code == 4 and "disk on fire" in result.error
    assert target(tmp_path) == before and published(tmp_path).is_dir()
    assert not (tmp_path / "gold" / "published.new").exists()
    assert sorted(p.name for p in (tmp_path / "gold").glob("published-*")) == [before]  # the half-made folder is gone


def test_a09_blocks_the_swap_when_an_identifier_reaches_a_text_column(tmp_path):
    frozen_april(tmp_path)
    before = target(tmp_path)
    org = tmp_path / "org"
    (org / "people.csv").write_text("person_id,manager_id\nfunction-1,\n", encoding="utf-8")  # looks like a value of the files
    make_build(tmp_path / "gold", "2026-05-05")
    result = run(tmp_path, today=date(2026, 5, 5), minutes=60)
    assert result.exit_code == 4 and "A09" in result.error
    assert target(tmp_path) == before


def test_the_permission_check_names_a_loose_file_a_loose_folder_and_a_link(tmp_path):
    folder = tmp_path / "f"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "a.csv").write_text("x\n", encoding="utf-8")
    os.chmod(folder, 0o700)
    os.chmod(folder / "sub", 0o700)
    os.chmod(folder / "sub" / "a.csv", 0o600)
    assert publish.private_violations(folder) == []
    os.chmod(folder / "sub" / "a.csv", 0o640)
    os.chmod(folder / "sub", 0o750)
    (folder / "link").symlink_to("sub")
    bad = publish.private_violations(folder)
    assert any("a.csv" in b for b in bad) and any(b.startswith("sub ") for b in bad) and any("link" in b for b in bad)


def test_published_files_and_folders_are_private_under_any_umask(tmp_path):
    frozen_april(tmp_path)
    old = os.umask(0o000)
    try:
        make_build(tmp_path / "gold", "2026-05-05")
        os.chmod(tmp_path / "gold" / "builds" / "2026-05-05", 0o700)
        assert run(tmp_path, today=date(2026, 5, 5), minutes=60).exit_code == 0
    finally:
        os.umask(old)
    folder = tmp_path / "gold" / target(tmp_path)
    paths = [folder, *folder.rglob("*")]
    bad = ["%s %o" % (p.relative_to(folder), stat.S_IMODE(p.lstat().st_mode)) for p in paths
           if stat.S_IMODE(p.lstat().st_mode) != (0o700 if p.is_dir() else 0o600)]
    assert not bad, bad[:5]


def test_publish_is_for_posix_only(tmp_path, monkeypatch):
    make_build(tmp_path / "gold", "2026-05-03")
    monkeypatch.setattr(publish, "is_posix", lambda: False)
    result = run(tmp_path, today=date(2026, 5, 3))
    assert result.exit_code == 2 and result.failure_kind == "input" and "POSIX" in result.error
    assert not published(tmp_path).exists()


# ---- what stays: the previous folder, an orphan, the lock ---------------------------------------------------------------


def test_only_the_current_and_previous_published_folders_stay(tmp_path):
    make_build(tmp_path / "gold", "2026-05-03")
    names = []
    for minutes in range(4):
        assert run(tmp_path, today=date(2026, 5, 3), minutes=minutes).exit_code == 0
        names.append(target(tmp_path))
        (tmp_path / "gold" / "published-20200101T000000000000").mkdir(exist_ok=True)  # an orphan of a killed run
    stay = sorted(p.name for p in (tmp_path / "gold").glob("published-*"))
    assert len(set(names)) == 4
    assert stay == sorted([names[-2], names[-1], "published-20200101T000000000000"])  # the orphan was made after the last run
    run(tmp_path, today=date(2026, 5, 3), minutes=9)
    assert sorted(p.name for p in (tmp_path / "gold").glob("published-*")) == sorted([names[-1], target(tmp_path)])  # orphan gone
    assert published(tmp_path).is_dir() and (published(tmp_path) / "central").is_dir()


def test_a_clash_of_timestamps_gets_a_number(tmp_path):
    make_build(tmp_path / "gold", "2026-05-03")
    run(tmp_path, today=date(2026, 5, 3))
    first = target(tmp_path)
    run(tmp_path, today=date(2026, 5, 3))  # the same clock reading
    second = target(tmp_path)
    run(tmp_path, today=date(2026, 5, 3))
    assert second == first + "-2" and target(tmp_path) == first + "-3"


def test_a_publish_of_one_step_stops_on_a_held_lock(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    cfg = config(tmp_path)
    with file_lock(tmp_path / "gold" / ".lock", "gold folder") as held:
        assert held is True
        out = io.StringIO()
        code = run_job(cfg, step="publish", now=lambda: NOW, out=out, today=date(2026, 5, 4))
    line = json.loads(out.getvalue().splitlines()[0])
    assert code == 1 and (line["step"], line["exit_code"], line["failure_kind"]) == ("publish", 1, "transient")
    assert not (tmp_path / "gold" / "frozen").exists() and not published(tmp_path).exists()


def test_two_publishes_at_once_the_second_exits_1_and_the_month_is_frozen_once(tmp_path, monkeypatch):
    make_build(tmp_path / "gold", "2026-05-04")
    cfg = config(tmp_path)
    inside, release = threading.Event(), threading.Event()
    real = publish._freeze_one

    def slow(*args, **kw):
        inside.set()
        assert release.wait(30)
        return real(*args, **kw)

    monkeypatch.setattr(publish, "_freeze_one", slow)
    results = {}

    def first():
        out = io.StringIO()
        results["first"] = (run_job(cfg, step="publish", now=lambda: NOW, out=out, today=date(2026, 5, 4)), out.getvalue())

    # the first run is not a complete run, so it freezes nothing on the normal rule: make it a cap freeze
    cfg["publish"] = {"freeze_after_days": 0, "freeze_cap_extra_days": 0}
    thread = threading.Thread(target=first)
    thread.start()
    assert inside.wait(30)
    out = io.StringIO()
    second = run_job(cfg, step="publish", now=lambda: NOW, out=out, today=date(2026, 5, 4))
    release.set()
    thread.join(30)
    assert second == 1 and json.loads(out.getvalue().splitlines()[0])["failure_kind"] == "transient"
    assert results["first"][0] == 0
    assert sorted(p.name for p in (tmp_path / "gold" / "frozen").iterdir()) == ["2026-04"]
    assert list(json.loads((tmp_path / "gold" / "frozen.json").read_text(encoding="utf-8"))) == ["2026-04"]


# ---- what a reader may be served ---------------------------------------------------------------------------------------


def test_every_published_file_is_servable_to_its_own_audience_and_to_nobody_else(tmp_path):
    frozen_april(tmp_path)
    make_build(tmp_path / "gold", "2026-05-05")
    run(tmp_path, today=date(2026, 5, 5), minutes=60)
    served = tmp_path / "gold" / target(tmp_path)
    assert not any(p.name == "delivery" for p in served.rglob("*"))
    audiences = ("central", "exec", "manager", "person")
    count = 0
    for path in served.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(served).as_posix()
        first = rel.split("/")[0]
        own = "central" if first == "manifest.json" else first
        key = rel.split("/")[1] if first in ("manager", "person") else None
        assert servable(rel, own + (":" + key if key else "")), rel
        assert [a for a in audiences if a != own and servable(rel, a)] == [], rel
        count += 1
    assert count > 30
    # the build and frozen paths are central-only material outside the served root: no rule reaches them
    for rel in ("builds/2026-05-05/central/person_month.csv", "frozen/2026-04/central/person_month.csv", "frozen.json",
                "builds/2026-05-05/manifest.json", "delivery/directory.csv"):
        assert not any(servable(rel, a) for a in audiences), rel


def test_the_published_folder_never_holds_delivery_even_if_the_build_does(tmp_path):
    make_build(tmp_path / "gold", "2026-05-03")
    run(tmp_path, today=date(2026, 5, 3))
    assert (tmp_path / "gold" / "builds" / "2026-05-03" / "delivery" / "directory.csv").is_file()
    assert not list((tmp_path / "gold").glob("published-*/delivery"))
    digest = hashlib.sha256((tmp_path / "gold" / "builds" / "2026-05-03" / "delivery" / "directory.csv").read_bytes()).hexdigest()
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() != digest for p in published(tmp_path).rglob("*") if p.is_file())


# ---- v0.3 hardening A-12: publish refuses links planted in a build -------------------------------------------------------


def test_a_link_planted_in_the_build_makes_publish_exit_4_and_keeps_published(tmp_path):
    make_build(tmp_path / "gold", "2026-05-03")
    assert run(tmp_path, today=date(2026, 5, 3)).exit_code == 0
    before = target(tmp_path)
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\n", encoding="utf-8")
    build = make_build(tmp_path / "gold", "2026-05-04")
    (build / "central" / "planted.csv").symlink_to(outside)
    result = run(tmp_path, today=date(2026, 5, 4), minutes=5)
    assert result.exit_code == 4 and result.failure_kind == "publish", result
    assert "builds/2026-05-04/central/planted.csv" in result.error and "symbolic link" in result.error
    assert target(tmp_path) == before and not [p for p in (tmp_path / "gold").glob("published-*") if p.name != before]


def test_a_linked_folder_in_the_build_is_named_and_not_entered(tmp_path):
    build = make_build(tmp_path / "gold", "2026-05-04")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.csv").write_text("x\n", encoding="utf-8")
    (build / "exec" / "more").symlink_to(elsewhere, target_is_directory=True)
    result = run(tmp_path)
    assert result.exit_code == 4 and "builds/2026-05-04/exec/more" in result.error


def test_a_link_planted_in_a_frozen_month_makes_publish_exit_4(tmp_path):
    frozen = frozen_april(tmp_path)
    before = target(tmp_path)
    (frozen / "central" / "planted.csv").symlink_to(tmp_path / "outside.csv")
    make_build(tmp_path / "gold", "2026-05-05")
    result = run(tmp_path, today=date(2026, 5, 5), minutes=60)
    assert result.exit_code == 4 and "frozen/2026-04/central/planted.csv" in result.error
    assert target(tmp_path) == before


# ---- v0.3 hardening A-13: old builds are pruned ----------------------------------------------------------------------------


def test_five_daily_runs_keep_two_builds_and_only_the_newest_holds_delivery(tmp_path):
    for day in range(4, 9):
        make_build(tmp_path / "gold", "2026-05-%02d" % day)
        result = run(tmp_path, today=date(2026, 5, day), minutes=day)
        assert result.exit_code == 0, result
    builds = sorted(p.name for p in (tmp_path / "gold" / "builds").iterdir())
    assert builds == ["2026-05-07", "2026-05-08"]
    assert (tmp_path / "gold" / "builds" / "2026-05-08" / "delivery" / "directory.csv").is_file()
    assert not (tmp_path / "gold" / "builds" / "2026-05-07" / "delivery").exists()
    assert (tmp_path / "gold" / "builds" / "2026-05-07" / "central").is_dir()  # the rest of a kept build stays
    assert result.extra["pruned"] == ["2026-05-06"]
    frozen = tmp_path / "gold" / "frozen" / "2026-04"
    publish.verify_sums(frozen)  # frozen months do not depend on a build: untouched and still verifying
    assert json.loads((tmp_path / "gold" / "frozen.json").read_text(encoding="utf-8"))["2026-04"]["source_build"] == "2026-05-04"


def test_keep_builds_follows_the_setting_and_never_goes_below_one(tmp_path):
    from meterline_job.config import ConfigError, parse_config

    for day in range(4, 8):
        make_build(tmp_path / "gold", "2026-05-%02d" % day)
        assert run(tmp_path, today=date(2026, 5, day), minutes=day, keep_builds=3).exit_code == 0
    assert sorted(p.name for p in (tmp_path / "gold" / "builds").iterdir()) == ["2026-05-05", "2026-05-06", "2026-05-07"]
    base = (b'data_root = "d"\ngold_root = "g"\n[gold]\norg = "o"\npolicy = "p"\nprices = "x"\n'
            b'[[connector]]\nname = "c"\nkind = "anthropic"\nsince = "2026-01-01"\n')
    assert parse_config(base)["publish"]["keep_builds"] == 2
    with pytest.raises(ConfigError, match="keep_builds must be a whole number, 1 or more"):
        parse_config(base + b"[publish]\nkeep_builds = 0\n")


def test_the_build_published_was_assembled_from_is_never_pruned(tmp_path):
    older = make_build(tmp_path / "gold", "2026-05-03")
    newer = make_build(tmp_path / "gold", "2026-05-04")
    assert publish.prune_builds(tmp_path / "gold", 1, older) == []  # the build named as the source stays, whatever its age
    assert older.is_dir() and newer.is_dir()
