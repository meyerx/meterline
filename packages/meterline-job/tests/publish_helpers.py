"""Helpers for the publish tests: a synthetic gold build with the real file layout and headers, a config, and the readers.

Everything is invented. A build made here has the files of a real gold folder (central, exec, manager/<key>, person/<key>,
delivery, manifest.json) with the column lists of ``meterline_gold.write.SCHEMAS``; the cell values are simple and
derived from the month, so a test can tell a corrected figure from the figure it replaced. The readers use ``csv``
directly and nothing of the code under test.
"""
import csv
import json
from pathlib import Path

from meterline_gold import GOLD_SCHEMA_VERSION, policy_privacy, write

EXAMPLES = Path(__import__("meterline_gold").__file__).resolve().parent / "examples"
POLICY = EXAMPLES / "policy.example.yaml"
POLICY_SHA, PRIVACY = policy_privacy(POLICY)

CENTRAL_MONTHLY = ["person_day", "person_day_activity", "person_month", "seats_month", "pools_month", "costs_month",
                   "reconciliation_month", "data_quality", "groups_month", "comparison_groups", "scenario_persons",
                   "scenario_units", "scenario_summary", "named_exceptions"]
CENTRAL_PLAIN = ["people", "units", "identity_map", "alert_events"]
EXEC_FILES = {"groups_month": "groups_month_marked", "seats_month": "exec_seats_month", "pools_month": "exec_pools_month",
              "costs_month": "exec_costs_month", "scenario_summary": "exec_scenario_summary"}
MONEY_BASE = 10


def _cell(kind, column, month, person, manager, final, bump):
    if column == "schema_version":
        return GOLD_SCHEMA_VERSION
    if column == "month":
        return month
    if column in ("day", "attribution_day", "eval_day", "valid_from", "budget_crossed_on"):
        return month + "-01"
    if column == "valid_to":
        return ""
    if column == "person_key":
        return person
    if column == "manager_key":
        return manager
    if column == "is_final":
        return "true" if final else "false"
    if kind == "bool":
        return "false"
    if kind == "money":
        return "%d.5" % (MONEY_BASE + bump)
    if kind in ("count", "quantity"):
        return "3"
    if kind == "ratio":
        return "0.5000"
    if kind in ("map_count", "map_money", "json"):
        return "{}"
    if kind == "date":
        return month + "-01"
    return {"cell_id": "cell-1", "compare_cell": "cmp-1", "rule_id": "rule-1", "scenario_id": "sc-1",
            "provider": "anthropic", "provider_account_id": "acct-1", "metric": "metric-1"}.get(column, column + "-1")


def _rows(schema_name, months, people, manager, non_final, bumps, per_person):
    schema = write.SCHEMAS[schema_name]
    out = []
    for month in months:
        for person in (people if per_person else [""]):
            out.append([GOLD_SCHEMA_VERSION] + [
                _cell(kind, column, month, person, manager, month not in non_final, bumps.get(month, 0))
                for column, kind in schema])
    return ["schema_version"] + [c for c, _ in schema], out


def _write(path, header, rows):
    write.write_csv(path, header, rows)


