"""Canonical CSV writer, folder layout, manifest and the A09 file check (spec 9).

The runner writes the bytes: values are fetched as Python objects and formatted here by fixed rules; DuckDB's own
text conversion is never used.
"""

import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path

from meterline import amount_str, quantity_str

from meterline_gold import GOLD_SCHEMA_VERSION, __version__, params  # both defined once, in __init__.py
from meterline_gold.errors import BuildFailure, InputError

GENERATOR = {"name": "meterline-gold", "version": __version__}

T = "text"
_TOKENS = [("tokens_input_uncached", "count"), ("tokens_cache_read", "count"), ("tokens_cache_write", "count"),
           ("tokens_cache_write_5m", "count"), ("tokens_cache_write_1h", "count"),
           ("tokens_input_total", "count"), ("tokens_output", "count")]
_SEAT_COLS = [("month", T), ("provider", T), ("provider_account_id", T), ("seat_source", T), ("eval_day", "date"),
              ("seats_billed", "count"), ("seats_on_eval_day", "count"), ("active_seats", "count"),
              ("idle_seats", "count"), ("seats_reported_max", "count"), ("seat_price", "money"),
              ("seat_cost", "money"), ("currency", T)]
_POOL_COLS = [("month", T), ("provider", T), ("provider_account_id", T), ("seats_billed", "count"),
              ("included_per_seat", "quantity"), ("pool_credits", "quantity"), ("gross_credits", "quantity"),
              ("pool_used_credits", "quantity"), ("overage_credits", "quantity"), ("overage_amount", "money"),
              ("pool_used_share", "ratio"), ("budget_amount", "money"), ("budget_state", T),
              ("budget_crossed_on", "date"), ("currency", T), ("is_final", "bool")]
_COST_COLS = [("month", T), ("provider", T), ("provider_account_id", T), ("currency", T), ("usage_cost_source", "money"),
              ("cost_source_basis", T), ("usage_cost_ref", "money"), ("seat_cost", "money"),
              ("total_cost_ref", "money"), ("cost_basis_label", T), ("invoice_usage", "money")]

