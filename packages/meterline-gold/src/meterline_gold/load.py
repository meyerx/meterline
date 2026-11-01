"""Stage 0 (spec 3, 5): read silver, organisation files, price list and invoice; hash identifiers; load DuckDB tables.

This is the only place identifiers (person_id, actor_id) are seen. Keyed hashes are computed here, before anything
enters DuckDB, and only the hashes are loaded. The conversion of per-million prices to per-token prices is the one
arithmetic done in Python (exact Decimal).
"""

import csv
import hashlib
import hmac
import json
import os
import re
from collections import defaultdict
from datetime import date, timezone
from decimal import Decimal
from pathlib import Path

from meterline import KINDS, MAX_DECIMALS, RecordError, latest_per_key, read_jsonl, validate

from meterline_gold.errors import BuildFailure, InputError
from meterline_gold.iso4217 import is_iso_4217

_UTC = timezone.utc
KEY_MIN_BYTES = 32
END = date.max
NULL = "\\N"

ORG_FILES = {
    "units.csv": ["unit_id", "valid_from", "valid_to", "dimension", "key", "name", "kind", "parent_id",
                  "function", "cost_center", "head_person_id"],
    "people.csv": ["person_id", "valid_from", "valid_to", "unit_id", "manager_id", "function", "level",
                   "cost_center", "population", "attrs"],
    "memberships.csv": ["person_id", "unit_id", "dimension", "role", "valid_from", "valid_to"],
    "identities.csv": ["person_id", "provider", "method", "actor_kind", "actor_id", "actor_login", "actor_email",
                       "valid_from", "valid_to"],
    "directory.csv": ["person_id", "given_name", "family_name", "email"],
}
METHODS = ("email", "manual", "scim", "derived", "oidc_claim", "non_person")


# ---- hashing ----------------------------------------------------------------------------------------------


class Hasher:
    def __init__(self, key):
        if isinstance(key, str):
            key = key.encode("utf-8")
        if not isinstance(key, (bytes, bytearray)) or len(key) < KEY_MIN_BYTES:
            raise InputError("the hashing key must be at least %d bytes (METERLINE_GOLD_KEY)" % KEY_MIN_BYTES)
        self._key = bytes(key)
        self._cache = {}

    def _h(self, text):
        got = self._cache.get(text)
        if got is None:
            got = hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
            self._cache[text] = got
        return got

    def person(self, person_id):
        return "pk_" + self._h("person|" + person_id)

    def actor(self, provider, kind, actor_id):
        return "ak_" + self._h("actor|%s|%s|%s" % (provider, kind, actor_id))

    def key_check(self):
        return "kc_" + self._h("meterline-gold key check")


def key_from_environment():
    value = os.environ.get("METERLINE_GOLD_KEY")
    if value is None or value == "":
        raise InputError("METERLINE_GOLD_KEY is not set")
    return value.encode("utf-8")


# ---- CSV helpers --------------------------------------------------------------------------------------------


def _read_org_csv(directory, name):
    path = Path(directory) / name
    columns = ORG_FILES[name]
    try:
        handle = open(path, encoding="utf-8", newline="")  # noqa: SIM115 - opened apart so only the open itself reads as "cannot read"; the with below closes it
    except OSError as exc:
        raise InputError("%s: cannot read (%s)" % (path, exc)) from exc
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise InputError("%s: empty file, expected the header %s" % (path, ",".join(columns))) from None
        if header != columns:
            raise InputError("%s:1: header must be %s" % (path, ",".join(columns)))
        rows = []
        for line, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(columns):
                raise InputError("%s:%d: expected %d fields, got %d" % (path, line, len(columns), len(row)))
            item = {c: (v if v != "" else None) for c, v in zip(columns, row, strict=True)}
            item["_line"] = line
            item["_file"] = name
            rows.append(item)
    return rows


def _date(row, column, required=True):
    value = row[column]
    if value is None:
        if required:
            raise InputError("%s:%d: %s is required" % (row["_file"], row["_line"], column))
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InputError("%s:%d: %s must be a date written YYYY-MM-DD" % (row["_file"], row["_line"], column)) from exc


def _require(row, column):
    if row[column] is None:
        raise InputError("%s:%d: %s is required" % (row["_file"], row["_line"], column))
    return row[column]


