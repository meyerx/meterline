"""Normaliser: reads raw files only (never the network) and emits meterline
records. Money is ``Decimal`` end to end: response bodies are parsed with
``parse_float=Decimal`` and cents become units by ``Decimal(amount) / Decimal(100)``.
A field the code does not know is not an error: it is counted in ``NormalisedRun.unknown_fields`` (key paths and
counts, never a value) for the run manifest, and the run goes on.

Nothing here reads the wall clock; ``pulled_at`` is passed in.
"""

import calendar
import gzip
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from meterline.published import drop_unchanged as drop_unchanged
from meterline.published import load_published as load_published_kinds
from meterline.records import (
    ActivityRecord,
    OrgTotal,
    SeatSnapshot,
    UsageRecord,
    batch_failures,
)
from meterline.run import UnknownFields, note_unknown_fields

logger = logging.getLogger("meterline_anthropic.normalise")

PROVIDER = "anthropic"
FINAL_AFTER_DAYS = 7
HUNDRED = Decimal(100)

KINDS = {
    "usage": UsageRecord,
    "activity": ActivityRecord,
    "seats": SeatSnapshot,
    "org_totals": OrgTotal,
}

ACTIVITY_METRICS = (
    "sessions", "conversations", "messages", "commits", "pull_requests",
    "lines_added", "lines_removed", "edits_accepted", "edits_rejected",
    "artifacts", "skills_used", "connectors_used", "web_searches",
)
# v1 names that became ``counters`` entries in v2 (spec section 4); the other
# eight stay core attributes. meterline-anthropic keeps v1's zeros for both.
COUNTER_METRICS = ("conversations", "artifacts", "skills_used", "connectors_used", "web_searches")
CORE_METRICS = tuple(m for m in ACTIVITY_METRICS if m not in COUNTER_METRICS)
# Raw totals use the names of the raw quantities; ``tokens_input`` is the API's
# uncached input, ``web_search_requests`` ends up in ``extras``.
TOKEN_FIELDS = (
    "tokens_input", "tokens_output", "tokens_cache_write_5m",
    "tokens_cache_write_1h", "tokens_cache_read",
)
USAGE_TOTAL_FIELDS = TOKEN_FIELDS + ("requests", "web_search_requests", "cost", "cost_list")


# ---- the shapes the connector knows (docs/api-shapes), for the unknown-field report -------------------------------
# A dict lists the known keys, a one-item list describes the items of a list, None is a value that is not looked
# into, and "*" describes every key of a map whose keys are data (see meterline.run.note_unknown_fields).

