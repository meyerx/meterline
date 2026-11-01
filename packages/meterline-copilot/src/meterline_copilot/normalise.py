"""Normaliser: reads raw files only (never the network) and emits meterline
records (spec v2 section 6.4). Money and credits are ``Decimal`` end to end:
response bodies are parsed with ``parse_float=Decimal``.

Nothing here reads the wall clock; ``pulled_at`` is passed in. Besides the raw
files of the run, two rules read earlier raw files of the data directory, never
a file stamped after the run's ``pulled_at`` (so a rebuild sees what the
original run saw): the seat snapshot of a day combines every raw file of that
day (6.4 rule 8), and a person's id is looked up in the reports and seat lists
of the month the person's billing was read for (6.4 rule 5).
"""

import gzip
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from meterline.published import drop_unchanged as drop_unchanged
from meterline.published import load_published as load_published_kinds
from meterline.records import (
    ActivityRecord, OrgTotal, SeatAssignment, SeatSnapshot, UsageRecord, batch_failures,
)
from meterline.run import UnknownFields, note_unknown_fields

logger = logging.getLogger("meterline_copilot.normalise")

PROVIDER = "github_copilot"
CURRENCY = "USD"
CREDIT_PRICE = Decimal("0.01")  # USD per AI credit, fixed by GitHub's documentation
AI_CREDITS_FROM = date(2026, 6, 19)  # ai_credits_used exists in the per-user rows from this day; older rows may lack it
# token-carrying families of a per-user row that no record is built from; their sums are counted in the run manifest
UNREAD_TOKEN_FAMILIES = ("totals_by_client", "totals_by_vscode_agent", "totals_by_3rd_party_agent")
TOKEN_SUMS = ("prompt_tokens_sum", "output_tokens_sum")
DAY_FINAL_AFTER_DAYS = 4
MONTH_FINAL_AFTER_DAYS = 7
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

KINDS = {
    "usage": UsageRecord,
    "activity": ActivityRecord,
    "seats": SeatSnapshot,
    "seat_assignments": SeatAssignment,
    "org_totals": OrgTotal,
}

FEATURE_FIELDS = (
    "code_generation_activity_count", "code_acceptance_activity_count", "loc_added_sum", "loc_deleted_sum",
    "user_initiated_interaction_count",
)
DISTINCT_COUNTERS = (
    ("distinct_custom_agent_use_count", "custom_agents_used"),
    ("distinct_mcp_use_count", "mcp_servers_used"),
    ("distinct_plugin_use_count", "plugins_used"),
    ("distinct_skill_use_count", "skills_used"),
    ("distinct_slash_cmd_use_count", "slash_commands_used"),
)
NO_MODEL = "(no model)"


