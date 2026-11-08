"""P4 and P5 of the publish spec: a change of the hashing key, people who left, the rule that erasure relies on, and the
two freshness files. Synthetic builds (``publish_helpers.make_build``); everything is read back with ``csv`` and ``json``.
"""
import csv
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from meterline_gold import policy_privacy, servable
from meterline_job import publish, schemas
from publish_helpers import POLICY, config, make_build, month_rows, read_csv

NOW = datetime(2026, 5, 4, 6, 0, tzinfo=timezone.utc)
APRIL_END = date(2026, 4, 30)
DONE = {}


@pytest.fixture(autouse=True)
def connectors(monkeypatch):
    DONE.clear()
    DONE.update(claude=APRIL_END, proxy=APRIL_END)
    monkeypatch.setattr(publish, "connector_complete_through", lambda spec, data_dir: DONE[spec["name"]])


def run(tmp_path, today=date(2026, 5, 4), complete=True, minutes=0, cfg=None):
    cfg = cfg or config(tmp_path)
    clock = NOW + timedelta(minutes=minutes)
    return publish.run_publish(cfg, publish.Context(today=today, now=lambda: clock, run_complete=complete))


def served(tmp_path):
    return tmp_path / "gold" / "published"


def all_cells(root):
    cells = set()
    for path in Path(root).rglob("*.csv"):
        for row in csv.reader(open(path, encoding="utf-8", newline="")):  # noqa: SIM115 - short test helper
            cells.update(row)
    return cells


def keyed_months(root, kind):
    """{month} of every row of every file under ``root/<kind>/``."""
    months = set()
    for path in (Path(root) / kind).rglob("*.csv"):
        header, rows = read_csv(path)
        months.update(r[header.index("month")] for r in rows)
    return months


def key_change_scenario(tmp_path):
    """April frozen under kc_one, then a build under kc_two with other person and manager keys."""
    first = make_build(tmp_path / "gold", "2026-05-04", key_check="kc_one", people=("pk_a", "pk_b"), managers=("mk_a",))
    assert run(tmp_path).extra["frozen"] == ["2026-04"]
    second = make_build(tmp_path / "gold", "2026-05-05", key_check="kc_two", people=("pk_c", "pk_d"), managers=("mk_b",))
    result = run(tmp_path, today=date(2026, 5, 5), minutes=60)
    assert result.exit_code == 0, result
    return first, second


# ---- P4: a change of the hashing key ----------------------------------------------------------------------------------


def test_after_a_key_change_manager_and_person_folders_hold_only_months_under_the_new_key(tmp_path):
    first, second = key_change_scenario(tmp_path)
    root = served(tmp_path)
    assert sorted(p.name for p in (root / "person").iterdir()) == ["pk_c", "pk_d"]
    assert sorted(p.name for p in (root / "manager").iterdir()) == ["mk_b"]
    assert keyed_months(root, "person") == {"2026-05"} and keyed_months(root, "manager") == {"2026-05"}
    old_cells = all_cells(first / "person") | all_cells(first / "manager")
    assert {"pk_a", "pk_b", "mk_a"} <= old_cells
    leaked = (all_cells(root / "person") | all_cells(root / "manager")) & {"pk_a", "pk_b", "mk_a", "kc_one"}
    assert not leaked, "an old-key value reached a manager or person folder: %s" % sorted(leaked)


def test_after_a_key_change_the_exec_rows_of_the_frozen_month_are_kept_as_frozen(tmp_path):
    first, second = key_change_scenario(tmp_path)
    exec_rows = month_rows(served(tmp_path) / "exec", "2026-04")
    assert exec_rows and exec_rows == month_rows(first / "exec", "2026-04")
    assert month_rows(served(tmp_path) / "exec", "2026-05") == month_rows(second / "exec", "2026-05")


def test_after_a_key_change_central_keeps_the_old_key_rows_beside_the_new_ones(tmp_path):
    first, second = key_change_scenario(tmp_path)
    header, rows = read_csv(served(tmp_path) / "central" / "person_month.csv")
    keys = {(r[header.index("month")], r[header.index("person_key")]) for r in rows}
    assert keys == {("2026-04", "pk_a"), ("2026-04", "pk_b"), ("2026-05", "pk_c"), ("2026-05", "pk_d")}
    assert json.loads((served(tmp_path) / "central" / "published.json").read_text(encoding="utf-8"))["key_rotations"] == ["2026-04"]