def _keys(*names: str, **inner: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {name: None for name in names}
    out.update(inner)
    return out


_ENVELOPE = ("has_more", "next_page", "data_refreshed_at", "organization_id")
_ACTOR = _keys("type", "user_id", "email_address", "name", "deleted")
_CACHE = _keys("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
_TOOL_USE = _keys("web_search_requests")
_TOKENS = ("uncached_input_tokens", "output_tokens", "cache_read_input_tokens", "requests")
_ACTIVITY_COUNTS = _keys("distinct_session_count", "message_count", "artifacts_created_count", "distinct_skills_used_count",
                         "distinct_connectors_used_count")
_CHAT_COUNTS = _keys("distinct_conversation_count", "message_count", "distinct_artifacts_created_count",
                     "distinct_skills_used_count", "distinct_connectors_used_count")
_PERIOD = ("starting_at", "ending_at")
KNOWN_SHAPES: Dict[str, Any] = {
    "user_usage_report": _keys(*_ENVELOPE, data=[_keys(*_PERIOD, *_TOKENS, "model", "product", "total_tokens", actor=_ACTOR,
                                                       cache_creation=_CACHE, server_tool_use=_TOOL_USE)]),
    "user_cost_report": _keys(*_ENVELOPE, data=[_keys(*_PERIOD, "model", "product", "amount", "list_amount", "currency",
                                                      "requests", actor=_ACTOR)]),
    "usage_report": _keys(*_ENVELOPE, data=[_keys(*_PERIOD, results=[_keys(*_TOKENS, cache_creation=_CACHE,
                                                                           server_tool_use=_TOOL_USE)])]),
    "cost_report": _keys(*_ENVELOPE, data=[_keys(*_PERIOD, results=[_keys("amount", "list_amount", "currency", "requests")])]),
    "summaries": _keys(*_ENVELOPE, data=[_keys(*_PERIOD, "assigned_seat_count", "pending_invite_count", "daily_active_user_count",
                                               "weekly_active_user_count", "monthly_active_user_count")]),
    "users": _keys(*_ENVELOPE, data=[_keys(
        "web_search_count", "last_activity_date",
        user=_keys("type", "id", "email_address"),
        chat_metrics=_CHAT_COUNTS,
        claude_code_metrics=_keys(
            core_metrics=_keys("distinct_session_count", "commit_count", "pull_request_count", "artifacts_created_count",
                               lines_of_code=_keys("added_count", "removed_count")),
            tool_actions={"*": _keys("accepted_count", "rejected_count")}),
        cowork_metrics=_ACTIVITY_COUNTS,
        design_metrics=_ACTIVITY_COUNTS,
        office_metrics=_keys(excel=_ACTIVITY_COUNTS, outlook=_ACTIVITY_COUNTS, powerpoint=_ACTIVITY_COUNTS, word=_ACTIVITY_COUNTS),
        science_metrics=_ACTIVITY_COUNTS,
        chat_cowork_unified_metrics=_keys(chat=_CHAT_COUNTS, sessions=_ACTIVITY_COUNTS))]),
}


def parse_timestamp(text: str) -> datetime:
    """ISO timestamp (``Z`` accepted) to a tz-aware UTC datetime."""
    value = str(text)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_raw(path: Path) -> Dict[str, Any]:
    """Load one raw file (the dict written by the fetcher)."""
    with gzip.open(Path(path), "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


@dataclass
class NormalisedRun:
    usage: List[UsageRecord] = field(default_factory=list)
    activity: List[ActivityRecord] = field(default_factory=list)
    seats: List[SeatSnapshot] = field(default_factory=list)
    org_totals: List[OrgTotal] = field(default_factory=list)
    # Straight sums of the raw rows. Keys: "usage", "org_totals", "activity"
    # map field name -> Decimal; "actor_day_cost" maps (actor_id, day) -> Decimal.
    raw_totals: Dict[str, Dict[Any, Decimal]] = field(default_factory=dict)
    unknown_products: List[str] = field(default_factory=list)
    unknown_models: List[str] = field(default_factory=list)
    cost_only: List[str] = field(default_factory=list)
    row_counts: Dict[str, int] = field(default_factory=dict)
    unknown_fields: UnknownFields = field(default_factory=dict)  # (endpoint, key path) -> count of keys the connector does not know

    def records(self, kind: str) -> List[Any]:
        return getattr(self, kind)


# ---- strict reading (a missing or mistyped field fails the run) ----------------


class ShapeError(ValueError):
    """A response row lacks a field the documented shape promises, or holds it
    with the wrong type. The message names the endpoint and the field, never a
    value. A run that meets one publishes nothing (exit 2)."""

    def __init__(self, endpoint: str, field_name: str, problem: str) -> None:
        self.endpoint = endpoint
        self.field_name = field_name
        super().__init__("%s: %s %s" % (endpoint, field_name, problem))


def _lookup(row: Any, path: str, endpoint: str) -> Any:
    """The value at the dotted ``path`` of ``row``; every step must exist."""
    node = row
    walked: List[str] = []
    for name in path.split("."):
        if not isinstance(node, dict):
            raise ShapeError(endpoint, ".".join(walked) or path, "is not an object")
        walked.append(name)
        if name not in node:
            raise ShapeError(endpoint, ".".join(walked), "is missing")
        node = node[name]
    return node


def whole_number(value: Any, endpoint: str, field_name: str) -> int:
    """A JSON whole number (``12`` or ``12.0``); no string, bool, null or fraction."""
    if value is None:
        raise ShapeError(endpoint, field_name, "is null")
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ShapeError(endpoint, field_name, "is not a whole number")
    if isinstance(value, Decimal) and (not value.is_finite() or value != value.to_integral_value()):
        raise ShapeError(endpoint, field_name, "is not a whole number")
    return int(value)


def required_count(row: Any, path: str, endpoint: str) -> int:
    return whole_number(_lookup(row, path, endpoint), endpoint, path)


def optional_count(row: Dict[str, Any], key: str, endpoint: str) -> Optional[int]:
    """An ``int|null`` field of the spec: absent or null gives None."""
    value = row.get(key)
    return None if value is None else whole_number(value, endpoint, key)


def activity_count(block: Dict[str, Any], key: str) -> int:
    """A count of a ``users`` block: null or absent is 0 (spec 4 rule 4), any
    other value must be a whole number."""
    value = block.get(key)
    return 0 if value is None else whole_number(value, "users", key)


def cents(row: Any, key: str, endpoint: str) -> Decimal:
    """An amount in cents (a decimal string, per the spec) as units. Must be a
    finite number."""
    value = _lookup(row, key, endpoint)
    if value is None:
        raise ShapeError(endpoint, key, "is null")
    if not isinstance(value, str):  # the API sends an amount as a decimal string; a JSON number is not what it promises
        raise ShapeError(endpoint, key, "is not a decimal string")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ShapeError(endpoint, key, "is not a number") from None
    if not number.is_finite():
        raise ShapeError(endpoint, key, "is not a finite number")
    return number / HUNDRED


def day_of(row: Any, endpoint: str) -> date:
    value = _lookup(row, "starting_at", endpoint)
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ShapeError(endpoint, "starting_at", "is not a date") from None


def user_id_of(row: Any, path: str, endpoint: str) -> str:
    value = _lookup(row, path, endpoint)
    if not isinstance(value, str) or not value:
        raise ShapeError(endpoint, path, "is not a non-empty string")
    return value


def _pages(raw: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    for page in raw["pages"]:
        yield json.loads(page["body"], parse_float=Decimal)


def data_list(body: Any, endpoint: Any) -> List[Any]:
    """The ``data`` list of one answer. The envelope is required: an answer without it, or with ``null`` there, is an
    error and never an empty answer (an empty answer is ``"data": []``)."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ShapeError(str(endpoint), "data", "is missing or not a list")
    return data


def row_count(raw: Dict[str, Any]) -> int:
    """How many rows the pages of a flat raw file (``users``, ``summaries``, the per-user reports) hold."""
    return sum(1 for _ in _flat_rows(raw))


def _flat_rows(raw: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    for body in _pages(raw):
        yield from data_list(body, raw.get("endpoint"))


def _buckets(raw: Dict[str, Any]) -> Iterator[Tuple[date, List[Dict[str, Any]]]]:
    endpoint = raw.get("endpoint")
    for body in _pages(raw):
        for bucket in data_list(body, endpoint):
            results = bucket.get("results") if isinstance(bucket, dict) else None
            if not isinstance(results, list):
                raise ShapeError(str(endpoint), "data.results", "is missing or not a list")
            yield day_of(bucket, endpoint), list(results)


def _provider_account_id(raw: Dict[str, Any]) -> str:
    """The raw file's organization id (rule 1); never empty or ``"None"``."""
    value = raw.get("organization_id")
    if not value:
        for body in _pages(raw):
            if body.get("organization_id"):
                value = body["organization_id"]
                break
    if not value:
        raise ShapeError(str(raw.get("endpoint")), "organization_id", "is missing")
    return str(value)


def _email(actor: Dict[str, Any], endpoint: str, where: str) -> Optional[str]:
    """The e-mail of an actor or user, lower-cased. The key is required (the API version 2026-10-21 renamed it from
    ``email``): a row without it is an error, and only an explicit null gives no e-mail."""
    if "email_address" not in actor:
        raise ShapeError(endpoint, where + ".email_address", "is missing")
    value = actor["email_address"]
    if value is None:
        return None
    return str(value).strip().lower()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def is_final_period(period_start: date, pulled_at: datetime) -> bool:
    """True when the period's month ended more than 7 days before the pull."""
    last_day = calendar.monthrange(period_start.year, period_start.month)[1]
    month_end = date(period_start.year, period_start.month, last_day)
    return (pulled_at.astimezone(timezone.utc).date() - month_end) > timedelta(days=FINAL_AFTER_DAYS)


ZERO_NUMBERS: Dict[str, Any] = {
    "tokens_input": 0, "tokens_output": 0, "tokens_cache_write_5m": 0, "tokens_cache_write_1h": 0,
    "tokens_cache_read": 0, "requests": None, "web_search_requests": 0,
}


def usage_numbers(row: Dict[str, Any], endpoint: str) -> Dict[str, Any]:
    """The token numbers of one usage row, read strictly: every measure key must
    be present and a whole number (``requests`` is ``int|null``). The raw-totals
    reader of the fetcher uses this too, so the two can never agree on a zero."""
    return {
        "tokens_input": required_count(row, "uncached_input_tokens", endpoint),
        "tokens_output": required_count(row, "output_tokens", endpoint),
        "tokens_cache_write_5m": required_count(row, "cache_creation.ephemeral_5m_input_tokens", endpoint),
        "tokens_cache_write_1h": required_count(row, "cache_creation.ephemeral_1h_input_tokens", endpoint),
        "tokens_cache_read": required_count(row, "cache_read_input_tokens", endpoint),
        "requests": optional_count(row, "requests", endpoint),
        "web_search_requests": required_count(row, "server_tool_use.web_search_requests", endpoint),
    }


def _measure_fields(nums: Dict[str, Any]) -> Dict[str, Any]:
    """The v2 token, request and extras fields for one set of raw numbers
    (the output of ``usage_numbers``); cost fields are added by the caller."""
    write = nums["tokens_cache_write_5m"] + nums["tokens_cache_write_1h"]
    return {
        "tokens_input_uncached": nums["tokens_input"],
        "tokens_cache_read": nums["tokens_cache_read"],
        "tokens_cache_write": write,
        "tokens_cache_write_5m": nums["tokens_cache_write_5m"],
        "tokens_cache_write_1h": nums["tokens_cache_write_1h"],
        "tokens_input_total": nums["tokens_input"] + nums["tokens_cache_read"] + write,
        "tokens_output": nums["tokens_output"],
        "requests": nums["requests"],
        "extras": {"web_search_requests": nums["web_search_requests"]},
    }


def _cost_fields(cost: Optional[Decimal], cost_list: Optional[Decimal], currency: str) -> Dict[str, Any]:
    has_cost = cost is not None or cost_list is not None
    return {
        "cost": cost,
        "cost_list": cost_list,
        "currency": currency,
        "cost_basis": "consumption" if has_cost else None,
    }


def _add(totals: Dict[Any, Decimal], key: Any, value: Any) -> None:
    totals[key] = totals.get(key, Decimal(0)) + Decimal(value)


# ---- activity block mappings (rule 4) ----------------------------------------


def _chat_counts(block: Dict[str, Any]) -> Dict[str, int]:
    return {
        "conversations": activity_count(block, "distinct_conversation_count"),
        "messages": activity_count(block, "message_count"),
        "artifacts": activity_count(block, "distinct_artifacts_created_count"),
        "skills_used": activity_count(block, "distinct_skills_used_count"),
        "connectors_used": activity_count(block, "distinct_connectors_used_count"),
    }


def _cowork_counts(block: Dict[str, Any]) -> Dict[str, int]:
    return {
        "sessions": activity_count(block, "distinct_session_count"),
        "messages": activity_count(block, "message_count"),
        "artifacts": activity_count(block, "artifacts_created_count"),
        "skills_used": activity_count(block, "distinct_skills_used_count"),
        "connectors_used": activity_count(block, "distinct_connectors_used_count"),
    }


def _code_counts(block: Dict[str, Any]) -> Dict[str, int]:
    core = block.get("core_metrics") or {}
    lines = core.get("lines_of_code") or {}
    tools = [t for t in (block.get("tool_actions") or {}).values() if isinstance(t, dict)]
    return {
        "sessions": activity_count(core, "distinct_session_count"),
        "commits": activity_count(core, "commit_count"),
        "pull_requests": activity_count(core, "pull_request_count"),
        "artifacts": activity_count(core, "artifacts_created_count"),
        "lines_added": activity_count(lines, "added_count"),
        "lines_removed": activity_count(lines, "removed_count"),
        "edits_accepted": sum(activity_count(t, "accepted_count") for t in tools),
        "edits_rejected": sum(activity_count(t, "rejected_count") for t in tools),
    }


def _sub(row: Dict[str, Any], *path: str) -> Optional[Dict[str, Any]]:
    node: Any = row
    for name in path:
        node = node.get(name) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


# (product, path to the block in a user row, mapping)
ACTIVITY_BLOCKS = (
    ("chat", ("chat_metrics",), _chat_counts),
    ("claude_code", ("claude_code_metrics",), _code_counts),
    ("cowork", ("cowork_metrics",), _cowork_counts),
    ("claude_design", ("design_metrics",), _cowork_counts),
    ("office_agent.excel", ("office_metrics", "excel"), _cowork_counts),
    ("office_agent.outlook", ("office_metrics", "outlook"), _cowork_counts),
    ("office_agent.powerpoint", ("office_metrics", "powerpoint"), _cowork_counts),
    ("office_agent.word", ("office_metrics", "word"), _cowork_counts),
    ("science", ("science_metrics",), _cowork_counts),
    ("chat_cowork_unified.chat", ("chat_cowork_unified_metrics", "chat"), _chat_counts),
    ("chat_cowork_unified.sessions", ("chat_cowork_unified_metrics", "sessions"), _cowork_counts),
)


# ---- normalise ---------------------------------------------------------------


@dataclass
class _Acc:
    """Everything one ``normalise_run`` call accumulates while it reads the raw files."""

    run: NormalisedRun
    pulled_at: datetime
    batch_id: str
    totals_usage: Dict[Any, Decimal] = field(default_factory=dict)
    totals_org: Dict[Any, Decimal] = field(default_factory=dict)
    totals_act: Dict[Any, Decimal] = field(default_factory=dict)
    actor_day_cost: Dict[Any, Decimal] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    # (org, day, actor id, row, numbers, refreshed)
    usage_rows: List[Tuple[str, date, str, Dict[str, Any], Dict[str, Any], Optional[datetime]]] = field(
        default_factory=list)
    # (actor_id, day, product, model) -> [(org, row, cost, cost_list, refreshed)]
    cost_rows: Dict[Tuple[str, date, str, str], List[Tuple[str, Dict[str, Any], Decimal, Decimal, Optional[datetime]]]] = field(
        default_factory=dict)
    org_usage: Dict[Tuple[str, date], Dict[str, Any]] = field(default_factory=dict)
    org_refreshed: Dict[Tuple[str, date], Optional[datetime]] = field(default_factory=dict)
    org_cost: Dict[Tuple[str, date], Dict[str, Any]] = field(default_factory=dict)
    org_cost_refreshed: Dict[Tuple[str, date], Optional[datetime]] = field(default_factory=dict)


def _read_user_usage(acc: _Acc, raw: Dict[str, Any], provider_account_id: str, refreshed: Optional[datetime]) -> None:
    endpoint = "user_usage_report"
    for row in _flat_rows(raw):
        acc.counts["user_usage_report"] += 1
        day = day_of(row, endpoint)
        actor_id = user_id_of(row, "actor.user_id", endpoint)
        _email(row["actor"], endpoint, "actor")  # the key must be there on every row, even when only the first is mapped
        nums = usage_numbers(row, endpoint)
        acc.usage_rows.append((provider_account_id, day, actor_id, row, nums, refreshed))
        for name in TOKEN_FIELDS + ("web_search_requests",):
            _add(acc.totals_usage, name, nums[name])
        if nums["requests"] is not None:
            _add(acc.totals_usage, "requests", nums["requests"])


def _read_user_cost(acc: _Acc, raw: Dict[str, Any], provider_account_id: str, refreshed: Optional[datetime]) -> None:
    endpoint = "user_cost_report"
    for row in _flat_rows(raw):
        acc.counts["user_cost_report"] += 1
        day = day_of(row, endpoint)
        key = (user_id_of(row, "actor.user_id", endpoint), day, _text(row.get("product")), _text(row.get("model")))
        _email(row["actor"], endpoint, "actor")
        cost = cents(row, "amount", endpoint)
        cost_list = cents(row, "list_amount", endpoint)
        acc.cost_rows.setdefault(key, []).append((provider_account_id, row, cost, cost_list, refreshed))
        _add(acc.totals_usage, "cost", cost)
        _add(acc.totals_usage, "cost_list", cost_list)
        _add(acc.actor_day_cost, (key[0], day), cost)


def _read_org_usage(acc: _Acc, raw: Dict[str, Any], provider_account_id: str, refreshed: Optional[datetime]) -> None:
    endpoint = "usage_report"
    for day, results in _buckets(raw):
        okey = (provider_account_id, day)
        bucket = acc.org_usage.setdefault(okey, {name: 0 for name in TOKEN_FIELDS + ("web_search_requests",)})
        bucket.setdefault("requests", None)
        acc.org_refreshed[okey] = refreshed
        for row in results:
            acc.counts["usage_report"] += 1
            nums = usage_numbers(row, endpoint)
            for name in TOKEN_FIELDS + ("web_search_requests",):
                bucket[name] += nums[name]
                _add(acc.totals_org, name, nums[name])
            if nums["requests"] is not None:
                bucket["requests"] = (bucket["requests"] or 0) + nums["requests"]
                _add(acc.totals_org, "requests", nums["requests"])


def _read_org_cost(acc: _Acc, raw: Dict[str, Any], provider_account_id: str, refreshed: Optional[datetime]) -> None:
    endpoint = "cost_report"
    for day, results in _buckets(raw):
        okey = (provider_account_id, day)
        bucket = acc.org_cost.setdefault(okey, {"cost": Decimal(0), "cost_list": Decimal(0), "currency": "", "rows": 0})
        acc.org_cost_refreshed[okey] = refreshed
        for row in results:
            acc.counts["cost_report"] += 1
            cost, cost_list = cents(row, "amount", endpoint), cents(row, "list_amount", endpoint)
            bucket["rows"] += 1
            bucket["cost"] += cost
            bucket["cost_list"] += cost_list
            if row.get("currency") and not bucket["currency"]:
                bucket["currency"] = str(row["currency"])
            _add(acc.totals_org, "cost", cost)
            _add(acc.totals_org, "cost_list", cost_list)


def _emit_activity(
    acc: _Acc, provider_account_id: str, day: date, user_id: str, email: Optional[str], product: str, metrics: Dict[str, int]
) -> None:
    for name, value in metrics.items():
        _add(acc.totals_act, name, value)
    if not any(metrics.values()):
        return
    full = {name: 0 for name in ACTIVITY_METRICS}
    full.update(metrics)
    acc.run.activity.append(
        ActivityRecord(
            provider=PROVIDER,
            provider_account_id=provider_account_id,
            scope="",
            grain="day",
            period_start=day,
            period_end=day,
            actor_kind="user",
            actor_id=user_id,
            actor_email=email,
            product=product,
            counters={name: full[name] for name in COUNTER_METRICS},
            pulled_at=acc.pulled_at,
            data_refreshed_at=None,
            batch_id=acc.batch_id,
            **{name: full[name] for name in CORE_METRICS},
        )
    )


def _read_users(acc: _Acc, raw: Dict[str, Any], provider_account_id: str, refreshed: Optional[datetime]) -> None:
    endpoint = "users"
    day = date.fromisoformat(raw["window"][:10])
    for row in _flat_rows(raw):
        acc.counts["users"] += 1
        user_id = user_id_of(row, "user.id", endpoint)
        email = _email(row["user"], endpoint, "user")
        for product, block_path, mapping in ACTIVITY_BLOCKS:
            block = _sub(row, *block_path)
            if block is None:
                continue
            _emit_activity(acc, provider_account_id, day, user_id, email, product, mapping(block))
        searches = activity_count(row, "web_search_count")
        if searches:
            _emit_activity(acc, provider_account_id, day, user_id, email, "all", {"web_searches": searches})


def _read_summaries(acc: _Acc, raw: Dict[str, Any], provider_account_id: str, refreshed: Optional[datetime]) -> None:
    endpoint = "summaries"
    for row in _flat_rows(raw):
        acc.counts["summaries"] += 1
        day = day_of(row, endpoint)
        seats = _lookup(row, "assigned_seat_count", endpoint)  # int|null; v2 section 4 keeps null as 0
        active = [required_count(row, name, endpoint) for name in (
            "daily_active_user_count", "weekly_active_user_count", "monthly_active_user_count")]
        acc.run.seats.append(
            SeatSnapshot(
                provider=PROVIDER,
                provider_account_id=provider_account_id,
                scope="",
                grain="day",
                period_start=day,
                period_end=day,
                seats=0 if seats is None else whole_number(seats, endpoint, "assigned_seat_count"),
                pending_invites=optional_count(row, "pending_invite_count", endpoint),
                daily_active=active[0],
                weekly_active=active[1],
                monthly_active=active[2],
                pulled_at=acc.pulled_at,
                data_refreshed_at=None,
                batch_id=acc.batch_id,
            )
        )


def _read_endpoint(
    acc: _Acc, endpoint: Any, raw: Dict[str, Any], provider_account_id: str, refreshed: Optional[datetime]
) -> None:
    if endpoint == "user_usage_report":
        _read_user_usage(acc, raw, provider_account_id, refreshed)
    elif endpoint == "user_cost_report":
        _read_user_cost(acc, raw, provider_account_id, refreshed)
    elif endpoint == "usage_report":
        _read_org_usage(acc, raw, provider_account_id, refreshed)
    elif endpoint == "cost_report":
        _read_org_cost(acc, raw, provider_account_id, refreshed)
    elif endpoint == "users":
        _read_users(acc, raw, provider_account_id, refreshed)
    elif endpoint == "summaries":
        _read_summaries(acc, raw, provider_account_id, refreshed)
    else:
        raise ValueError("raw file with unknown endpoint: " + str(endpoint))


def _join_usage(acc: _Acc) -> set:
    """Usage records, joined with their cost on (actor, day, product, model) (rule 3).
    Returns the cost keys that found a usage row."""
    joined = set()
    for provider_account_id, day, actor_id, row, nums, refreshed in acc.usage_rows:
        actor = row["actor"]
        key = (actor_id, day, _text(row.get("product")), _text(row.get("model")))
        matches = acc.cost_rows.get(key)
        cost: Optional[Decimal] = None
        cost_list: Optional[Decimal] = None
        currency = ""
        if matches:
            joined.add(key)
            cost = sum((m[2] for m in matches), Decimal(0))
            cost_list = sum((m[3] for m in matches), Decimal(0))
            currency = str(matches[0][1].get("currency") or "")
        acc.run.usage.append(
            UsageRecord(
                provider=PROVIDER,
                provider_account_id=provider_account_id,
                scope="",
                grain="day",
                period_start=day,
                period_end=day,
                actor_kind="user",
                actor_id=actor_id,
                actor_email=_email(actor, "user_usage_report", "actor"),
                actor_deleted=bool(actor.get("deleted", False)),
                product=key[2],
                model=key[3],
                model_provider=PROVIDER,
                is_final=is_final_period(day, acc.pulled_at),
                cost_only=False,
                pulled_at=acc.pulled_at,
                data_refreshed_at=refreshed,
                batch_id=acc.batch_id,
                **_measure_fields(nums),
                **_cost_fields(cost, cost_list, currency),
            )
        )
    return joined


def _cost_only_usage(acc: _Acc, joined: set) -> List[str]:
    """Usage records for cost rows no usage row matched; returns their keys as text."""
    cost_only_keys: List[str] = []
    for key in sorted(k for k in acc.cost_rows if k not in joined):
        actor_id, day, product, model = key
        entries = acc.cost_rows[key]
        provider_account_id, first_row, _cost, _cost_list, refreshed = entries[0]
        actor = first_row["actor"]
        acc.run.usage.append(
            UsageRecord(
                provider=PROVIDER,
                provider_account_id=provider_account_id,
                scope="",
                grain="day",
                period_start=day,
                period_end=day,
                actor_kind="user",
                actor_id=actor_id,
                actor_email=_email(actor, "user_cost_report", "actor"),
                actor_deleted=bool(actor.get("deleted", False)),
                product=product,
                model=model,
                model_provider=PROVIDER,
                **_measure_fields(ZERO_NUMBERS),
                **_cost_fields(
                    sum((e[2] for e in entries), Decimal(0)),
                    sum((e[3] for e in entries), Decimal(0)),
                    str(first_row.get("currency") or ""),
                ),
                is_final=is_final_period(day, acc.pulled_at),
                cost_only=True,
                pulled_at=acc.pulled_at,
                data_refreshed_at=refreshed,
                batch_id=acc.batch_id,
            )
        )
        cost_only_keys.append("%s|%s|%s|%s" % (actor_id, day.isoformat(), product, model))
    return cost_only_keys


def _org_total_records(acc: _Acc) -> None:
    for okey in sorted(set(acc.org_usage) | set(acc.org_cost)):
        provider_account_id, day = okey
        usage = acc.org_usage.get(okey)
        cost_part = acc.org_cost.get(okey)
        if cost_part is not None and not cost_part["rows"]:
            cost_part = None  # an empty cost bucket is a day without cost data: null, not a cost of 0 (spec 2.1 C)
        nums = usage or {name: 0 for name in TOKEN_FIELDS + ("web_search_requests",)}
        refreshed = acc.org_refreshed.get(okey) or acc.org_cost_refreshed.get(okey)
        acc.run.org_totals.append(
            OrgTotal(
                provider=PROVIDER,
                provider_account_id=provider_account_id,
                scope="",
                grain="day",
                period_start=day,
                period_end=day,
                product="",
                model="",
                model_provider=PROVIDER,
                **_measure_fields({**nums, "requests": nums.get("requests")}),
                **_cost_fields(
                    None if cost_part is None else cost_part["cost"],
                    None if cost_part is None else cost_part["cost_list"],
                    "" if cost_part is None else str(cost_part["currency"] or ""),
                ),
                is_final=is_final_period(day, acc.pulled_at),
                pulled_at=acc.pulled_at,
                data_refreshed_at=refreshed,
                batch_id=acc.batch_id,
            )
        )


def _note_unknown(found: UnknownFields, endpoint: Any, raw: Dict[str, Any]) -> None:
    """Count the keys of every page of a raw file that ``KNOWN_SHAPES`` does not list."""
    shape = KNOWN_SHAPES.get(endpoint)
    if shape is not None:
        for body in _pages(raw):
            note_unknown_fields(found, str(endpoint), body, shape)


def normalise_run(
    raw_files: Sequence[Path],
    batch_id: str,
    pulled_at: datetime,
    published: Optional[Dict[str, Dict[tuple, Any]]] = None,
) -> NormalisedRun:
    """Turn the raw files of one run into records and raw totals. ``published``
    is what ``load_published`` returns; products and models that no published
    usage record carries go to ``unknown_products`` / ``unknown_models``."""
    pulled_at = pulled_at.astimezone(timezone.utc)
    run = NormalisedRun()
    acc = _Acc(run=run, pulled_at=pulled_at, batch_id=batch_id)

    for path in raw_files:
        raw = read_raw(path)
        endpoint = raw["endpoint"]
        provider_account_id = _provider_account_id(raw)
        stamp = raw.get("data_refreshed_at")
        refreshed = parse_timestamp(stamp) if stamp else None
        _note_unknown(run.unknown_fields, endpoint, raw)
        _read_endpoint(acc, endpoint, raw, provider_account_id, refreshed)

    joined = _join_usage(acc)
    cost_only_keys = _cost_only_usage(acc, joined)
    _org_total_records(acc)

    run.raw_totals = {
        "usage": acc.totals_usage,
        "org_totals": acc.totals_org,
        "activity": acc.totals_act,
        "actor_day_cost": acc.actor_day_cost,
    }
    seen_products = {r.product for r in ((published or {}).get("usage") or {}).values()}
    seen_models = {r.model for r in ((published or {}).get("usage") or {}).values()}
    run.unknown_products = sorted({r.product for r in run.usage if r.product and r.product not in seen_products})
    run.unknown_models = sorted({r.model for r in run.usage if r.model and r.model not in seen_models})
    run.cost_only = sorted(cost_only_keys)
    run.row_counts = dict(sorted(acc.counts.items()))
    logger.info(
        "normalised %d usage, %d activity, %d seat, %d org-total records",
        len(run.usage), len(run.activity), len(run.seats), len(run.org_totals),
    )
    return run


# ---- checks (spec rule 6) -----------------------------------------------------


def _record_value(rec: Any, name: str) -> Any:
    """The value of a raw-total name on a record: renamed, in ``extras`` or in
    ``counters`` where v2 moved it."""
    if name == "tokens_input":
        return rec.tokens_input_uncached
    if name == "web_search_requests":
        return rec.extras.get(name, 0)
    if name in COUNTER_METRICS:
        return rec.counters.get(name, 0)
    return getattr(rec, name)


def _sum_field(records: Iterable[Any], name: str) -> Decimal:
    total = Decimal(0)
    for rec in records:
        value = _record_value(rec, name)
        if value is not None:
            total += Decimal(value)
    return total


def run_checks(run: NormalisedRun, today: date) -> List[str]:
    """Return the failures of spec rule 6 (an empty list means all pass).
    Messages carry kinds, counts and ids, never emails."""
    failures: List[str] = []

    for kind, _cls in KINDS.items():
        records = run.records(kind)
        # v2 record rules (grain, token sums, cost basis, no future dates, no
        # negative numbers) and no duplicate keys within the batch
        for message in batch_failures(records, today):
            failures.append("%s: %s" % (kind, message))

    # record totals equal raw totals
    totals = run.raw_totals
    pairs = (
        ("usage", run.usage, USAGE_TOTAL_FIELDS),
        ("org_totals", run.org_totals, USAGE_TOTAL_FIELDS),
        ("activity", run.activity, ACTIVITY_METRICS),
    )
    for kind, records, names in pairs:
        raw_side = totals.get(kind, {})
        for name in names:
            got = _sum_field(records, name)
            want = raw_side.get(name, Decimal(0))
            if got != want:
                failures.append("%s: %s totals differ (records %s, raw %s)" % (kind, name, got, want))

    # per-user cost per day not above the organization total
    org_cost_by_day: Dict[date, Decimal] = {}
    for rec in run.org_totals:
        if rec.cost is not None:
            org_cost_by_day[rec.period_start] = org_cost_by_day.get(rec.period_start, Decimal(0)) + rec.cost
    over = 0
    for (_actor_id, day), amount in sorted(totals.get("actor_day_cost", {}).items()):
        limit = org_cost_by_day.get(day)
        if limit is not None and amount > limit:
            over += 1
    if over:
        failures.append("usage: %d actor-days with cost above the organization total" % over)
    return failures


# ---- revisions ---------------------------------------------------------------


def load_published(data_dir: Path) -> Dict[str, Dict[tuple, Any]]:
    """Latest record per key for each kind, from ``records/<kind>/*.jsonl``."""
    return load_published_kinds(data_dir, KINDS)