# dataset -> ordered (column, type); the first column of a file, schema_version, is added by the writer
SCHEMAS = {
    "people": [("person_key", T), ("valid_from", "date"), ("valid_to", "date"), ("unit_id", T), ("manager_key", T),
               ("function", T), ("level", T), ("cost_center", T), ("population", T), ("attrs", "json")],
    "units": [("unit_id", T), ("valid_from", "date"), ("valid_to", "date"), ("dimension", T), ("key", T),
              ("name", T), ("kind", T), ("parent_id", T), ("function", T), ("cost_center", T), ("head_key", T)],
    "identity_map": [("person_key", T), ("provider", T), ("method", T), ("actor_kind", T), ("actor_key", T),
                     ("valid_from", "date"), ("valid_to", "date")],
    "directory": [("person_key", T), ("given_name", T), ("family_name", T), ("email", T)],
    "person_day": [("day", "date"), ("month", T), ("provider", T), ("provider_account_id", T), ("person_key", T),
                   ("actor_key", T), ("identity_status", T), ("identity_method", T), ("actor_kind", T),
                   ("product", T), ("model", T), ("model_tier", T), ("requests", "count")] + _TOKENS + [
                   ("credits", "quantity"), ("cost_source", "money"), ("cost_source_basis", T),
                   ("cost_source_currency", T), ("cost_ref", "money"), ("cost_ref_status", T),
                   ("is_final", "bool"), ("source_records", "count")],
    "person_day_activity": [("day", "date"), ("month", T), ("provider", T), ("provider_account_id", T), ("person_key", T),
                            ("actor_key", T), ("identity_status", T), ("product", T), ("sessions", "count"),
                            ("messages", "count"), ("requests", "count"), ("suggestions_shown", "count"),
                            ("suggestions_accepted", "count"), ("lines_added", "count"),
                            ("lines_removed", "count"), ("edits_accepted", "count"), ("edits_rejected", "count"),
                            ("commits", "count"), ("pull_requests", "count"), ("source_records", "count")],
    "person_month": [("month", T), ("person_key", T), ("month_status", T), ("attribution_day", "date"),
                     ("unit_id", T), ("manager_key", T), ("function", T), ("level", T), ("cost_center", T),
                     ("population", T), ("employed_days", "count"), ("active_days", "count"),
                     ("active_days_by_tool", "map_count"), ("requests", "count"),
                     ("tokens_input_total", "count"), ("tokens_output", "count"), ("tokens_cache_read", "count"),
                     ("credits", "quantity"), ("cost_ref", "money"), ("cost_ref_by_provider", "map_money"),
                     ("rows_unpriced", "count"), ("rows_unknown_model", "count"), ("premium_share", "ratio"),
                     ("cache_rate", "ratio"), ("intensity_band", T), ("is_final", "bool"),
                     ("projected_cost_ref", "money")],
    "seats_month": _SEAT_COLS,
    "pools_month": _POOL_COLS,
    "costs_month": _COST_COLS,
    # the exec forms (7.6.1) carry the hidden marker (7.6.2, v1.2); written under the central file names
    "exec_seats_month": _SEAT_COLS + [("hidden", "bool")],
    "exec_pools_month": _POOL_COLS + [("hidden", "bool")],
    "exec_costs_month": _COST_COLS + [("hidden", "bool")],
    "reconciliation_month": [("month", T), ("provider", T), ("provider_account_id", T), ("currency", T),
                             ("actor_cost_source", "money"), ("org_cost_source", "money"), ("org_cost_basis", T),
                             ("unallocated_cost_source", "money"), ("unreconciled_share", "ratio"),
                             ("actor_credits", "quantity"), ("org_credits", "quantity"),
                             ("unallocated_credits", "quantity"), ("cost_ref", "money"),
                             ("ref_vs_source_gap", "money"), ("ref_vs_source_share", "ratio"),
                             ("invoice_usage", "money"), ("invoice_usage_gap", "money"),
                             ("invoice_seats", "money"), ("invoice_seats_gap", "money")],
    # the value column is formatted by the metric's type; the query carries the type and the raw values
    "data_quality": [("month", T), ("provider", T), ("provider_account_id", T), ("metric", T), ("value", "dq")],
    "groups_month": [("month", T), ("cell_id", T), ("cell_kind", T), ("cell_key", T), ("depth", "count"),
                     ("parent_cell_id", T), ("suppressed", "bool"), ("suppression_reason", T), ("persons", "count"),
                     ("active_persons", "count"), ("active_days", "count"), ("cost_ref", "money"),
                     ("p50_cost_ref", "money"), ("p75_cost_ref", "money"), ("p90_cost_ref", "money"),
                     ("p99_cost_ref", "money"), ("top_decile_share", "ratio"), ("is_final", "bool")],
    # the exec and manager forms (7.6.2): the central columns without suppressed and suppression_reason, then hidden
    "groups_month_marked": [("month", T), ("cell_id", T), ("cell_kind", T), ("cell_key", T), ("depth", "count"),
                            ("parent_cell_id", T), ("persons", "count"), ("active_persons", "count"),
                            ("active_days", "count"), ("cost_ref", "money"), ("p50_cost_ref", "money"),
                            ("p75_cost_ref", "money"), ("p90_cost_ref", "money"), ("p99_cost_ref", "money"),
                            ("top_decile_share", "ratio"), ("is_final", "bool"), ("hidden", "bool")],
    "comparison_groups": [("month", T), ("compare_cell", T), ("persons", "count"), ("p50_cost_ref", "money"),
                          ("p75_cost_ref", "money"), ("p90_cost_ref", "money")],
    "comparison": [("month", T), ("compare_cell", T), ("own_cost_ref", "money"), ("comparison_band", T),
                   ("hidden", "bool")],
    "scenario_persons": [("scenario_id", T), ("month", T), ("person_key", T), ("cap", "money"),
                         ("cost_ref", "money"), ("stopped_on", "date"), ("excess", "money"), ("top_ups", "count"),
                         ("top_up_total", "money")],
    "scenario_units": [("scenario_id", T), ("month", T), ("unit_id", T), ("envelope", "money"),
                       ("position", "money"), ("stopped_on", "date"), ("excess", "money"), ("top_ups", "count"),
                       ("top_up_total", "money")],
    "scenario_summary": [("scenario_id", T), ("month", T), ("persons", "count"), ("persons_stopped", "count"),
                         ("persons_excess", "money"), ("persons_top_up_total", "money"), ("units", "count"),
                         ("units_stopped", "count"), ("units_excess", "money"), ("units_top_up_total", "money")],
    # the exec form (7.6.1); written under the file name scenario_summary
    "exec_scenario_summary": [("scenario_id", T), ("month", T), ("persons", "count"),
                              ("persons_stopped", "count"), ("units", "count"), ("hidden", "bool")],
    "alert_events": [("day", "date"), ("event_type", T), ("scenario_id", T), ("subject_kind", T),
                     ("subject_key", T), ("value", "money"), ("threshold", "money"), ("trailing_sum", "money"),
                     ("trailing_days", "count")],
    # threshold and value are text columns that A09 leaves out (9.5); value is typed per row by the query
    "named_exceptions": [("month", T), ("rule_id", T), ("audience", T), ("person_key", T), ("manager_key", T),
                         ("condition", T), ("threshold", "ntext"), ("value", "nx")],
}