# The shapes the connector knows (docs/api-shapes/copilot), for the unknown-field report. A dict lists the known keys,
# a one-item list describes the items of a list, and None is a value that is not looked into (see
# meterline.run.note_unknown_fields). The seat's assignee and team objects and the arrays that were empty in the
# transcribed example (totals_by_client, totals_by_language_model, totals_by_model_feature) are not looked into.
def _keys(*names: str, **inner: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {name: None for name in names}
    out.update(inner)
    return out


_TOKEN_USAGE = _keys("avg_tokens_per_request", "output_tokens_sum", "prompt_tokens_sum")
_SURFACE = _keys("prompt_count", "request_count", "session_count", token_usage=_TOKEN_USAGE,
                 last_known_cli_version=_keys("cli_version", "sampled_at"))
_ACTIVITY_SUMS = ("code_acceptance_activity_count", "code_generation_activity_count", "loc_added_sum", "loc_deleted_sum",
                  "loc_suggested_to_add_sum", "loc_suggested_to_delete_sum")
_REPORT_ROW = dict(
    totals_by_3rd_party_agent=[_keys("agent_id", "agent_name", "session_count", "user_initiated_interaction_count")],
    totals_by_cli=_SURFACE,
    totals_by_client=[None],
    totals_by_copilot_app=_SURFACE,
    totals_by_custom_agent=[_keys("custom_agent", "interaction_count")],
    totals_by_feature=[_keys(*_ACTIVITY_SUMS, "feature", "user_initiated_interaction_count")],
    totals_by_ide=[_keys(*_ACTIVITY_SUMS, "ide", "user_initiated_interaction_count",
                         last_known_ide_version=_keys("ide_version", "sampled_at"),
                         last_known_plugin_version=_keys("plugin", "plugin_version", "sampled_at"))],
    totals_by_language_feature=[_keys(*_ACTIVITY_SUMS, "feature", "language")],
    totals_by_language_model=[None],
    totals_by_mcp=[_keys("interaction_count", "mcp")],
    totals_by_model_feature=[None],
    totals_by_plugin=[_keys("interaction_count", "plugin")],
    totals_by_skill=[_keys("interaction_count", "skill")],
    totals_by_slash_cmd=[_keys("interaction_count", "slash_cmd")],
    totals_by_vscode_agent=_keys("session_count", "total_user_messages"),
)
_COMMON_ROW = ("day", "enterprise_id", "user_initiated_interaction_count", *_ACTIVITY_SUMS,
               *(source for source, _counter in DISTINCT_COUNTERS))
_ACTIVE_USERS = (
    "daily_active_cli_users",
    "daily_active_copilot_app_users",
    "daily_active_copilot_cloud_agent_users",
    "daily_active_copilot_code_review_users",
    "daily_active_users",
    "daily_active_vscode_agent_users",
    "daily_passive_copilot_code_review_users",
    "monthly_active_agent_users",
    "monthly_active_chat_users",
    "monthly_active_copilot_cloud_agent_users",
    "monthly_active_copilot_code_review_users",
    "monthly_active_users",
    "monthly_active_vscode_agent_users",
    "monthly_passive_copilot_code_review_users",
    "weekly_active_copilot_cloud_agent_users",
    "weekly_active_copilot_code_review_users",
    "weekly_active_users",
    "weekly_active_vscode_agent_users",
    "weekly_passive_copilot_code_review_users",
)
KNOWN_SHAPES: Dict[str, Any] = {
    "users_1_day": _keys(
        *_COMMON_ROW, "ai_credits_used", "organization_id", "user_id", "user_login",
        "used_agent", "used_chat", "used_cli", "used_copilot_app", "used_copilot_cloud_agent", "used_copilot_code_review_active",
        "used_copilot_code_review_passive", "used_copilot_coding_agent", "used_vscode_agent",
        ai_adoption_phase=_keys("phase", "phase_number", "version"), **_REPORT_ROW),
    "aggregate_1_day": _keys(
        *_COMMON_ROW, *_ACTIVE_USERS,
        pull_requests=_keys(
            "median_minutes_to_merge", "median_minutes_to_merge_copilot_authored", "median_minutes_to_merge_copilot_reviewed",
            "total_applied_suggestions", "total_copilot_applied_suggestions", "total_copilot_suggestions", "total_created",
            "total_created_by_copilot", "total_merged", "total_merged_created_by_copilot", "total_merged_reviewed_by_copilot",
            "total_reviewed", "total_reviewed_by_copilot", "total_suggestions"),
        totals_by_ai_adoption_phase=[_keys(
            "avg_code_acceptance_activities", "avg_code_generation_activities", "avg_loc_added", "avg_loc_deleted",
            "avg_pull_requests_created", "avg_pull_requests_median_minutes_to_merge", "avg_pull_requests_merged",
            "avg_pull_requests_minutes_to_review", "avg_pull_requests_review_cycles", "avg_pull_requests_reviewed",
            "avg_user_initiated_interactions", "phase", "phase_number", "total_engaged_users", "total_pull_requests_merged",
            "users_in_phase_28d")],
        **_REPORT_ROW),
    "seats": _keys("total_seats", seats=[_keys(
        "assignee", "assigning_team", "created_at", "last_activity_at", "last_activity_editor", "last_authenticated_at",
        "pending_cancellation_date", "plan_type", "updated_at")]),
    "seat_summary": _keys("plan_type", "seat_management_setting", seat_breakdown=_keys(
        "active_this_cycle", "added_this_cycle", "inactive_this_cycle", "pending_cancellation", "pending_invitation", "total")),
}
_BILLING = _keys("organization", "enterprise", "user", timePeriod=_keys("day", "month", "year"), usageItems=[_keys(
    "discountAmount", "discountQuantity", "grossAmount", "grossQuantity", "model", "netAmount", "netQuantity", "pricePerUnit",
    "product", "sku", "unitType")])
KNOWN_SHAPES["billing_day"] = _BILLING
KNOWN_SHAPES["billing_user_month"] = _BILLING

UNIT_NAMES = {
    "credits": "ai_credit", "ai-credits": "ai_credit", "ai_credits": "ai_credit", "requests": "premium_request",
}


def parse_timestamp(text: str) -> datetime:
    value = str(text)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_raw(path: Path) -> Dict[str, Any]:
    with gzip.open(Path(path), "rb") as handle:
        raw = json.loads(handle.read().decode("utf-8"))
    if "provider_account_id" not in raw and "org_id" in raw:  # compatibility: a raw file written before the MF-13 rename
        raw["provider_account_id"] = raw.pop("org_id")
    return raw


def stamp_of(path: Path) -> datetime:
    return datetime.strptime(Path(path).name.split(".")[0], STAMP_FORMAT).replace(tzinfo=timezone.utc)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def month_bounds(year: int, month: int) -> Tuple[date, date]:
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def is_final_day(day: date, pulled_at: datetime) -> bool:
    """True when the day ended at least four full UTC days before the pull."""
    ended = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
    return pulled_at.astimezone(timezone.utc) >= ended + timedelta(days=DAY_FINAL_AFTER_DAYS)


def is_final_month(day: date, pulled_at: datetime) -> bool:
    """True when the month of ``day`` ended more than seven days before the pull."""
    _first, last = month_bounds(day.year, day.month)
    return (pulled_at.astimezone(timezone.utc).date() - last).days > MONTH_FINAL_AFTER_DAYS


def normal_name(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower())


def unit_name(raw: Any) -> Tuple[str, bool]:
    """(unit, known) for a billing ``unitType`` (6.4 rule 6)."""
    key = str(raw if raw is not None else "").strip().lower()
    if key in UNIT_NAMES:
        return UNIT_NAMES[key], True
    name = normal_name(key).strip("_")
    if not name or not name[0].isalpha():
        name = "unit_" + name if name else "unknown"
    return name, False


# ---- parsing ----------------------------------------------------------------------


def parse_ndjson(text: str, where: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line, parse_float=Decimal)
        except ValueError:
            raise ValueError("%s: line %d is not JSON" % (where, number)) from None
        if not isinstance(value, dict):
            raise ValueError("%s: line %d is not an object" % (where, number))
        out.append(value)
    return out


def _body(page: Dict[str, Any]) -> Any:
    return json.loads(page["body"], parse_float=Decimal)


class ShapeError(ValueError):
    """A response lacks a field the documented shape promises, or holds it with
    the wrong type. The message names the endpoint and the field, never a
    value. A run that meets one publishes nothing (exit 2)."""

    def __init__(self, endpoint: str, field_name: str, problem: str) -> None:
        self.endpoint = endpoint
        self.field_name = field_name
        super().__init__("%s%s %s" % (endpoint + ": " if endpoint else "", field_name, problem))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, Decimal)) and not isinstance(value, bool)