def make_build(gold_root, name, months=("2026-04", "2026-05"), key_check="kc_one", people=("pk_a", "pk_b"),
               managers=("mk_a",), non_final=(), bumps=None, policy_sha=POLICY_SHA, parquet=False, data_through=None):
    """A gold-shaped folder ``gold_root/builds/<name>``; returns its path.

    ``non_final``: months whose rows carry is_final = false. ``bumps``: {month: n} adds n to every money cell of that
    month (a late correction). ``parquet`` writes the Parquet twins with gold's own writer."""
    bumps, non_final = dict(bumps or {}), set(non_final)
    folder = Path(gold_root) / "builds" / name
    manager = managers[0] if managers else ""
    for stem in CENTRAL_MONTHLY:
        header, rows = _rows(stem, months, people, manager, non_final, bumps,
                             stem in ("person_day", "person_day_activity", "person_month", "scenario_persons",
                                      "named_exceptions"))
        _write(folder / "central" / (stem + ".csv"), header, rows)
    for stem in CENTRAL_PLAIN:
        header, rows = _rows(stem, ["2026-01"], people, manager, set(), {}, stem in ("people", "identity_map"))
        _write(folder / "central" / (stem + ".csv"), header, rows)
    header, rows = _rows("directory", ["2026-01"], people, manager, set(), {}, True)
    _write(folder / "delivery" / "directory.csv", header, rows)
    for stem, schema_name in EXEC_FILES.items():
        header, rows = _rows(schema_name, months, people, manager, non_final, bumps, False)
        _write(folder / "exec" / (stem + ".csv"), header, rows)
    for key in managers:
        for stem, schema_name in (("groups_month", "groups_month_marked"), ("named_exceptions", "named_exceptions")):
            header, rows = _rows(schema_name, months, people[:1], key, non_final, bumps, stem == "named_exceptions")
            _write(folder / "manager" / key / (stem + ".csv"), header, rows)
    for person in people:
        for stem, schema_name in (("person_day", "person_day"), ("person_month", "person_month_person"),
                                  ("comparison", "comparison")):
            header, rows = _rows(schema_name, months, [person], manager, non_final, bumps, True)
            _write(folder / "person" / person / (stem + ".csv"), header, rows)
    if parquet:
        from meterline_gold.write import write_parquet_text

        for path in sorted(folder.rglob("*.csv")):
            write_parquet_text(path.with_suffix(".parquet"), _schema_name_of(path.relative_to(folder)), *read_csv(path))
    manifest = {"gold_schema_version": GOLD_SCHEMA_VERSION, "months": list(months), "key_check": key_check,
                "data_through": data_through or (months[-1] + "-28"), "as_of": months[-1] + "-28",
                "inputs": {"policy_sha256": policy_sha}, "outputs": []}
    (folder / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return folder


def _schema_name_of(rel):
    parts = Path(rel).parts
    stem = Path(rel).stem
    if parts[0] == "exec":
        return EXEC_FILES[stem]
    if parts[0] == "manager" and stem == "groups_month":
        return "groups_month_marked"
    if parts[0] == "person" and stem == "person_month":
        return "person_month_person"
    return stem


def read_csv(path):
    """(header, rows) of a CSV file, read with ``csv`` and nothing else."""
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def month_rows(folder, month):
    """{relative path: sorted rows} of every CSV under ``folder`` that has a month column, rows of ``month`` only."""
    out = {}
    for path in sorted(Path(folder).rglob("*.csv")):
        header, rows = read_csv(path)
        if "month" in header:
            at = header.index("month")
            out[path.relative_to(folder).as_posix()] = sorted(r for r in rows if r[at] == month)
    return out


def count_non_final(build, month):
    """Rows of ``month`` with is_final = false in the four central datasets that carry the column, counted directly."""
    total = 0
    for stem in ("person_day", "person_month", "pools_month", "groups_month"):
        header, rows = read_csv(Path(build) / "central" / (stem + ".csv"))
        total += sum(1 for r in rows if r[header.index("month")] == month and r[header.index("is_final")] == "false")
    return total


def config(tmp_path, **extra):
    """A job configuration over ``tmp_path``; ``extra`` replaces top-level keys."""
    (tmp_path / "org").mkdir(exist_ok=True)
    base = {
        "data_root": str(tmp_path / "data"), "gold_root": str(tmp_path / "gold"),
        "gold": {"org": str(tmp_path / "org"), "policy": str(POLICY), "prices": str(EXAMPLES / "prices.example.yaml")},
        "connector": [{"name": "claude", "kind": "anthropic", "since": "2026-04-01"},
                      {"name": "proxy", "kind": "litellm", "since": "2026-04-01"}],
    }
    base.update(extra)
    return base


# ---- a simulator run cut at a day, for the slow end-to-end test -------------------------------------------------------

KINDS = ("usage", "activity", "seats", "seat_assignments", "org_totals")


def sim_silver_through(sim_run, data_root, day, non_final_days=3, correct=None):
    """Write the simulator's records dated on or before ``day`` into ``data_root/<provider>/records/<kind>/``.

    A record counts as dated by its ``period_end``. The records of the last ``non_final_days`` days up to ``day`` (per
    provider) get ``is_final = false`` where the kind has the field. Each provider folder also gets
    ``complete_through.json``: the last day before that window, which is what the test's stand-in for a connector's
    ``complete_through`` reads. ``correct(record)`` may change a record (a late correction); it runs on the kept records.
    Returns ``{provider: last final day}``."""
    from datetime import timedelta

    sim_run, data_root = Path(sim_run), Path(data_root)
    limit = day.isoformat()
    window_start = (day - timedelta(days=non_final_days - 1)).isoformat()
    final_through = (day - timedelta(days=non_final_days)).isoformat()
    for folder in data_root.glob("*/records"):
        __import__("shutil").rmtree(folder)
    providers = {}
    for kind in KINDS:
        for path in sorted((sim_run / "records" / kind).glob("*.jsonl")):
            by_provider = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record["period_end"] > limit:
                    continue
                if "is_final" in record and record["period_end"] >= window_start:
                    record["is_final"] = False
                if correct is not None:
                    record = correct(record) or record
                by_provider.setdefault(record["provider"], []).append(json.dumps(record, sort_keys=True))
            for provider, lines in by_provider.items():
                target = data_root / provider / "records" / kind / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")
                providers[provider] = final_through
    for provider in providers:
        (data_root / provider / "complete_through.json").write_text(json.dumps({"through": final_through}), encoding="utf-8")
    return providers