# the person_month file of a person folder: the central columns without manager_key (owner decision of 2026-10-16,
# minimisation: a person folder carries no other person's key), written under the file name person_month
SCHEMAS["person_month_person"] = [(c, kind) for c, kind in SCHEMAS["person_month"] if c != "manager_key"]

# dataset -> key columns, equal to the spec's keys (6.1-6.9)
DATASET_KEYS = {
    "people": ("person_key", "valid_from"),
    "units": ("unit_id", "valid_from"),
    "identity_map": ("provider", "actor_kind", "actor_key", "valid_from"),
    "directory": ("person_key",),
    "person_day": ("day", "provider", "provider_account_id", "person_key", "actor_key", "product", "model"),
    "person_day_activity": ("day", "provider", "provider_account_id", "person_key", "actor_key", "product"),
    "person_month": ("month", "person_key"),
    "seats_month": ("month", "provider", "provider_account_id"),
    "pools_month": ("month", "provider", "provider_account_id"),
    "costs_month": ("month", "provider", "provider_account_id"),
    "reconciliation_month": ("month", "provider", "provider_account_id"),
    "data_quality": ("month", "provider", "provider_account_id", "metric"),
    "groups_month": ("month", "cell_id"),
    "comparison_groups": ("month", "compare_cell"),
    "comparison": ("month",),
    "scenario_persons": ("scenario_id", "month", "person_key"),
    "scenario_units": ("scenario_id", "month", "unit_id"),
    "scenario_summary": ("scenario_id", "month"),
    "alert_events": ("day", "event_type", "scenario_id", "subject_kind", "subject_key", "threshold"),
    "named_exceptions": ("month", "rule_id", "person_key"),
}

# (audience folder, dataset, query name); the manifest and the report list central files first
LAYOUT = [("central", name, name) for name in (
    "people", "units", "identity_map", "person_day", "person_day_activity", "person_month", "seats_month",
    "pools_month", "costs_month", "reconciliation_month", "data_quality", "groups_month",
    "comparison_groups", "scenario_persons", "scenario_units", "scenario_summary", "alert_events",
    "named_exceptions")] + [
    ("delivery", "directory", "directory"),
    ("exec", "groups_month", "exec/groups_month"),
    ("exec", "seats_month", "exec/seats_month"),
    ("exec", "pools_month", "exec/pools_month"),
    ("exec", "costs_month", "exec/costs_month"),
]
# the exec files that carry the hidden marker use their own schema (7.6.2)
EXEC_MARKED = {"seats_month": "exec_seats_month", "pools_month": "exec_pools_month", "costs_month": "exec_costs_month",
               "groups_month": "groups_month_marked"}
# audience folders whose file is the exec form of a dataset: (audience, file name, query, schema)
EXEC_SUMMARY = ("exec", "scenario_summary", "exec/scenario_summary", "exec_scenario_summary")


def output_path(audience, name):
    return "%s/%s.csv" % (audience, name)


# ---- value formatting -------------------------------------------------------------------------------------


def _ratio(value):
    return format(Decimal(value).quantize(Decimal("0.0001")), "f")


def _map_value(kind, value):
    if kind == "map_money":
        return amount_str(Decimal(value))
    return int(value)