def as_int(value: Any, where: str, endpoint: str = "") -> int:
    if not _is_number(value) or not Decimal(value).is_finite() or Decimal(value) != Decimal(value).to_integral_value() or value < 0:
        raise ShapeError(endpoint, where, "is not a whole number")
    return int(value)


def opt_int(row: Dict[str, Any], name: str, where: str) -> Optional[int]:
    value = row.get(name)
    return None if value is None else as_int(value, where + "." + name)


def as_decimal(value: Any, where: str, endpoint: str = "") -> Decimal:
    if not _is_number(value) or not Decimal(value).is_finite():
        raise ShapeError(endpoint, where, "is not a number")
    return Decimal(value)


def _present(row: Any, name: str, endpoint: str, where: str = "") -> Any:
    """``row[name]``, which must exist and not be null."""
    path = where + "." + name if where else name
    if not isinstance(row, dict) or name not in row:
        raise ShapeError(endpoint, path, "is missing")
    if row[name] is None:
        raise ShapeError(endpoint, path, "is null")
    return row[name]


def need_int(row: Any, name: str, endpoint: str, where: str = "") -> int:
    """A required whole-number field."""
    return as_int(_present(row, name, endpoint, where), where + "." + name if where else name, endpoint)


def need_decimal(row: Any, name: str, endpoint: str, where: str = "") -> Decimal:
    """A required number field."""
    return as_decimal(_present(row, name, endpoint, where), where + "." + name if where else name, endpoint)