def test_a_month_frozen_under_the_new_key_reaches_the_keyed_folders(tmp_path):
    key_change_scenario(tmp_path)
    DONE.update(claude=date(2026, 5, 31), proxy=date(2026, 5, 31))
    make_build(tmp_path / "gold", "2026-06-05", months=("2026-04", "2026-05", "2026-06"), key_check="kc_two",
               people=("pk_c", "pk_d"), managers=("mk_b",))
    result = run(tmp_path, today=date(2026, 6, 5), minutes=120)
    assert result.extra["frozen"] == ["2026-05"]
    assert keyed_months(served(tmp_path), "person") == {"2026-05", "2026-06"}  # April stays out, May is frozen under kc_two
    assert keyed_months(served(tmp_path), "exec") == {"2026-04", "2026-05", "2026-06"}


def test_without_a_key_change_the_keyed_folders_hold_the_frozen_months(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    make_build(tmp_path / "gold", "2026-05-05")
    run(tmp_path, today=date(2026, 5, 5), minutes=60)
    assert keyed_months(served(tmp_path), "person") == {"2026-04", "2026-05"}
    assert json.loads((served(tmp_path) / "central" / "published.json").read_text(encoding="utf-8"))["key_rotations"] == []


# ---- P4: people who left ----------------------------------------------------------------------------------------------


def test_a_person_who_left_and_a_former_manager_keep_no_published_folder_but_stay_in_central(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04", people=("pk_a", "pk_b"), managers=("mk_a", "mk_b"))
    run(tmp_path)
    make_build(tmp_path / "gold", "2026-05-05", people=("pk_a",), managers=("mk_a",))
    assert run(tmp_path, today=date(2026, 5, 5), minutes=60).exit_code == 0
    root = served(tmp_path)
    assert sorted(p.name for p in (root / "person").iterdir()) == ["pk_a"]
    assert sorted(p.name for p in (root / "manager").iterdir()) == ["mk_a"]
    header, rows = read_csv(root / "central" / "person_month.csv")
    april_keys = {r[header.index("person_key")] for r in rows if r[header.index("month")] == "2026-04"}
    assert april_keys == {"pk_a", "pk_b"}  # the history of the person who left stays central
    assert (tmp_path / "gold" / "frozen" / "2026-04" / "person" / "pk_b" / "person_month.csv").is_file()  # and in frozen/


# ---- P4: the rule erasure relies on (the tool is a later change) ------------------------------------------------------


def test_the_manual_erasure_procedure_leaves_no_trace_and_exec_untouched(tmp_path):
    first = make_build(tmp_path / "gold", "2026-05-04", people=("pk_a", "pk_b"), managers=("mk_a",))
    run(tmp_path)
    run(tmp_path, today=date(2026, 5, 5), minutes=30)
    exec_before = {p.name: p.read_bytes() for p in (served(tmp_path) / "exec").glob("*.csv")}
    # 1. the person leaves the inputs: the next build has no pk_b; 2. frozen/ is cleaned by hand, checksums rewritten,
    # the erasure noted; 3. every builds/ folder is deleted; the next run builds again
    gold = tmp_path / "gold"
    frozen = gold / "frozen" / "2026-04"
    shutil.rmtree(frozen / "person" / "pk_b")
    for path in frozen.rglob("*.csv"):
        header, rows = read_csv(path)
        if "person_key" in header:
            at = header.index("person_key")
            from meterline_gold.write import write_csv

            write_csv(path, header, [r for r in rows if r[at] != "pk_b"])
    publish.write_sums(frozen)
    entries = json.loads((gold / "frozen.json").read_text(encoding="utf-8"))
    entries["2026-04"]["erasures"] = [{"erased_at": "2026-05-06T06:00:00+00:00"}]
    (gold / "frozen.json").write_text(json.dumps(entries), encoding="utf-8")
    shutil.rmtree(gold / "builds")
    make_build(gold, "2026-05-06", people=("pk_a",), managers=("mk_a",))
    result = run(tmp_path, today=date(2026, 5, 6), minutes=60)
    assert result.exit_code == 0, result
    assert "pk_b" not in all_cells(served(tmp_path)) and not list(served(tmp_path).rglob("pk_b"))
    assert {p.name: p.read_bytes() for p in (served(tmp_path) / "exec").glob("*.csv")} == exec_before
    assert first.exists() is False


# ---- P4: a change of privacy parameters --------------------------------------------------------------------------------


def policy_with_k(tmp_path, k):
    path = tmp_path / ("policy-k%d.yaml" % k)
    path.write_text(POLICY.read_text(encoding="utf-8").replace("  k: 5", "  k: %d" % k), encoding="utf-8")
    return path


def test_a_policy_change_adds_a_warning_and_publishing_goes_ahead(tmp_path):
    five, six = policy_with_k(tmp_path, 5), policy_with_k(tmp_path, 6)
    assert policy_privacy(five)[1]["k"] == 5 and policy_privacy(six)[1]["k"] == 6
    cfg = config(tmp_path)
    cfg["gold"]["policy"] = str(five)
    make_build(tmp_path / "gold", "2026-05-04", policy_sha=policy_privacy(five)[0])
    first = run(tmp_path, cfg=cfg)
    assert first.extra["frozen"] == ["2026-04"] and first.extra["warnings"] == ["stale_source claude", "stale_source proxy"]
    cfg["gold"]["policy"] = str(six)
    make_build(tmp_path / "gold", "2026-05-05", policy_sha=policy_privacy(six)[0])
    second = run(tmp_path, today=date(2026, 5, 5), minutes=60, cfg=cfg)
    assert second.exit_code == 0 and second.extra["warnings"] == ["policy_changed 2026-04", "stale_source claude", "stale_source proxy"]
    published = json.loads((served(tmp_path) / "central" / "published.json").read_text(encoding="utf-8"))
    assert published["warnings"] == ["policy_changed 2026-04", "stale_source claude", "stale_source proxy"]
    assert set(published["policy_sha256"]) == {"2026-05-04", "2026-05-05"} and len(set(published["policy_sha256"].values())) == 2


def test_a_policy_change_that_leaves_the_privacy_parameters_alone_is_no_warning(tmp_path):
    five = policy_with_k(tmp_path, 5)
    other = tmp_path / "policy-label.yaml"
    other.write_text(five.read_text(encoding="utf-8").replace('label: "Invented', 'label: "Changed, invented', 1), encoding="utf-8")
    assert policy_privacy(other)[0] != policy_privacy(five)[0] and policy_privacy(other)[1] == policy_privacy(five)[1]
    cfg = config(tmp_path)
    cfg["gold"]["policy"] = str(five)
    make_build(tmp_path / "gold", "2026-05-04", policy_sha=policy_privacy(five)[0])
    run(tmp_path, cfg=cfg)
    cfg["gold"]["policy"] = str(other)
    make_build(tmp_path / "gold", "2026-05-05", policy_sha=policy_privacy(other)[0])
    assert run(tmp_path, today=date(2026, 5, 5), minutes=60, cfg=cfg).extra["warnings"] == ["stale_source claude", "stale_source proxy"]


# ---- P5: the freshness files ---------------------------------------------------------------------------------------------


def test_published_json_says_what_the_folder_holds(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    DONE["proxy"] = date(2026, 4, 30)
    run(tmp_path)
    make_build(tmp_path / "gold", "2026-05-05", key_check="kc_one")
    run(tmp_path, today=date(2026, 5, 5), minutes=60)
    data = json.loads((served(tmp_path) / "central" / "published.json").read_text(encoding="utf-8"))
    assert data["latest_build"] == "2026-05-05" and data["data_complete_through"] == "2026-04-30"
    assert data["published_at"] == "2026-05-04T07:00:00+00:00"
    assert data["frozen_months"] == ["2026-04"] and data["in_progress_months"] == ["2026-05"]
    assert data["forced_freezes"] == [] and data["key_rotations"] == [] and data["warnings"] == ["stale_source claude", "stale_source proxy"]
    assert list(data["policy_sha256"]) == ["2026-05-04", "2026-05-05"] and all(len(v) == 64 for v in data["policy_sha256"].values())
    assert sorted(data) == ["data_complete_through", "forced_freezes", "frozen_months", "in_progress_months", "key_rotations",
                            "latest_build", "policy_sha256", "published_at", "sources", "warnings"]


def test_published_json_lists_a_forced_freeze_and_a_missing_completeness(tmp_path):
    make_build(tmp_path / "gold", "2026-05-14")
    DONE["proxy"] = None
    run(tmp_path, today=date(2026, 5, 14), complete=False)
    data = json.loads((served(tmp_path) / "central" / "published.json").read_text(encoding="utf-8"))
    assert data["forced_freezes"] == ["2026-04"] and data["warnings"] == ["forced_freeze 2026-04", "stale_source claude"]
    assert data["data_complete_through"] is None


def test_every_other_audience_folder_holds_a_freshness_file_with_two_keys(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    folders = [served(tmp_path) / "exec", served(tmp_path) / "manager" / "mk_a", served(tmp_path) / "person" / "pk_a",
               served(tmp_path) / "person" / "pk_b"]
    for folder in folders:
        data = json.loads((folder / "freshness.json").read_text(encoding="utf-8"))
        assert sorted(data) == ["data_through", "frozen_months", "published_at", "stale_sources"], folder
        assert data["published_at"] == "2026-05-04T06:00:00+00:00" and data["frozen_months"] == ["2026-04"]
    assert not (served(tmp_path) / "central" / "freshness.json").exists()


def test_a_keyed_folder_lists_only_the_frozen_months_it_holds(tmp_path):
    key_change_scenario(tmp_path)
    assert json.loads((served(tmp_path) / "person" / "pk_c" / "freshness.json").read_text(encoding="utf-8"))["frozen_months"] == []
    assert json.loads((served(tmp_path) / "exec" / "freshness.json").read_text(encoding="utf-8"))["frozen_months"] == ["2026-04"]


def test_the_two_files_validate_against_their_committed_schemas(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    root = Path(__file__).resolve().parents[3] / "docs" / "schemas" / "job"
    published_schema = json.loads((root / "published.schema.json").read_text(encoding="utf-8"))
    freshness_schema = json.loads((root / "freshness.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads((served(tmp_path) / "central" / "published.json").read_text(encoding="utf-8")), published_schema)
    jsonschema.validate(json.loads((served(tmp_path) / "exec" / "freshness.json").read_text(encoding="utf-8")), freshness_schema)
    broken = json.loads((served(tmp_path) / "central" / "published.json").read_text(encoding="utf-8"))
    broken["published"] = broken.pop("published_at")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, published_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"published_at": "x", "frozen_months": [], "extra": 1}, freshness_schema)


def test_the_committed_schemas_are_what_the_code_generates(tmp_path):
    root = Path(__file__).resolve().parents[3] / "docs" / "schemas" / "job"
    schemas.write_schemas(tmp_path)
    for name in ("published.schema.json", "freshness.schema.json"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (root / name).read_text(encoding="utf-8"), (
            "%s differs from what meterline_job.schemas generates; run: python -m meterline_job.schemas docs/schemas/job" % name)


def test_the_freshness_files_are_servable_to_their_own_audience_only(tmp_path):
    make_build(tmp_path / "gold", "2026-05-04")
    run(tmp_path)
    assert servable("central/published.json", "central") and not any(servable("central/published.json", a) for a in ("exec", "manager", "person"))
    assert servable("exec/freshness.json", "exec") and not any(servable("exec/freshness.json", a) for a in ("central", "manager", "person"))
    assert servable("manager/mk_a/freshness.json", "manager:mk_a") and not servable("manager/mk_a/freshness.json", "manager:mk_z")
    assert servable("person/pk_a/freshness.json", "person:pk_a") and not servable("person/pk_a/freshness.json", "exec")


def test_the_key_rule_in_one_table():
    frozen = {"2026-03": {"key_check": "kc_a"}, "2026-04": {"key_check": "kc_b"}}
    assert publish.frozen_months_for("person", "kc_b", frozen) == ["2026-04"]
    assert publish.frozen_months_for("manager", "kc_a", frozen) == ["2026-03"]
    assert publish.frozen_months_for("exec", "kc_b", frozen) == ["2026-03", "2026-04"]
    assert publish.frozen_months_for("central", "kc_b", frozen) == ["2026-03", "2026-04"]


def test_the_names_of_the_two_files_are_pinned():
    """Typed here, never read from the schema: a key added, removed or renamed fails until this list is changed on purpose."""
    assert sorted(schemas.published_schema()["properties"]) == [
        "data_complete_through", "forced_freezes", "frozen_months", "in_progress_months", "key_rotations", "latest_build",
        "policy_sha256", "published_at", "sources", "warnings"]
    assert sorted(schemas.freshness_schema()["properties"]) == ["data_through", "frozen_months", "published_at", "stale_sources"]