def format_value(kind, value):
    if value is None:
        return ""
    if kind == "text":
        return str(value)
    if kind == "date":
        return value.isoformat()
    if kind == "count":
        return str(int(value))
    if kind == "money":
        return amount_str(Decimal(value))
    if kind == "quantity":
        return quantity_str(Decimal(value))
    if kind == "ratio":
        return _ratio(value)
    if kind == "bool":
        return "true" if value else "false"
    if kind in ("map_money", "map_count"):
        obj = {str(k): _map_value(kind, v) for k, v in dict(value).items()}
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if kind == "json":
        return json.dumps(json.loads(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if kind == "ntext":
        return str(value)
    raise ValueError("unknown column type " + kind)


def _dq_value(value_type, v_count, v_decimal, v_date):
    if value_type == "count":
        return str(int(v_count))
    if value_type == "money":
        return amount_str(Decimal(v_decimal))
    if value_type == "ratio":
        return _ratio(v_decimal)
    if value_type == "date":
        return v_date.isoformat()
    raise ValueError("unknown metric type " + value_type)


def format_rows(name, columns, rows, schema_name=None):
    """Header names and formatted string rows for a dataset (schema_version first)."""
    schema = SCHEMAS[schema_name or name]
    names = [c for c, _ in schema]
    header = ["schema_version"] + names
    out = []
    if name == "data_quality":
        idx = {c: i for i, c in enumerate(columns)}
        for r in rows:
            out.append([GOLD_SCHEMA_VERSION, _txt(r[idx["month"]]), r[idx["provider"]], r[idx["provider_account_id"]],
                        r[idx["metric"]],
                        _dq_value(r[idx["value_type"]], r[idx["v_count"]], r[idx["v_decimal"]], r[idx["v_date"]])])
        return header, out
    if name == "named_exceptions":
        idx = {c: i for i, c in enumerate(columns)}
        for r in rows:
            value = _dq_value(r[idx["value_type"]], r[idx["v_count"]], r[idx["v_decimal"]], None)
            out.append([GOLD_SCHEMA_VERSION, r[idx["month"]], r[idx["rule_id"]], r[idx["audience"]],
                        r[idx["person_key"]], _txt(r[idx["manager_key"]]), r[idx["condition"]],
                        r[idx["threshold_text"]], value])
        return header, out
    if columns != names:
        raise BuildFailure("%s: the query returns %s, the schema is %s" % (name, columns, names))
    for r in rows:
        out.append([GOLD_SCHEMA_VERSION] + [format_value(kind, v) for (_, kind), v in zip(schema, r, strict=True)])
    return header, out


def _txt(value):
    return "" if value is None else str(value)


def write_csv(path, header, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)


# ---- Parquet (optional, outside the byte check) ---------------------------------------------------------------


def write_parquet(path, name, columns, rows, schema_name=None):
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise InputError("--parquet needs pyarrow; install the 'parquet' extra (%s)" % exc) from exc
    schema = SCHEMAS[schema_name or name]
    header, formatted = format_rows(name, columns, rows, schema_name)
    kinds = [k for _, k in schema]
    fields, arrays = [pa.field("schema_version", pa.string())], [pa.array([GOLD_SCHEMA_VERSION] * len(rows), pa.string())]
    for i, ((col, kind), _) in enumerate(zip(schema, kinds, strict=True)):
        values = [r[i + 1] for r in formatted]
        if name == "data_quality" or kind in ("text", "ntext", "nx", "date", "json", "map_money", "map_count", "dq",
                                              "bool"):
            fields.append(pa.field(col, pa.string()))
            arrays.append(pa.array([v if v != "" else None for v in values], pa.string()))
        elif kind == "count":
            fields.append(pa.field(col, pa.int64()))
            arrays.append(pa.array([int(v) if v != "" else None for v in values], pa.int64()))
        else:
            fields.append(pa.field(col, pa.decimal128(38, 12)))
            arrays.append(pa.array([Decimal(v) if v != "" else None for v in values], pa.decimal128(38, 12)))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_arrays(arrays, schema=pa.schema(fields)), str(path))


# ---- the gold folder ----------------------------------------------------------------------------------------


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _file_entry(root, rel):
    data = (Path(root) / rel).read_bytes()
    entry = {"path": rel, "bytes": len(data), "sha256": _sha256(data)}
    if rel.endswith(".csv"):
        entry["rows"] = len(list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))) - 1
    else:
        entry["rows"] = None
    return entry


def _split_by_first_column(columns, rows):
    """{key: [rows without the first column]} in the order the rows arrive (they are sorted by that column)."""
    out = {}
    for r in rows:
        out.setdefault(r[0], []).append(tuple(r[1:]))
    return list(columns[1:]), out


def _write_one(folder, audience, name, columns, rows, parquet, schema_name=None):
    header, formatted = format_rows(name, columns, rows, schema_name)
    path = folder / output_path(audience, name)
    write_csv(path, header, formatted)
    if parquet:
        write_parquet(path.with_suffix(".parquet"), name, columns, rows, schema_name)