def report_rows(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, text in enumerate(raw.get("files") or []):
        rows.extend(parse_ndjson(text, "%s %s file %d" % (raw["endpoint"], raw["day"], index + 1)))
    return rows


def seat_items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in raw["pages"]:
        body = _body(page)
        if not isinstance(body, dict) or not isinstance(body.get("seats"), list):
            raise ValueError("seats page has no seats list")
        for seat in body["seats"]:
            if not isinstance(seat, dict) or "assignee" not in seat:
                raise ValueError("a seat has no assignee")
            if seat["assignee"] is None:
                continue  # the schema allows a seat nobody holds; unheld_seats counts it
            if not isinstance(seat["assignee"], dict):
                raise ValueError("a seat has no assignee")
            out.append(seat)
    return out


def unheld_seats(raw: Dict[str, Any]) -> int:
    """How many seats of a seat-list raw file have a null ``assignee``."""
    return sum(1 for page in raw["pages"] for seat in (_body(page) or {}).get("seats", [])
               if isinstance(seat, dict) and "assignee" in seat and seat["assignee"] is None)


def _paths(data_dir: Path, endpoint: str, label_prefix: str = "") -> Iterable[Path]:
    folder = Path(data_dir) / "raw" / endpoint
    if not folder.is_dir():
        return []
    found: List[Path] = []
    for sub in sorted(folder.iterdir()):
        if sub.is_dir() and sub.name.startswith(label_prefix):
            found.extend(sorted(sub.glob("*.json.gz")))
    return found


def month_people(data_dir: Path, year: int, month: int, upto: datetime) -> Dict[str, Optional[str]]:
    """Logins seen in the month, with the user id each one carries: logins of
    the stored per-user report rows dated in the month, and of the seat lists
    pulled in the month (raw files stamped after ``upto`` are ignored). The id
    comes from a report row, else from a seat's assignee, else it is None.
    Keys keep the first spelling seen; a login differing only in case is one
    person."""
    prefix = "%04d-%02d-" % (year, month)
    people: Dict[str, Tuple[str, Optional[str]]] = {}
    for path in _paths(data_dir, "users_1_day", prefix):
        if stamp_of(path) > upto:
            continue
        for row in report_rows(read_raw(path)):
            login = row.get("user_login")
            if isinstance(login, str) and login:
                uid = row.get("user_id")
                have = people.get(login.lower())
                if have is None or (have[1] is None and uid is not None):
                    people[login.lower()] = (login, None if uid is None else str(uid))
    for path in _paths(data_dir, "seats", prefix):
        if stamp_of(path) > upto:
            continue
        for seat in seat_items(read_raw(path)):
            who = seat["assignee"]
            login = who.get("login")
            if isinstance(login, str) and login:
                uid = who.get("id")
                have = people.get(login.lower())
                if have is None:
                    people[login.lower()] = (login, None if uid is None else str(uid))
                elif have[1] is None and uid is not None:
                    people[login.lower()] = (have[0], str(uid))
    return {login: uid for login, uid in people.values()}


# ---- billing items --------------------------------------------------------------------


@dataclass
class Group:
    """Billing items of one (product, model), summed (6.4 rules 5 and 7)."""

    quantities: Dict[str, Decimal] = field(default_factory=dict)
    gross: Decimal = Decimal(0)
    discount: Decimal = Decimal(0)
    net: Decimal = Decimal(0)
    discount_quantity: Decimal = Decimal(0)
    net_quantity: Decimal = Decimal(0)
    prices: List[Decimal] = field(default_factory=list)


def group_items(body: Any, endpoint: str, unknown_units: List[str]) -> Dict[Tuple[str, str], Group]:
    """Sum the billing items by (product, model). The six quantity and amount
    fields of an item are required numbers; a missing one is never read as 0."""
    if not isinstance(body, dict) or not isinstance(body.get("usageItems"), list):
        raise ShapeError(endpoint, "usageItems", "is missing or not a list")
    groups: Dict[Tuple[str, str], Group] = {}
    for entry in body["usageItems"]:
        if not isinstance(entry, dict):
            raise ShapeError(endpoint, "usageItems", "holds an item that is not an object")
        key = (normal_name(entry.get("product") or ""), str(entry.get("model") or ""))
        unit, known = unit_name(entry.get("unitType"))
        if not known:
            unknown_units.append(unit)
        group = groups.setdefault(key, Group())
        gross_q = need_decimal(entry, "grossQuantity", endpoint)
        group.quantities[unit] = group.quantities.get(unit, Decimal(0)) + gross_q
        group.gross += need_decimal(entry, "grossAmount", endpoint)
        group.discount += need_decimal(entry, "discountAmount", endpoint)
        group.net += need_decimal(entry, "netAmount", endpoint)
        group.discount_quantity += need_decimal(entry, "discountQuantity", endpoint)
        group.net_quantity += need_decimal(entry, "netQuantity", endpoint)
        if entry.get("pricePerUnit") is not None:
            group.prices.append(as_decimal(entry["pricePerUnit"], "pricePerUnit", endpoint))
    return groups


def _extras(group: Group) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "discount_quantity": group.discount_quantity, "net_quantity": group.net_quantity, "net_amount": group.net,
    }
    if group.prices and len(set(group.prices)) == 1:
        out["price_per_unit"] = group.prices[0]
    return out


# ---- result ---------------------------------------------------------------------------


@dataclass
class NormalisedRun:
    usage: List[UsageRecord] = field(default_factory=list)
    activity: List[ActivityRecord] = field(default_factory=list)
    seats: List[SeatSnapshot] = field(default_factory=list)
    seat_assignments: List[SeatAssignment] = field(default_factory=list)
    org_totals: List[OrgTotal] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    unknown_products: List[str] = field(default_factory=list)
    unknown_models: List[str] = field(default_factory=list)
    unknown_units: List[str] = field(default_factory=list)
    feature_sum_mismatch: List[str] = field(default_factory=list)
    aggregate_missing: List[str] = field(default_factory=list)
    seats_without_assignee: int = 0
    ai_credits_absent: int = 0
    unread_token_families: Dict[str, int] = field(
        default_factory=lambda: {"%s.%s" % (f, m): 0 for f in UNREAD_TOKEN_FAMILIES for m in TOKEN_SUMS})
    row_counts: Dict[str, int] = field(default_factory=dict)
    unknown_fields: UnknownFields = field(default_factory=dict)  # (endpoint, key path) -> count of keys the connector does not know
    raw_credits: Dict[date, Decimal] = field(default_factory=dict)
    raw_tokens: Dict[Tuple[str, date], Decimal] = field(default_factory=dict)
    raw_features: Dict[Tuple[date, str], Dict[str, int]] = field(default_factory=dict)
    provider_account_id: str = ""

    def records(self, kind: str) -> List[Any]:
        return getattr(self, kind)


# ---- mapping ----------------------------------------------------------------------------


def _usage_base(org: str, day: date, actor_id: str, login: Optional[str], product: str, pulled_at: datetime,
                batch_id: str) -> Dict[str, Any]:
    return dict(
        provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
        actor_kind="user", actor_id=actor_id, actor_email=None, actor_login=login, actor_deleted=None,
        product=product, model="", model_provider=None,
        is_final=is_final_day(day, pulled_at), cost_only=False,
        pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
    )


def _count_unread_tokens(run: NormalisedRun, row: Dict[str, Any], endpoint: str) -> None:
    """Add the prompt and output token sums of the families no record is built from to the run's counters, so that the
    tokens missing from the records are visible per run. One level only: the per-model copies inside
    ``totals_by_client`` are not added again."""
    for family in UNREAD_TOKEN_FAMILIES:
        value = row.get(family)
        for item in value if isinstance(value, list) else [value]:
            usage = item.get("token_usage") if isinstance(item, dict) else None
            if not isinstance(usage, dict):
                continue
            for name in TOKEN_SUMS:
                if usage.get(name) is not None:
                    run.unread_token_families["%s.%s" % (family, name)] += as_int(usage[name], family + "." + name, endpoint)