def _overlaps(intervals):
    """True when two (start, end, label) intervals of a list share a day; end None means open."""
    ordered = sorted(intervals, key=lambda i: (i[0], i[1] or END))
    for a, b in zip(ordered, ordered[1:], strict=False):  # neighbours: one shorter on purpose
        if (a[1] or END) >= b[0]:
            return a, b
    return None


# ---- organisation files -----------------------------------------------------------------------------------


class Org:
    def __init__(self):
        self.units = []
        self.people = []
        self.identities = []
        self.directory = []
        self.unit_ids = set()
        self.person_ids = set()
        self.identifiers = set()


def _check_memberships(members):
    for row in members:
        _require(row, "person_id")
        _require(row, "unit_id")
        _date(row, "valid_from")
        _date(row, "valid_to", required=False)


def _read_units(units, org):
    by_unit = defaultdict(list)
    for row in units:
        uid = _require(row, "unit_id")
        _require(row, "dimension")
        start, end = _date(row, "valid_from"), _date(row, "valid_to", required=False)
        if end is not None and end < start:
            raise InputError("units.csv:%d: valid_to is before valid_from" % row["_line"])
        by_unit[uid].append((start, end, row["_line"]))
        org.unit_ids.add(uid)
    for uid, intervals in sorted(by_unit.items()):
        clash = _overlaps(intervals)
        if clash:
            raise InputError("units.csv:%d: unit %s overlaps its row at line %d" % (clash[1][2], uid, clash[0][2]))