def write_gold(ctx, folder, run_module, parquet=False):
    """Write every file and the manifest into ``folder`` (which must not exist)."""
    folder = Path(folder)
    for audience, name, query in LAYOUT:
        columns, rows = run_module.fetch(ctx.con, query)
        _write_one(folder, audience, name, columns, rows, parquet,
                   EXEC_MARKED.get(name) if audience == "exec" else None)
    audience, name, query, schema_name = EXEC_SUMMARY
    columns, rows = run_module.fetch(ctx.con, query)
    _write_one(folder, audience, name, columns, rows, parquet, schema_name)
    # manager/<manager_key>/{groups_month, named_exceptions}.csv: one folder per manager, written even with no rows
    _, key_rows = run_module.fetch(ctx.con, "manager/keys")
    columns, rows = run_module.fetch(ctx.con, "manager/groups_month")
    columns, by_manager = _split_by_first_column(columns, rows)
    ne_columns, ne_rows = run_module.fetch(ctx.con, "manager/named_exceptions")
    ne_columns, ne_by_manager = _split_by_first_column(ne_columns, ne_rows)
    for (manager_key,) in key_rows:
        _write_one(folder, "manager/" + manager_key, "groups_month", columns, by_manager.get(manager_key, []),
                   parquet, "groups_month_marked")
        _write_one(folder, "manager/" + manager_key, "named_exceptions", ne_columns,
                   ne_by_manager.get(manager_key, []), parquet)
    # person/<person_key>/{person_day, person_month, comparison}.csv: one folder per person with a person_month row
    person_keys = [k for (k,) in ctx.con.execute(
        "SELECT DISTINCT person_key FROM person_month ORDER BY person_key").fetchall()]
    person_columns = {}
    for name in ("person_day", "person_month", "comparison"):
        columns, rows = run_module.fetch(ctx.con, name)
        idx = columns.index("person_key")
        # the person_day and person_month files keep person_key; the comparison file has none (7.5); the
        # person_month file has no manager_key (a person folder carries no other person's key)
        dropped = {"comparison": ("person_key",), "person_month": ("manager_key",)}.get(name, ())
        drop_at = [i for i, c in enumerate(columns) if c in dropped]
        grouped = {}
        for r in rows:
            if r[idx] is not None:
                keep = [v for i, v in enumerate(r) if i not in drop_at]
                grouped.setdefault(r[idx], []).append(tuple(keep))
        person_columns[name] = ([c for i, c in enumerate(columns) if i not in drop_at], grouped)
    for key in person_keys:
        for name in ("person_day", "person_month", "comparison"):
            columns, grouped = person_columns[name]
            _write_one(folder, "person/" + key, name, columns, grouped.get(key, []), parquet,
                       "person_month_person" if name == "person_month" else None)
    violations = a09_violations(folder, ctx.org_dir, ctx.silver_dir)
    if violations:
        raise BuildFailure("A09: an identifier reached an output: %s" % (violations[0],))
    write_manifest(ctx, folder)


def _suppression_counts(ctx):
    """All cell-months and the suppressed ones by reason (spec 7.4)."""
    cells, primary, differencing, secondary = ctx.con.execute(
        "SELECT count(*), count(*) FILTER (WHERE reason = 'k'), count(*) FILTER (WHERE reason = 'differencing'), "
        "count(*) FILTER (WHERE reason = 'secondary') FROM cell_state"
    ).fetchone()
    return {"cells": cells, "k": primary, "differencing": differencing, "secondary": secondary}


def input_flags(ctx):
    """F-37: what the inputs got wrong without stopping the build. ``persons_missing`` counts the persons that an
    identity row or a resolved usage row names and that people.csv does not employ in any month of the build."""
    con = ctx.con
    persons = con.execute(
        "SELECT count(*) FROM (SELECT person_key FROM org_identities WHERE person_key IS NOT NULL "
        "AND person_key NOT IN (SELECT person_key FROM org_people) UNION SELECT person_key FROM person_orphans)"
    ).fetchone()[0]
    rows = con.execute("SELECT count(*) FROM person_orphans").fetchone()[0]
    units = con.execute("SELECT count(DISTINCT unit_id) FROM unit_parent_unknown").fetchone()[0]
    return {"persons_missing": persons, "rows_person_missing": rows, "units_parent_unknown": units}