def _map_user_row(run: NormalisedRun, org: str, day: date, row: Dict[str, Any], pulled_at: datetime, batch_id: str) -> None:
    where = day.isoformat()
    endpoint = "users_1_day " + where
    if row.get("user_id") is None:
        raise ShapeError(endpoint, "user_id", "is missing")
    if str(row.get("day") or where)[:10] != where:
        raise ShapeError(endpoint, "day", "is outside the report day")
    uid = str(as_int(row["user_id"], "user_id", endpoint))
    login = row.get("user_login")
    login = str(login) if login else None
    tag = "%s|%s" % (where, _hash(uid))

    # 1. daily credits
    # (spec 6.4 rule 1 once wrote the record "only when ai_credits_used is present"; F-06: the field is required)
    # (before AI_CREDITS_FROM the field may be absent: no credit record, counted in the manifest)
    if day < AI_CREDITS_FROM and row.get("ai_credits_used") is None:
        run.ai_credits_absent += 1
    else:
        amount = need_decimal(row, "ai_credits_used", endpoint)
        run.raw_credits[day] = run.raw_credits.get(day, Decimal(0)) + amount
        cost = amount * CREDIT_PRICE
        run.usage.append(UsageRecord(
            **_usage_base(org, day, uid, login, "copilot", pulled_at, batch_id),
            quantities={"ai_credit": amount}, cost=cost, cost_list=cost, currency=CURRENCY, cost_basis="consumption",
        ))
    _count_unread_tokens(run, row, endpoint)

    # 2. CLI and app tokens
    surfaces = {}
    for key, product in (("totals_by_cli", "copilot_cli"), ("totals_by_copilot_app", "copilot_app")):
        block = row.get(key)
        if not isinstance(block, dict):
            continue  # absent when the surface is unused (spec 6.2)
        surfaces[product] = block
        usage = _present(block, "token_usage", endpoint, key)
        total = need_int(usage, "prompt_tokens_sum", endpoint, key + ".token_usage")
        out = need_int(usage, "output_tokens_sum", endpoint, key + ".token_usage")
        requests = need_int(block, "request_count", endpoint, key)
        run.usage.append(UsageRecord(
            **_usage_base(org, day, uid, login, product, pulled_at, batch_id),
            tokens_input_total=total, tokens_output=out, requests=requests,
        ))
        run.raw_tokens[(product, day)] = run.raw_tokens.get((product, day), Decimal(0)) + total

    # 3. activity, per feature, and one record of distinct counts
    features = row.get("totals_by_feature") or []
    if not isinstance(features, list):
        raise ValueError("totals_by_feature of %s is not a list" % where)
    sums = {name: 0 for name in FEATURE_FIELDS}
    for entry in features:
        if not isinstance(entry, dict) or not entry.get("feature"):
            raise ValueError("a totals_by_feature entry of %s has no feature" % where)
        name = str(entry["feature"])
        for field_name in FEATURE_FIELDS:
            sums[field_name] += opt_int(entry, field_name, "feature " + name) or 0
        sessions = requests = None
        block = surfaces.get(name)
        if block is not None:
            sessions = opt_int(block, "session_count", name)
            requests = opt_int(block, "request_count", name)
        counters = {}
        for src, dst in (("loc_suggested_to_add_sum", "lines_suggested_to_add"),
                         ("loc_suggested_to_delete_sum", "lines_suggested_to_delete")):
            value = opt_int(entry, src, "feature " + name)
            if value is not None:
                counters[dst] = value
        run.activity.append(ActivityRecord(
            provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
            actor_kind="user", actor_id=uid, actor_email=None, actor_login=login, product=name,
            sessions=sessions, requests=requests,
            messages=opt_int(entry, "user_initiated_interaction_count", name),
            suggestions_shown=opt_int(entry, "code_generation_activity_count", name),
            suggestions_accepted=opt_int(entry, "code_acceptance_activity_count", name),
            lines_added=opt_int(entry, "loc_added_sum", name), lines_removed=opt_int(entry, "loc_deleted_sum", name),
            counters=counters, pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
        ))
    distinct = {}
    for src, dst in DISTINCT_COUNTERS:
        value = opt_int(row, src, "row")
        if value is not None:
            distinct[dst] = value
    if distinct:
        run.activity.append(ActivityRecord(
            provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
            actor_kind="user", actor_id=uid, actor_email=None, actor_login=login, product="copilot",
            counters=distinct, pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
        ))

    # 4. the features add up to the row's own counters
    run.raw_features[(day, uid)] = sums
    for field_name in FEATURE_FIELDS:
        top = opt_int(row, field_name, "row")
        if top is not None and top != sums[field_name]:
            run.feature_sum_mismatch.append(tag)
            break