def _person_attrs(row, attrs_keep):
    attrs = row["attrs"]
    if attrs is None:
        kept = {}
    else:
        try:
            obj = json.loads(attrs)
        except ValueError as exc:
            raise InputError("people.csv:%d: attrs is not JSON" % row["_line"]) from exc
        if not isinstance(obj, dict) or any(not isinstance(v, str) for v in obj.values()):
            raise InputError("people.csv:%d: attrs must be a JSON object of strings" % row["_line"])
        kept = {k: obj[k] for k in sorted(obj) if k in attrs_keep}
    return json.dumps(kept, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _read_people(people, line_units, line_dimension, attrs_keep, org):
    by_person = defaultdict(list)
    for row in people:
        pid = _require(row, "person_id")
        start, end = _date(row, "valid_from"), _date(row, "valid_to", required=False)
        if end is not None and end < start:
            raise InputError("people.csv:%d: valid_to is before valid_from" % row["_line"])
        by_person[pid].append((start, end, row["_line"]))
        uid = _require(row, "unit_id")
        if not _covered(line_units.get(uid, []), start, end):
            raise InputError(
                "people.csv:%d: unit_id %s is not a %s unit valid over the whole row" % (row["_line"], uid, line_dimension)
            )
        row["_attrs"] = _person_attrs(row, attrs_keep)
        org.person_ids.add(pid)
    for pid, intervals in sorted(by_person.items()):
        clash = _overlaps(intervals)
        if clash:
            raise InputError("people.csv:%d: person %s overlaps its row at line %d" % (clash[1][2], pid, clash[0][2]))


def _read_identities(identities, org):
    accounts = defaultdict(list)
    for row in identities:
        method = _require(row, "method")
        if method not in METHODS:
            raise InputError("identities.csv:%d: method %r is not one of %s" % (row["_line"], method, ", ".join(METHODS)))
        if (row["person_id"] is None) != (method == "non_person"):
            raise InputError("identities.csv:%d: person_id is empty exactly when method is non_person" % row["_line"])
        _require(row, "provider")
        _require(row, "actor_kind")
        _require(row, "actor_id")
        start, end = _date(row, "valid_from"), _date(row, "valid_to", required=False)
        if end is not None and end < start:
            raise InputError("identities.csv:%d: valid_to is before valid_from" % row["_line"])
        accounts[(row["provider"], row["actor_kind"], row["actor_id"])].append((start, end, row["_line"]))
        org.identities.append(row)
    for account, intervals in sorted(accounts.items()):
        clash = _overlaps(intervals)
        if clash:
            raise InputError(
                "identities.csv:%d: account %s/%s/%s overlaps its row at line %d"
                % (clash[1][2], account[0], account[1], account[2], clash[0][2])
            )


def _collect_identifiers(org, people, identities, directory_rows):
    # every identifier the inputs hold: A09 keeps them out of the outputs
    for row in people:
        org.identifiers.add(row["person_id"])
    for row in identities:
        for column in ("person_id", "actor_id", "actor_login", "actor_email"):
            if row[column]:
                org.identifiers.add(row[column])
    for row in directory_rows:
        org.identifiers.add(row["person_id"])
        if row["email"]:
            org.identifiers.add(row["email"])


def read_org(directory, hasher, line_dimension, attrs_keep):
    org = Org()
    units = _read_org_csv(directory, "units.csv")
    people = _read_org_csv(directory, "people.csv")
    members = _read_org_csv(directory, "memberships.csv")
    identities = _read_org_csv(directory, "identities.csv")
    directory_rows = _read_org_csv(directory, "directory.csv")

    _check_memberships(members)
    _read_units(units, org)

    line_units = defaultdict(list)
    for row in units:
        if row["dimension"] == line_dimension:
            line_units[row["unit_id"]].append((_date(row, "valid_from"), _date(row, "valid_to", required=False)))

    _read_people(people, line_units, line_dimension, attrs_keep, org)

    seen_keys = {}
    for pid in sorted(org.person_ids):
        pk = hasher.person(pid)
        if pk in seen_keys and seen_keys[pk] != pid:
            raise BuildFailure("two person_ids have the same person_key %s" % pk)
        seen_keys[pk] = pid
    for row in people:
        org.people.append(row)

    _read_identities(identities, org)

    for row in directory_rows:
        _require(row, "person_id")
        org.directory.append(row)

    org.units = units
    _collect_identifiers(org, people, identities, directory_rows)
    return org


def _covered(intervals, start, end):
    """Whether the (possibly contiguous) unit intervals cover every day of [start, end]."""
    end = end or END
    day = start
    for s, e in sorted(intervals, key=lambda i: i[0]):
        e = e or END
        if s <= day <= e:
            if e >= end:
                return True
            day = date.fromordinal(e.toordinal() + 1)
    return False


# ---- silver -----------------------------------------------------------------------------------------------


class Silver:
    def __init__(self):
        self.current = {}
        self.usage_batches = []
        self.identifiers = set()
        self.files = []


def _check_decimals(rec, where):
    values = []
    if getattr(rec, "cost", None) is not None:
        values.append(("cost", rec.cost))
    if getattr(rec, "cost_list", None) is not None:
        values.append(("cost_list", rec.cost_list))
    for name, value in (getattr(rec, "quantities", None) or {}).items():
        values.append(("quantities." + name, value))
    for name, value in (getattr(rec, "extras", None) or {}).items():
        if isinstance(value, Decimal):
            values.append(("extras." + name, value))
    for name, value in values:
        if -value.as_tuple().exponent > MAX_DECIMALS:
            raise InputError("%s: %s has more than %d decimals" % (where, name, MAX_DECIMALS))


def _content_hash(rec):
    data = rec.to_dict()
    for name in ("pulled_at", "data_refreshed_at", "batch_id"):
        data.pop(name, None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def read_silver(directory):
    directory = Path(directory)
    silver = Silver()
    for kind, cls in KINDS.items():
        folder = directory / "records" / kind
        records = []
        if folder.is_dir():
            for path in sorted(folder.glob("*.jsonl")):
                silver.files.append(path)
                try:
                    loaded = read_jsonl(path, cls)
                except (RecordError, ValueError, KeyError, TypeError) as exc:
                    raise InputError("%s: %s" % (path, exc)) from exc
                for number, rec in enumerate(loaded, start=1):
                    try:
                        validate(rec)
                    except RecordError as exc:
                        raise InputError("%s: record %d breaks the record format: %s" % (path, number, exc)) from exc
                records.extend(loaded)
        for rec in records:
            _check_decimals(rec, kind)
        current = latest_per_key(records)
        silver.current[kind] = [current[k] for k in sorted(current, key=_sort_key)]
        for rec in records:
            for name in ("actor_id", "actor_login", "actor_email"):
                value = getattr(rec, name, None)
                if value:
                    silver.identifiers.add(value)
        if kind == "usage":
            by_key = defaultdict(list)
            for rec in records:
                by_key[rec.key()].append(rec)
            for key in sorted(by_key, key=_sort_key):
                group = by_key[key]
                if len(group) > 1:
                    silver.usage_batches.extend((rec, _content_hash(rec)) for rec in group)
    silver.files.sort()
    return silver


def check_currencies(silver, reference):
    """F-34: the cost columns of silver are summed per (provider, org, month) and set against the reference cost, so
    every cost in silver is in one currency, an ISO 4217 code, and it is the reference currency. Raises InputError."""
    seen = {}
    for kind in ("usage", "org_totals"):
        for rec in silver.current[kind]:
            if rec.cost is None and rec.cost_list is None:
                continue
            if not is_iso_4217(rec.currency):
                raise InputError("silver %s: currency %r is not an ISO 4217 code" % (kind, rec.currency))
            seen.setdefault(rec.currency, kind)
    if len(seen) > 1:
        raise InputError(
            "silver costs are in more than one currency (%s); gold never adds amounts of different currencies. "
            "Build once per currency from silver that holds one." % ", ".join(sorted(seen))
        )
    if seen and reference not in seen:
        (code,) = seen
        raise InputError(
            "silver costs are in %s but the reference currency of the policy and the price list is %s; the "
            "reference cost is set against the source cost, so both must be in one currency" % (code, reference)
        )


def _sort_key(key):
    return tuple("" if v is None else (v.isoformat() if isinstance(v, date) else str(v)) for v in key)


# ---- DuckDB loading -----------------------------------------------------------------------------------------


def _cell(value):
    if value is None:
        return NULL
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


_SQL_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
_SQL_TYPE = re.compile(r"BIGINT|VARCHAR|DATE|BOOLEAN|DECIMAL\(\d{1,2},\d{1,2}\)")


def _check_sql_names(name, columns):
    """Table and column names and types are spliced into SQL text (identifiers cannot be bound), so they must come
    from the code: plain lower-case identifiers and the column types the loader uses. Anything else is a bug."""
    for ident in [name] + [c for c, _ in columns]:
        if not isinstance(ident, str) or not _SQL_IDENTIFIER.fullmatch(ident):
            raise BuildFailure("load_table: %r is not a plain SQL identifier" % (ident,))
    for _, kind in columns:
        if not isinstance(kind, str) or not _SQL_TYPE.fullmatch(kind):
            raise BuildFailure("load_table: %r is not a column type the loader uses" % (kind,))


def load_table(con, name, columns, rows, tmpdir):
    """CREATE TABLE ``name`` from an iterable of tuples, through a temporary CSV file (fast and exact)."""
    _check_sql_names(name, columns)
    path = os.path.join(tmpdir, name + ".csv")
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        for row in rows:
            writer.writerow([_cell(v) for v in row])
            count += 1
    spec = ", ".join("'%s': '%s'" % (c, t) for c, t in columns)
    # The SQL text below holds only names and types checked by _check_sql_names (identifiers cannot be bound
    # parameters); row values reach DuckDB through the CSV file, whose path and null marker are bound parameters.
    if count == 0:
        con.execute("CREATE OR REPLACE TABLE %s (%s)" % (name, ", ".join("%s %s" % (c, t) for c, t in columns)))
        return
    con.execute(
        "CREATE OR REPLACE TABLE %s AS SELECT * FROM read_csv(?, header=false, columns={%s}, delim=',', quote='\"', "  # nosec B608 # noqa: S608 - names checked above, values in the CSV file
        "escape='\"', nullstr=?, auto_detect=false)" % (name, spec),
        [path, NULL],
    )


def _per_token(text):
    """Per-million price text -> exact per-token Decimal (a decimal shift of six places)."""
    return None if text is None else Decimal(text).scaleb(-6)


def _load_policy(con, policy, tmpdir):
    D = "DECIMAL(38,12)"
    load_table(con, "policy_t", [("k", "BIGINT"), ("idle_after_days", "BIGINT"), ("bands_metric", "VARCHAR"),
                                 ("currency", "VARCHAR"), ("exec_max_depth", "BIGINT"), ("tree_kind", "VARCHAR"),
                                 ("line_dimension", "VARCHAR")],
               [(policy.privacy.k, policy.seats.idle_after_days, policy.bands.metric, policy.currency,
                 policy.audiences.exec.max_depth, policy.privacy.tree.kind, policy.dimensions[0])], tmpdir)
    load_table(con, "policy_tree", [("ord", "BIGINT"), ("attribute", "VARCHAR")],
               [(i, a) for i, a in enumerate(policy.privacy.tree.attributes or [])], tmpdir)
    load_table(con, "policy_compare_by", [("ord", "BIGINT"), ("attribute", "VARCHAR")],
               [(i, a) for i, a in enumerate(policy.privacy.compare_by)], tmpdir)
    load_table(con, "policy_collapse", [("attribute", "VARCHAR"), ("value", "VARCHAR"), ("mapped", "VARCHAR")],
               [(a, v, m) for a in sorted(policy.privacy.collapse) for v, m in sorted(policy.privacy.collapse[a].items())],
               tmpdir)
    # every attribute a grid, comparison group or (phase 3) scenario cap can use, with the kept attrs key or NULL
    names = set(policy.privacy.tree.attributes or []) | set(policy.privacy.compare_by)
    for scenario in policy.scenarios:
        if scenario.person_caps and scenario.person_caps.by is not None:
            names.add(scenario.person_caps.by)
    load_table(con, "policy_attr_names", [("attribute", "VARCHAR"), ("attrs_key", "VARCHAR")],
               [(n, n[len("attrs."):] if n.startswith("attrs.") else None) for n in sorted(names)], tmpdir)
    load_table(con, "policy_bands", [("ord", "BIGINT"), ("band", "VARCHAR"), ("from_value", D)],
               [(i, t.band, Decimal(t.from_)) for i, t in enumerate(policy.bands.thresholds)], tmpdir)
    load_table(con, "policy_premium_tiers", [("tier", "VARCHAR")], [(t,) for t in sorted(set(policy.premium_tiers))], tmpdir)
    load_table(con, "policy_budgets", [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("monthly_amount", D)],
               [(b.provider, b.provider_account_id, Decimal(b.monthly_amount)) for b in policy.pools.budgets], tmpdir)


def _load_scenarios(con, policy, tmpdir):
    D = "DECIMAL(38,12)"
    # phase 3: scenarios, alert thresholds and named exceptions (spec 4.1, 4.2, 7.7, 8)
    scenarios = list(policy.scenarios)
    load_table(con, "scn_def",
               [("ord", "BIGINT"), ("scenario_id", "VARCHAR"), ("has_caps", "BOOLEAN"), ("default_cap", D),
                ("cap_by", "VARCHAR"), ("has_envelopes", "BOOLEAN"), ("top_up", D)],
               [(i, sc.id, sc.person_caps is not None,
                 None if sc.person_caps is None or sc.person_caps.default is None else Decimal(sc.person_caps.default),
                 None if sc.person_caps is None else sc.person_caps.by,
                 sc.unit_envelopes is not None,
                 _top_up(sc.top_up_amount)) for i, sc in enumerate(scenarios)],
               tmpdir)
    load_table(con, "scn_cap_values", [("scenario_id", "VARCHAR"), ("value", "VARCHAR"), ("cap", D)],
               [(sc.id, v, Decimal(c)) for sc in scenarios if sc.person_caps is not None
                for v, c in sorted(sc.person_caps.values.items())], tmpdir)
    load_table(con, "scn_envelopes", [("scenario_id", "VARCHAR"), ("unit_key", "VARCHAR"), ("envelope", D)],
               [(sc.id, key, Decimal(v)) for sc in scenarios if sc.unit_envelopes is not None
                for key, v in sorted(sc.unit_envelopes.values.items())], tmpdir)
    load_table(con, "scn_fractions", [("scenario_id", "VARCHAR"), ("fraction", D)],
               [(sc.id, f) for sc in scenarios for f in sorted({Decimal(x) for x in sc.alert_fractions})], tmpdir)
    spike = policy.alerts.spike
    load_table(con, "policy_alerts",
               [("breaker", D), ("spike_multiplier", D), ("spike_floor", D), ("spike_trailing", "BIGINT"),
                ("spike_min_history", "BIGINT")],
               [(None if policy.alerts.daily_breaker is None else Decimal(policy.alerts.daily_breaker),
                 None if spike is None else Decimal(spike.multiplier),
                 None if spike is None else Decimal(spike.floor),
                 None if spike is None else spike.trailing_days,
                 None if spike is None else spike.min_history_days)], tmpdir)
    load_table(con, "policy_exceptions",
               [("ord", "BIGINT"), ("rule_id", "VARCHAR"), ("audience", "VARCHAR"), ("condition", "VARCHAR"),
                ("threshold_text", "VARCHAR"), ("threshold_money", D), ("threshold_count", "BIGINT")],
               [(i, r.id, r.audience, r.condition, r.threshold,
                 Decimal(r.threshold) if r.condition == "month_cost_ref_above" else None,
                 int(r.threshold) if r.condition != "month_cost_ref_above" else None)
                for i, r in enumerate(policy.named_exceptions)], tmpdir)


def _load_prices(con, prices, invoice_rows, tmpdir):
    D = "DECIMAL(38,12)"
    load_table(con, "price_models", [("model", "VARCHAR"), ("vendor", "VARCHAR"), ("family", "VARCHAR"), ("tier", "VARCHAR")],
               [(m.model, m.vendor, m.family, m.tier) for m in prices.models], tmpdir)
    token_rows = []
    for t in prices.token_prices:
        pm = t.per_million
        if pm is None:
            token_rows.append((t.provider, t.model, t.valid_from, False, None, None, None, None, None))
        else:
            write_5m = _per_token(pm.cache_write_5m)
            write_1h = _per_token(pm.cache_write_1h) if pm.cache_write_1h is not None else write_5m
            token_rows.append((t.provider, t.model, t.valid_from, True, _per_token(pm.input_uncached),
                               _per_token(pm.cache_read), write_5m, write_1h, _per_token(pm.output)))
    load_table(con, "price_tokens",
               [("provider", "VARCHAR"), ("model", "VARCHAR"), ("valid_from", "DATE"), ("priced", "BOOLEAN"),
                ("p_input", D), ("p_cache_read", D), ("p_write_5m", D), ("p_write_1h", D), ("p_output", D)],
               token_rows, tmpdir)
    load_table(con, "price_units", [("provider", "VARCHAR"), ("unit", "VARCHAR"), ("valid_from", "DATE"), ("price", D)],
               [(u.provider, u.unit, u.valid_from, Decimal(u.price)) for u in prices.unit_prices], tmpdir)
    load_table(con, "price_included", [("provider", "VARCHAR"), ("product", "VARCHAR")],
               [(i.provider, i.product) for i in prices.included], tmpdir)
    load_table(con, "price_seats",
               [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("valid_from", "DATE"), ("per_month", D),
                ("included_credits", D)],
               [(s.provider, s.provider_account_id, s.valid_from, Decimal(s.per_month),
                 None if s.included_credits is None else Decimal(s.included_credits)) for s in prices.seats], tmpdir)
    load_table(con, "invoice",
               [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("month", "VARCHAR"), ("kind", "VARCHAR"), ("amount", D)],
               [(r["provider"], r["provider_account_id"], r["month"], r["kind"], r["amount"]) for r in invoice_rows], tmpdir)


def _load_org(con, org, hasher, tmpdir):
    # organisation
    load_table(con, "org_units",
               [("unit_id", "VARCHAR"), ("valid_from", "DATE"), ("valid_to", "DATE"), ("dimension", "VARCHAR"),
                ("key", "VARCHAR"), ("name", "VARCHAR"), ("kind", "VARCHAR"), ("parent_id", "VARCHAR"),
                ("function", "VARCHAR"), ("cost_center", "VARCHAR"), ("head_key", "VARCHAR")],
               [(r["unit_id"], date.fromisoformat(r["valid_from"]),
                 date.fromisoformat(r["valid_to"]) if r["valid_to"] else None, r["dimension"], r["key"], r["name"],
                 r["kind"], r["parent_id"], r["function"], r["cost_center"],
                 hasher.person(r["head_person_id"]) if r["head_person_id"] else None) for r in org.units], tmpdir)
    load_table(con, "org_people",
               [("person_key", "VARCHAR"), ("valid_from", "DATE"), ("valid_to", "DATE"), ("unit_id", "VARCHAR"),
                ("manager_key", "VARCHAR"), ("function", "VARCHAR"), ("level", "VARCHAR"), ("cost_center", "VARCHAR"),
                ("population", "VARCHAR"), ("attrs", "VARCHAR")],
               [(hasher.person(r["person_id"]), date.fromisoformat(r["valid_from"]),
                 date.fromisoformat(r["valid_to"]) if r["valid_to"] else None, r["unit_id"],
                 hasher.person(r["manager_id"]) if r["manager_id"] else None, r["function"], r["level"],
                 r["cost_center"], r["population"], r["_attrs"]) for r in org.people], tmpdir)
    load_table(con, "org_identities",
               [("person_key", "VARCHAR"), ("provider", "VARCHAR"), ("method", "VARCHAR"), ("actor_kind", "VARCHAR"),
                ("actor_key", "VARCHAR"), ("valid_from", "DATE"), ("valid_to", "DATE")],
               [(hasher.person(r["person_id"]) if r["person_id"] else None, r["provider"], r["method"],
                 r["actor_kind"], hasher.actor(r["provider"], r["actor_kind"], r["actor_id"]),
                 date.fromisoformat(r["valid_from"]), date.fromisoformat(r["valid_to"]) if r["valid_to"] else None)
                for r in org.identities], tmpdir)
    load_table(con, "org_directory",
               [("person_key", "VARCHAR"), ("given_name", "VARCHAR"), ("family_name", "VARCHAR"), ("email", "VARCHAR")],
               [(hasher.person(r["person_id"]), r["given_name"], r["family_name"], r["email"]) for r in org.directory],
               tmpdir)


def _load_silver(con, silver, hasher, tmpdir):
    D = "DECIMAL(38,12)"
    # silver
    def utc_day(rec):
        return rec.pulled_at.astimezone(_UTC).date()

    ak = hasher.actor
    usage = silver.current["usage"]
    load_table(
        con, "silver_usage",
        [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("scope", "VARCHAR"), ("grain", "VARCHAR"),
         ("period_start", "DATE"), ("actor_kind", "VARCHAR"), ("actor_key", "VARCHAR"), ("product", "VARCHAR"),
         ("model", "VARCHAR"), ("tokens_input_uncached", "BIGINT"), ("tokens_cache_read", "BIGINT"),
         ("tokens_cache_write", "BIGINT"), ("tokens_cache_write_5m", "BIGINT"), ("tokens_cache_write_1h", "BIGINT"),
         ("tokens_input_total", "BIGINT"), ("tokens_output", "BIGINT"), ("requests", "BIGINT"),
         ("ai_credit", D), ("cost", D), ("currency", "VARCHAR"), ("cost_basis", "VARCHAR"), ("is_final", "BOOLEAN"),
         ("batch_id", "VARCHAR"), ("pulled_date", "DATE")],
        ((r.provider, r.provider_account_id, r.scope, r.grain, r.period_start, r.actor_kind, ak(r.provider, r.actor_kind, r.actor_id),
          r.product, r.model, r.tokens_input_uncached, r.tokens_cache_read, r.tokens_cache_write,
          r.tokens_cache_write_5m, r.tokens_cache_write_1h, r.tokens_input_total, r.tokens_output, r.requests,
          r.quantities.get("ai_credit"), r.cost, r.currency, r.cost_basis, r.is_final, r.batch_id, utc_day(r))
         for r in usage), tmpdir)
    load_table(
        con, "silver_usage_batches",
        [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("scope", "VARCHAR"), ("grain", "VARCHAR"),
         ("period_start", "DATE"), ("actor_kind", "VARCHAR"), ("actor_key", "VARCHAR"), ("product", "VARCHAR"),
         ("model", "VARCHAR"), ("batch_id", "VARCHAR"), ("is_final", "BOOLEAN"), ("content_hash", "VARCHAR")],
        ((r.provider, r.provider_account_id, r.scope, r.grain, r.period_start, r.actor_kind, ak(r.provider, r.actor_kind, r.actor_id),
          r.product, r.model, r.batch_id, r.is_final, h) for r, h in silver.usage_batches), tmpdir)
    load_table(
        con, "silver_activity",
        [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("scope", "VARCHAR"), ("grain", "VARCHAR"),
         ("period_start", "DATE"), ("actor_kind", "VARCHAR"), ("actor_key", "VARCHAR"), ("product", "VARCHAR"),
         ("sessions", "BIGINT"), ("messages", "BIGINT"), ("requests", "BIGINT"), ("suggestions_shown", "BIGINT"),
         ("suggestions_accepted", "BIGINT"), ("lines_added", "BIGINT"), ("lines_removed", "BIGINT"),
         ("edits_accepted", "BIGINT"), ("edits_rejected", "BIGINT"), ("commits", "BIGINT"),
         ("pull_requests", "BIGINT"), ("pulled_date", "DATE")],
        ((r.provider, r.provider_account_id, r.scope, r.grain, r.period_start, r.actor_kind, ak(r.provider, r.actor_kind, r.actor_id),
          r.product, r.sessions, r.messages, r.requests, r.suggestions_shown, r.suggestions_accepted, r.lines_added,
          r.lines_removed, r.edits_accepted, r.edits_rejected, r.commits, r.pull_requests, utc_day(r))
         for r in silver.current["activity"]), tmpdir)
    load_table(
        con, "silver_seats",
        [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("scope", "VARCHAR"), ("grain", "VARCHAR"),
         ("period_start", "DATE"), ("seats", "BIGINT"), ("pulled_date", "DATE")],
        ((r.provider, r.provider_account_id, r.scope, r.grain, r.period_start, r.seats, utc_day(r))
         for r in silver.current["seats"]), tmpdir)
    load_table(
        con, "silver_assignments",
        [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("scope", "VARCHAR"), ("grain", "VARCHAR"),
         ("period_start", "DATE"), ("actor_kind", "VARCHAR"), ("actor_key", "VARCHAR"), ("pulled_date", "DATE")],
        ((r.provider, r.provider_account_id, r.scope, r.grain, r.period_start, r.actor_kind, ak(r.provider, r.actor_kind, r.actor_id),
          utc_day(r)) for r in silver.current["seat_assignments"]), tmpdir)
    load_table(
        con, "silver_org_totals",
        [("provider", "VARCHAR"), ("provider_account_id", "VARCHAR"), ("scope", "VARCHAR"), ("grain", "VARCHAR"),
         ("period_start", "DATE"), ("product", "VARCHAR"), ("model", "VARCHAR"), ("cost", D), ("currency", "VARCHAR"),
         ("cost_basis", "VARCHAR"), ("ai_credit", D), ("is_final", "BOOLEAN"), ("extra_discount_quantity", D),
         ("extra_net_quantity", D), ("pulled_date", "DATE")],
        ((r.provider, r.provider_account_id, r.scope, r.grain, r.period_start, r.product, r.model, r.cost, r.currency, r.cost_basis,
          r.quantities.get("ai_credit"), r.is_final, _extra(r, "discount_quantity"), _extra(r, "net_quantity"),
          utc_day(r)) for r in silver.current["org_totals"]), tmpdir)


def load_all(con, policy, prices, org, silver, invoice_rows, hasher, tmpdir):
    _load_policy(con, policy, tmpdir)
    _load_scenarios(con, policy, tmpdir)
    _load_prices(con, prices, invoice_rows, tmpdir)
    _load_org(con, org, hasher, tmpdir)
    _load_silver(con, silver, hasher, tmpdir)


def _top_up(text):
    """The top-up amount of a scenario; "0" means no top-up (change-list finding 25: null top_ups and top_up_total)."""
    if text is None or Decimal(text) == 0:
        return None
    return Decimal(text)


def _extra(rec, name):
    value = rec.extras.get(name)
    return None if value is None else Decimal(value)


def read_invoice(path, currency):
    """Rows of the invoice CSV (spec 3.5). Raises InputError."""
    columns = ["provider", "provider_account_id", "month", "currency", "kind", "amount"]
    try:
        handle = open(path, encoding="utf-8", newline="")  # noqa: SIM115 - opened apart so only the open itself reads as "cannot read"; the with below closes it
    except OSError as exc:
        raise InputError("%s: cannot read (%s)" % (path, exc)) from exc
    rows, seen = [], set()
    with handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != columns:
            raise InputError("%s:1: header must be %s" % (path, ",".join(columns)))
        for line, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(columns):
                raise InputError("%s:%d: expected %d fields" % (path, line, len(columns)))
            item = dict(zip(columns, row, strict=True))
            if item["currency"] != currency:
                raise InputError("%s:%d: currency %s differs from the reference currency %s" % (path, line, item["currency"], currency))
            if item["kind"] not in ("usage", "seats"):
                raise InputError("%s:%d: kind must be usage or seats" % (path, line))
            try:
                amount = Decimal(item["amount"])
            except Exception as exc:
                raise InputError("%s:%d: amount is not a decimal" % (path, line)) from exc
            if -amount.as_tuple().exponent > MAX_DECIMALS:
                raise InputError("%s:%d: amount has more than %d decimals" % (path, line, MAX_DECIMALS))
            key = (item["provider"], item["provider_account_id"], item["month"], item["kind"])
            if key in seen:
                raise InputError("%s:%d: duplicate (provider, provider_account_id, month, kind)" % (path, line))
            seen.add(key)
            item["amount"] = amount
            rows.append(item)
    return rows