def unknown_envelope_keys(ctx):
    """{scenario id: sorted keys} of the unit_envelopes keys that match no line unit of units.csv (4.3, 9.3)."""
    line = ctx.policy.dimensions[0]
    known = {row["key"] for row in ctx.org.units if row["dimension"] == line}
    out = {}
    for scenario in ctx.policy.scenarios:
        if scenario.unit_envelopes is None:
            continue
        unknown = sorted(k for k in scenario.unit_envelopes.values if k not in known)
        if unknown:
            out[scenario.id] = unknown
    return out


def write_manifest(ctx, folder):
    folder = Path(folder)
    build = ctx.con.execute("SELECT data_through, as_of FROM build_period").fetchone()
    months = [m for (m,) in ctx.con.execute("SELECT month FROM months ORDER BY month").fetchall()]
    files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    silver_root = Path(ctx.silver_dir)
    manifest = {
        "gold_schema_version": GOLD_SCHEMA_VERSION,
        "generator": dict(GENERATOR),
        "as_of": None if build[1] is None else build[1].isoformat(),
        "data_through": None if build[0] is None else build[0].isoformat(),
        "months": months,
        "key_check": ctx.hasher.key_check(),
        "inputs": {
            "policy_sha256": _sha256(params.canonical_hash_input(ctx.policy).encode("utf-8")),
            "prices_sha256": _sha256(params.canonical_hash_input(ctx.prices).encode("utf-8")),
            "invoice_sha256": None if ctx.invoice_path is None else _sha256(ctx.invoice_path.read_bytes()),
            "org": [{"path": p.name, "sha256": _sha256(p.read_bytes())}
                    for p in sorted(Path(ctx.org_dir).glob("*.csv"))],
            "silver": [{"path": p.relative_to(silver_root).as_posix(), "sha256": _sha256(p.read_bytes())}
                       for p in ctx.silver.files],
        },
        "suppression": _suppression_counts(ctx),
        "input_flags": input_flags(ctx),
        "unknown_envelope_keys": unknown_envelope_keys(ctx),
        "outputs": [_file_entry(folder, rel) for rel in files],
    }
    text = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    (folder / "manifest.json").write_text(text, encoding="utf-8", newline="\n")


# ---- A09 ----------------------------------------------------------------------------------------------------


def _identifier_sets(org_dir, silver_dir):
    """(identifiers, exempt): every person_id, actor_id, actor_login and actor_email of the inputs, and the
    org_ids and unit_ids that are allowed to look like one."""
    identifiers, exempt = set(), set()
    org_dir = Path(org_dir)
    for fname, columns in (("people.csv", ["person_id", "manager_id"]), ("identities.csv",
                           ["person_id", "actor_id", "actor_login", "actor_email"]),
                           ("memberships.csv", ["person_id"]), ("directory.csv", ["person_id", "email"]),
                           ("units.csv", ["head_person_id"])):
        path = org_dir / fname
        if not path.is_file():
            continue
        with open(path, encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for column in columns:
                    if row.get(column):
                        identifiers.add(row[column])
    units = org_dir / "units.csv"
    if units.is_file():
        with open(units, encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                exempt.add(row["unit_id"])
    for path in sorted(Path(silver_dir).rglob("*.jsonl")):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                obj = json.loads(line)
                for column in ("actor_id", "actor_login", "actor_email"):
                    if obj.get(column):
                        identifiers.add(obj[column])
                if obj.get("provider_account_id"):
                    exempt.add(obj["provider_account_id"])
    return identifiers - exempt, exempt


def a09_violations(out_dir, org_dir, silver_dir):
    """A09 (spec 9.5): (file, line, column, value) of every text column field, in a file other than
    delivery/directory.csv, that equals a person_id, actor_id, actor_login or actor_email of the inputs."""
    identifiers, _ = _identifier_sets(org_dir, silver_dir)
    out_dir = Path(out_dir)
    found = []
    for path in sorted(out_dir.rglob("*.csv")):
        rel = path.relative_to(out_dir).as_posix()
        if rel == "delivery/directory.csv":
            continue
        name = path.stem
        schema = SCHEMAS.get(name)
        if schema is None:
            continue
        text_columns = {"schema_version"} | {c for c, kind in schema if kind == "text"}
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            wanted = [i for i, c in enumerate(header) if c in text_columns]
            for line, row in enumerate(reader, start=2):
                for i in wanted:
                    if i < len(row) and row[i] in identifiers:
                        found.append((rel, line, header[i], row[i]))
    return found