def _active_counts(raw: Dict[str, Any], day: date) -> Optional[Tuple[Optional[int], Optional[int], Optional[int]]]:
    """Daily, weekly and monthly active users of ``day`` from an aggregate
    report: the ``day_totals`` entry of that day, else the row's top level."""
    names = ("daily_active_users", "weekly_active_users", "monthly_active_users")
    for row in report_rows(raw):
        candidates: List[Dict[str, Any]] = []
        totals = row.get("day_totals")
        if isinstance(totals, list):
            candidates.extend(e for e in totals if isinstance(e, dict) and str(e.get("day"))[:10] == day.isoformat())
        if not candidates and any(n in row for n in names) and str(row.get("day") or day.isoformat())[:10] == day.isoformat():
            candidates.append(row)
        for entry in candidates:
            return tuple(opt_int(entry, n, "aggregate") for n in names)  # type: ignore[return-value]
    return None


def _latest(data_dir: Path, endpoint: str, label: str, upto: datetime) -> Optional[Dict[str, Any]]:
    best: Optional[Path] = None
    for path in _paths(data_dir, endpoint, label):
        if path.parent.name != label or stamp_of(path) > upto:
            continue
        if best is None or stamp_of(path) >= stamp_of(best):
            best = path
    return None if best is None else read_raw(best)


def _note_unknown(found: UnknownFields, raw: Dict[str, Any]) -> None:
    """Count the keys of the answers of a raw file that ``KNOWN_SHAPES`` does not list. A report file holds the NDJSON
    lines behind the download links, any other file holds the pages. A body that cannot be read is left to the
    normaliser, which names it."""
    endpoint = raw.get("endpoint")
    shape = KNOWN_SHAPES.get(endpoint)
    if shape is None:
        return
    try:
        if endpoint in ("users_1_day", "aggregate_1_day"):
            bodies = report_rows(raw)
        else:
            bodies = [_body(page) for page in raw.get("pages") or [] if page.get("status", 200) == 200]
    except (KeyError, ValueError):
        return
    for body in bodies:
        note_unknown_fields(found, str(endpoint), body, shape)


def _single_org(run: NormalisedRun, raws: List[Tuple[Path, Dict[str, Any]]]) -> int:
    """Set ``run.provider_account_id`` from the raw files (one organization per run); returns the file count."""
    n_files = 0
    for _path, raw in raws:
        n_files += 1
        org = raw["provider_account_id"]
        if run.provider_account_id and run.provider_account_id != org:
            raise ValueError("raw files of two organizations in one run")
        run.provider_account_id = org
    return n_files


def _map_reports(
    run: NormalisedRun, org: str, raws: List[Tuple[Path, Dict[str, Any]]], pulled_at: datetime, batch_id: str
) -> set:
    """Map the per-user report files into records; returns the days that need a seat snapshot."""
    snapshot_days: set = set()
    for _path, raw in raws:
        endpoint = raw["endpoint"]
        if endpoint == "users_1_day":
            day = date.fromisoformat(raw["day"])
            for row in report_rows(raw):
                _map_user_row(run, org, day, row, pulled_at, batch_id)
        elif endpoint == "aggregate_1_day":
            snapshot_days.add(date.fromisoformat(raw["day"]))
        elif endpoint in ("seats", "seat_summary"):
            snapshot_days.add(parse_timestamp(raw["pulled_at"]).date())
        elif endpoint in ("billing_day", "billing_user_month"):
            pass
        else:
            raise ValueError("unknown endpoint in raw file: " + str(endpoint))
    return snapshot_days


def _note_unknowns(run: NormalisedRun, product: str, model: str, seen_products: set, seen_models: set) -> None:
    if model and model not in seen_models:
        run.unknown_models.append(model)
    if product not in seen_products:
        run.unknown_products.append(product)


def _billing_day(
    run: NormalisedRun, org: str, raw: Dict[str, Any], body: Any, year: int, month: int,
    pulled_at: datetime, batch_id: str, seen_products: set, seen_models: set,
) -> None:
    day = date(year, month, int(raw["day"]))
    day_groups = group_items(body, "billing_day %04d-%02d-%02d" % (year, month, day.day), run.unknown_units)
    with_model = {p for (p, m) in day_groups if m}
    for (product, model), group in sorted(day_groups.items()):
        if not model and product in with_model:
            model = NO_MODEL  # an all-models row may not sit beside per-model rows (3.6)
        run.org_totals.append(OrgTotal(
            provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
            product=product, model=model, model_provider=None,
            quantities=dict(group.quantities), cost=group.net, cost_list=group.gross,
            currency=CURRENCY, cost_basis="billed", extras=_extras(group),
            is_final=is_final_month(day, pulled_at),
            pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
        ))
        _note_unknowns(run, product, model, seen_products, seen_models)


def _billing_user_month(
    run: NormalisedRun, org: str, raw: Dict[str, Any], body: Any, year: int, month: int,
    known: Dict[str, Optional[str]], pulled_at: datetime, batch_id: str, seen_products: set, seen_models: set,
) -> None:
    first, last = month_bounds(year, month)
    login = str(raw["user"])
    ids = {k.lower(): v for k, v in known.items()}
    uid = ids.get(login.lower()) or login
    user_groups = group_items(body, "billing_user_month %04d-%02d" % (year, month), run.unknown_units)
    for (product, model), group in sorted(user_groups.items()):
        run.usage.append(UsageRecord(
            provider=PROVIDER, provider_account_id=org, scope="", grain="month", period_start=first, period_end=last,
            actor_kind="user", actor_id=uid, actor_email=None, actor_login=login, actor_deleted=None,
            product=product, model=model, model_provider=None,
            quantities=dict(group.quantities), cost=group.gross - group.discount, cost_list=group.gross,
            currency=CURRENCY, cost_basis="consumption", extras=_extras(group),
            is_final=is_final_month(first, pulled_at), cost_only=False,
            pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
        ))
        _note_unknowns(run, product, model, seen_products, seen_models)


def _billing_records(
    run: NormalisedRun, org: str, raws: List[Tuple[Path, Dict[str, Any]]], data_dir: Path,
    pulled_at: datetime, batch_id: str, seen_products: set, seen_models: set,
) -> None:
    """Billing: organization per day (billed) and person per month (consumption)."""
    people_cache: Dict[Tuple[int, int], Dict[str, Optional[str]]] = {}
    for _path, raw in raws:
        endpoint = raw["endpoint"]
        if endpoint not in ("billing_day", "billing_user_month"):
            continue
        page = raw["pages"][0] if raw.get("pages") else None
        if page is None:
            raise ValueError("a billing file has no page")
        body = _body(page)
        year, month = int(raw["year"]), int(raw["month"])
        if endpoint == "billing_day":
            _billing_day(run, org, raw, body, year, month, pulled_at, batch_id, seen_products, seen_models)
        else:
            if (year, month) not in people_cache:
                people_cache[(year, month)] = month_people(data_dir, year, month, pulled_at)
            _billing_user_month(run, org, raw, body, year, month, people_cache[(year, month)],
                                pulled_at, batch_id, seen_products, seen_models)


def _seat_assignment(
    org: str, uid: str, one: Dict[str, Any], today: date, pulled_at: datetime, batch_id: str
) -> SeatAssignment:
    seats = one["seats"]

    def stamps(name: str) -> List[datetime]:
        return [parse_timestamp(s[name]) for s in seats if s.get(name)]

    teams = sorted({str(s["assigning_team"]["slug"]) for s in seats
                    if isinstance(s.get("assigning_team"), dict) and s["assigning_team"].get("slug")})
    cancels = [str(s["pending_cancellation_date"])[:10] for s in seats if s.get("pending_cancellation_date")]
    plans = [s.get("plan_type") for s in seats if s.get("plan_type")]
    created, active, authed = stamps("created_at"), stamps("last_activity_at"), stamps("last_authenticated_at")
    return SeatAssignment(
        provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=today, period_end=today,
        actor_kind="user", actor_id=uid, actor_email=None,
        actor_login=str(one["login"]) if one["login"] else None,
        plan=str(plans[0]) if plans else None,
        assigned_at=min(created) if created else None,
        last_active_at=max(active) if active else None,
        last_authenticated_at=max(authed) if authed else None,
        pending_cancellation_date=date.fromisoformat(max(cancels)) if cancels else None,
        granted_by=",".join(teams) if teams else None,
        pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
    )


def _seat_assignments(
    run: NormalisedRun, org: str, raws: List[Tuple[Path, Dict[str, Any]]], pulled_at: datetime, batch_id: str
) -> date:
    """Seat assignments: the seat lists of this run, one record per person (6.4 rule 9).
    Returns the UTC day of the pull."""
    today = pulled_at.astimezone(timezone.utc).date()
    for _path, raw in raws:
        if raw["endpoint"] != "seats":
            continue
        people: Dict[str, Dict[str, Any]] = {}
        run.seats_without_assignee += unheld_seats(raw)
        for seat in seat_items(raw):
            who = seat["assignee"]
            if who.get("id") is None:
                raise ValueError("a seat's assignee has no id")
            one = people.setdefault(str(who["id"]), {"login": who.get("login"), "seats": []})
            one["seats"].append(seat)
        for uid, one in sorted(people.items()):
            run.seat_assignments.append(_seat_assignment(org, uid, one, today, pulled_at, batch_id))
    return today


def _snapshot_total(seats_raw: Optional[Dict[str, Any]]) -> Optional[int]:
    if seats_raw is not None and seats_raw.get("pages"):
        first_body = _body(seats_raw["pages"][0])
        if isinstance(first_body, dict):
            return opt_int(first_body, "total_seats", "seats")
    return None


def _snapshot_pending(summary_raw: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    if summary_raw is not None and summary_raw.get("pages"):
        breakdown = (_body(summary_raw["pages"][0]) or {}).get("seat_breakdown")
        if isinstance(breakdown, dict):
            return (opt_int(breakdown, "pending_invitation", "seat_breakdown"),
                    opt_int(breakdown, "pending_cancellation", "seat_breakdown"))
    return None, None


def _seat_snapshots(
    run: NormalisedRun, org: str, snapshot_days: set, data_dir: Path, today: date,
    pulled_at: datetime, batch_id: str,
) -> None:
    """Seat snapshots: every raw file of the days this run touched (6.4 rule 8)."""
    for day in sorted(snapshot_days):
        label = day.isoformat()
        seats_raw = _latest(data_dir, "seats", label, pulled_at)
        summary_raw = _latest(data_dir, "seat_summary", label, pulled_at)
        agg_raw = _latest(data_dir, "aggregate_1_day", label, pulled_at)
        total = _snapshot_total(seats_raw)
        pending_invites, pending_cancel = _snapshot_pending(summary_raw)
        counts = None
        if agg_raw is not None and agg_raw.get("status", 200) == 200:
            counts = _active_counts(agg_raw, day)
            if counts is None:
                run.aggregate_missing.append(label)
        daily_a, weekly_a, monthly_a = counts if counts else (None, None, None)
        if day > today:
            continue
        if all(v is None for v in (total, pending_invites, pending_cancel, daily_a, weekly_a, monthly_a)):
            continue
        run.seats.append(SeatSnapshot(
            provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
            seats=total, pending_invites=pending_invites, pending_cancellations=pending_cancel,
            daily_active=daily_a, weekly_active=weekly_a, monthly_active=monthly_a,
            pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
        ))


def normalise_run(
    paths: Iterable[Path],
    data_dir: Path,
    batch_id: str,
    pulled_at: datetime,
    published: Optional[Dict[str, Dict[tuple, Any]]] = None,
) -> NormalisedRun:
    run = NormalisedRun()
    raws = [(Path(p), read_raw(p)) for p in paths]
    seen_models = {r.model for r in ((published or {}).get("org_totals") or {}).values()}
    seen_products = {r.product for r in ((published or {}).get("org_totals") or {}).values()}
    n_files = _single_org(run, raws)
    if not run.provider_account_id:
        return run
    org = run.provider_account_id
    for _path, raw in raws:
        _note_unknown(run.unknown_fields, raw)

    snapshot_days = _map_reports(run, org, raws, pulled_at, batch_id)
    _billing_records(run, org, raws, data_dir, pulled_at, batch_id, seen_products, seen_models)
    today = _seat_assignments(run, org, raws, pulled_at, batch_id)
    _seat_snapshots(run, org, snapshot_days, data_dir, today, pulled_at, batch_id)

    run.unknown_units = sorted(set(run.unknown_units))
    run.unknown_products = sorted(set(run.unknown_products))
    run.unknown_models = sorted(set(run.unknown_models))
    run.feature_sum_mismatch = sorted(set(run.feature_sum_mismatch))
    run.aggregate_missing = sorted(set(run.aggregate_missing))
    run.row_counts = {"files": n_files, **{kind: len(run.records(kind)) for kind in KINDS}}
    logger.info("normalised %d files into %d records", n_files, sum(len(run.records(k)) for k in KINDS))
    return run


# ---- checks (spec 3.7, 6.4 rules 4 and 11) -------------------------------------------------


def run_checks(run: NormalisedRun, today: date) -> List[str]:
    """Failures of the checks before a batch is published (an empty list means
    all pass). Messages carry kinds, counts and dates, never logins. The v1
    check "actor cost per day not above the organization's" is not made: per
    person figures are consumption, organization figures are billed (6.4 rule 11)."""
    failures: List[str] = list(run.problems)
    for kind in KINDS:
        for message in batch_failures(run.records(kind), today):
            failures.append("%s: %s" % (kind, message))

    credits: Dict[date, Decimal] = {}
    tokens: Dict[Tuple[str, date], Decimal] = {}
    for rec in run.usage:
        if rec.grain != "day":
            continue
        if rec.product == "copilot":
            credits[rec.period_start] = credits.get(rec.period_start, Decimal(0)) + rec.quantities.get("ai_credit", Decimal(0))
        elif rec.tokens_input_total is not None:
            key = (rec.product, rec.period_start)
            tokens[key] = tokens.get(key, Decimal(0)) + rec.tokens_input_total
    for day, want in sorted(run.raw_credits.items()):
        if credits.get(day, Decimal(0)) != want:
            failures.append("usage %s: ai credit totals differ (records %s, raw %s)" % (day.isoformat(), credits.get(day, 0), want))
    for (product, day), want in sorted(run.raw_tokens.items()):
        if tokens.get((product, day), Decimal(0)) != want:
            failures.append("usage %s %s: input token totals differ" % (product, day.isoformat()))

    sums: Dict[Tuple[date, str], Dict[str, int]] = {}
    for rec in run.activity:
        if rec.product == "copilot":
            continue
        s = sums.setdefault((rec.period_start, rec.actor_id), {name: 0 for name in FEATURE_FIELDS})
        s["code_generation_activity_count"] += rec.suggestions_shown or 0
        s["code_acceptance_activity_count"] += rec.suggestions_accepted or 0
        s["loc_added_sum"] += rec.lines_added or 0
        s["loc_deleted_sum"] += rec.lines_removed or 0
        s["user_initiated_interaction_count"] += rec.messages or 0
    for key, want in sorted(run.raw_features.items()):
        got = sums.get(key, {name: 0 for name in FEATURE_FIELDS})
        if got != want:
            failures.append("activity %s: record totals differ from the feature sums" % key[0].isoformat())
    return failures


# ---- revisions ------------------------------------------------------------------------------


def load_published(data_dir: Path) -> Dict[str, Dict[tuple, Any]]:
    return load_published_kinds(data_dir, KINDS)
