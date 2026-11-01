"""Normaliser: reads raw files only (never the network) and emits meterline records (spec v1.0 sections 4 and 5).
Money is ``Decimal`` end to end: the API sends amounts as decimal strings in cents, and response bodies are parsed
with ``parse_float=Decimal``.

A response that lacks a field this module needs, or holds one with the wrong type, raises ``ShapeError``: the run
publishes nothing (exit 2) instead of guessing. A field this module does not know is not an error: it is counted in
``NormalisedRun.unknown_fields`` (key paths and counts, never a value) for the run manifest, and the run goes on. Nothing
here reads the wall clock; ``pulled_at`` is passed in.
"""

import gzip
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from meterline.published import drop_unchanged as drop_unchanged
from meterline.published import load_published as load_published_kinds
from meterline.records import UNASSIGNED_ID, OrgTotal, UsageRecord, batch_failures
from meterline.run import UnknownFields, note_unknown_fields

logger = logging.getLogger("meterline_anthropic_admin.normalise")

PROVIDER = "anthropic"
CURRENCY = "USD"
FINAL_AFTER = timedelta(hours=48)  # a bucket is final once it ended this long before the pull
PRODUCT_TOKENS = "api"
# cost_type -> product of the OrgTotal; "tokens" rows are split by model into the product above
OTHER_PRODUCTS = {"web_search": "api-web-search", "code_execution": "api-code-execution"}
COST_TYPES = ("tokens",) + tuple(OTHER_PRODUCTS)

KINDS = {"usage": UsageRecord, "org_totals": OrgTotal}

TOKEN_FIELDS = ("uncached", "c5", "c1", "read", "out")
USAGE_FIELDS = {
    "uncached_input_tokens", "cache_creation", "cache_read_input_tokens", "output_tokens", "server_tool_use", "model", "api_key_id",
    "workspace_id", "service_tier", "context_window", "inference_geo", "speed", "account_id", "service_account_id",
}
COST_FIELDS = {
    "amount", "currency", "cost_type", "description", "model", "token_type", "service_tier", "context_window", "inference_geo",
    "speed", "workspace_id",
}
CACHE_FIELDS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
PAGE_FIELDS = {"data", "has_more", "next_page"}
BUCKET_FIELDS = {"starting_at", "ending_at", "results"}


def _shape(result_fields: Set[str], **inner: Any) -> Dict[str, Any]:
    """The description of one page of a report, for ``note_unknown_fields``: the page, its day buckets and their results."""
    result: Dict[str, Any] = {name: inner.get(name) for name in result_fields}
    bucket: Dict[str, Any] = {name: None for name in BUCKET_FIELDS}
    bucket["results"] = [result]
    page: Dict[str, Any] = {name: None for name in PAGE_FIELDS}
    page["data"] = [bucket]
    return page


USAGE_SHAPE = _shape(USAGE_FIELDS, cache_creation={name: None for name in CACHE_FIELDS},
                     server_tool_use={"web_search_requests": None})
COST_SHAPE = _shape(COST_FIELDS)


class ShapeError(ValueError):
    """A response lacks a field the documented shape promises, holds one this connector does not know, or holds it
    with the wrong type. The message names the endpoint and the field, never a value."""

    def __init__(self, endpoint: str, field_name: str, problem: str) -> None:
        self.endpoint = endpoint
        self.field_name = field_name
        super().__init__("%s: %s %s" % (endpoint, field_name, problem))


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


def is_final_day(day: date, pulled_at: datetime) -> bool:
    """True when the day's bucket ended at least 48 hours before the pull."""
    ended = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
    return pulled_at.astimezone(timezone.utc) >= ended + FINAL_AFTER


# ---- reading the raw pages ----------------------------------------------------------------------------------------


@dataclass
class UsageRow:
    day: date
    api_key_id: Optional[str]
    workspace_id: Optional[str]
    model: str
    service_tier: Optional[str]
    uncached: int
    c5: int
    c1: int
    read: int
    out: int
    web: Optional[int]


@dataclass
class CostRow:
    day: date
    cost_type: str
    model: Optional[str]
    cents: Decimal
    workspace_id: Optional[str]


def _object(obj: Any, endpoint: str, where: str) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        raise ShapeError(endpoint, where, "is not an object")
    return obj


def _count(value: Any, endpoint: str, where: str) -> int:
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        raise ShapeError(endpoint, where, "is missing or not a whole number")
    if value < 0:
        raise ShapeError(endpoint, where, "is negative")
    return value


def _text(value: Any, endpoint: str, where: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise ShapeError(endpoint, where, "is not text")
    return value


def _window_days(raw: Dict[str, Any], endpoint: str) -> List[date]:
    """The days a raw file's window (``<first>_<last>``) asked for."""
    try:
        first, last = (date.fromisoformat(part) for part in str(raw["window"]).split("_"))
    except (KeyError, ValueError):
        raise ShapeError(endpoint, "window", "is not a pair of dates") from None
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _buckets(raw: Dict[str, Any], endpoint: str, shape: Dict[str, Any], found: UnknownFields) -> Iterator[Tuple[date, Dict[str, Any]]]:
    """Every result of every day bucket of every page, with the day of its bucket. The window's days each come once:
    a day twice means a page was sent twice, a day missing means one was skipped, and summing either gives wrong
    figures with no other sign."""
    seen: Set[date] = set()
    for page in raw["pages"]:
        body = _object(json.loads(page["body"], parse_float=Decimal), endpoint, "")
        note_unknown_fields(found, endpoint, body, shape)
        if not isinstance(body.get("data"), list):
            raise ShapeError(endpoint, "data", "is missing or not a list")
        for bucket in body["data"]:
            _object(bucket, endpoint, "data")
            try:
                start, end = parse_timestamp(bucket["starting_at"]), parse_timestamp(bucket["ending_at"])
            except (KeyError, ValueError, TypeError):
                raise ShapeError(endpoint, "data.starting_at or data.ending_at", "is missing or not a time") from None
            if start.time() != datetime.min.time() or end - start != timedelta(days=1):
                raise ShapeError(endpoint, "data.starting_at", "is not a one-day bucket from midnight UTC")
            if not isinstance(bucket.get("results"), list):
                raise ShapeError(endpoint, "data.results", "is missing or not a list")
            if start.date() in seen:
                raise ShapeError(endpoint, "data.starting_at", "has the day %s twice: a page was sent twice" % start.date().isoformat())
            seen.add(start.date())
            for result in bucket["results"]:
                yield start.date(), result
    missing = [day for day in _window_days(raw, endpoint) if day not in seen]
    if missing:
        raise ShapeError(endpoint, "data", "has no bucket for %d day(s) from %s: a page was skipped" % (len(missing), missing[0].isoformat()))


def usage_rows(raw: Dict[str, Any], found: Optional[UnknownFields] = None) -> List[UsageRow]:
    endpoint = str(raw.get("endpoint"))
    found = {} if found is None else found
    rows: List[UsageRow] = []
    for day, result in _buckets(raw, endpoint, USAGE_SHAPE, found):
        _object(result, endpoint, "results")
        cache = _object(result.get("cache_creation"), endpoint, "cache_creation")
        if any(name not in cache for name in CACHE_FIELDS):
            raise ShapeError(endpoint, "cache_creation", "lacks one of its two buckets")
        tools = result.get("server_tool_use")
        web = None
        if tools is not None:
            web = _count(_object(tools, endpoint, "server_tool_use").get("web_search_requests"),
                         endpoint, "server_tool_use.web_search_requests")
        model = _text(result.get("model"), endpoint, "model")
        if not model:
            raise ShapeError(endpoint, "model", "is missing or null although the query groups by it")
        rows.append(UsageRow(
            day=day, api_key_id=_text(result.get("api_key_id"), endpoint, "api_key_id") or None,
            workspace_id=_text(result.get("workspace_id"), endpoint, "workspace_id"), model=model,
            service_tier=_text(result.get("service_tier"), endpoint, "service_tier"),
            uncached=_count(result.get("uncached_input_tokens"), endpoint, "uncached_input_tokens"),
            c5=_count(cache[CACHE_FIELDS[0]], endpoint, "cache_creation." + CACHE_FIELDS[0]),
            c1=_count(cache[CACHE_FIELDS[1]], endpoint, "cache_creation." + CACHE_FIELDS[1]),
            read=_count(result.get("cache_read_input_tokens"), endpoint, "cache_read_input_tokens"),
            out=_count(result.get("output_tokens"), endpoint, "output_tokens"), web=web,
        ))
    return rows


def cost_rows(raw: Dict[str, Any], found: Optional[UnknownFields] = None) -> List[CostRow]:
    endpoint = str(raw.get("endpoint"))
    found = {} if found is None else found
    rows: List[CostRow] = []
    for day, result in _buckets(raw, endpoint, COST_SHAPE, found):
        _object(result, endpoint, "results")
        if result.get("currency") != CURRENCY:
            raise ShapeError(endpoint, "currency", "is missing or not USD")
        cost_type = result.get("cost_type")
        if cost_type not in COST_TYPES:
            raise ShapeError(endpoint, "cost_type", "is missing or not one of %s" % ", ".join(COST_TYPES))
        try:
            cents = Decimal(result["amount"])
            if not isinstance(result["amount"], str) or not cents.is_finite():
                raise ValueError
        except (KeyError, InvalidOperation, ValueError, TypeError):
            raise ShapeError(endpoint, "amount", "is missing or not a decimal string") from None
        model = _text(result.get("model"), endpoint, "model") or None
        if cost_type == "tokens" and model is None:
            raise ShapeError(endpoint, "model", "is missing on a token cost row")
        rows.append(CostRow(day, cost_type, model, cents, _text(result.get("workspace_id"), endpoint, "workspace_id")))
    return rows


# ---- result ---------------------------------------------------------------------------------------------------------


@dataclass
class NormalisedRun:
    usage: List[UsageRecord] = field(default_factory=list)
    org_totals: List[OrgTotal] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    row_counts: Dict[str, int] = field(default_factory=dict)
    raw_tokens: Dict[date, Dict[str, int]] = field(default_factory=dict)  # per day, summed straight from the rows
    raw_cost: Dict[date, Decimal] = field(default_factory=dict)  # per day, dollars
    data_days: Set[date] = field(default_factory=set)  # days with at least one usage or cost row
    unknown_fields: UnknownFields = field(default_factory=dict)  # (endpoint, key path) -> count of keys the connector does not know
    provider_account_id: str = ""

    def records(self, kind: str) -> List[Any]:
        return getattr(self, kind)


def _merge(into: Dict[str, int], row: UsageRow) -> None:
    for name in TOKEN_FIELDS:
        into[name] = into.get(name, 0) + getattr(row, name)
    into["priority"] = into.get("priority", 0) + (sum(getattr(row, n) for n in TOKEN_FIELDS) if row.service_tier == "priority" else 0)
    if row.web is not None:
        into["web"] = into.get("web", 0) + row.web


def _measures(m: Dict[str, int]) -> Dict[str, Any]:
    write = m["c5"] + m["c1"]
    return {
        "tokens_input_uncached": m["uncached"], "tokens_cache_read": m["read"], "tokens_cache_write": write,
        "tokens_cache_write_5m": m["c5"], "tokens_cache_write_1h": m["c1"],
        "tokens_input_total": m["uncached"] + m["read"] + write, "tokens_output": m["out"],
    }


def _read_files(run: NormalisedRun, paths: Iterable[Path]) -> Tuple[List[UsageRow], List[CostRow], Optional[str], int]:
    """The rows of every raw file, the workspace filter the files were pulled with, and the file count."""
    usage: List[UsageRow] = []
    cost: List[CostRow] = []
    seen: Set[Tuple[str, date]] = set()
    identity: Optional[Tuple[str, Optional[str]]] = None
    n_files = 0
    for path in paths:
        raw = read_raw(path)
        n_files += 1
        if identity is not None and identity != (raw["provider_account_id"], raw["workspace_id"]):
            raise ValueError("raw files of two organisation labels or workspace filters in one run")
        identity = (raw["provider_account_id"], raw["workspace_id"])
        run.provider_account_id = raw["provider_account_id"]
        if raw["endpoint"] not in ("usage_report_messages", "cost_report"):
            raise ValueError("unknown endpoint in raw file: " + str(raw["endpoint"]))
        found = (usage_rows(raw, run.unknown_fields) if raw["endpoint"] == "usage_report_messages"
                 else cost_rows(raw, run.unknown_fields))
        days = {(raw["endpoint"], row.day) for row in found}
        if days & seen:
            raise ValueError("the same report and date appear twice in one run")
        seen |= days
        (usage if raw["endpoint"] == "usage_report_messages" else cost).extend(found)  # type: ignore[arg-type]
    workspace = identity[1] if identity else None
    return usage, cost, workspace, n_files


def _group_usage(run: NormalisedRun, rows: List[UsageRow]) -> Dict[Tuple[date, Optional[str], str], Dict[str, int]]:
    """Rows summed per (day, key, model). A named key that shows up in two workspaces on one day is a problem, not a sum."""
    sums: Dict[Tuple[date, Optional[str], str], Dict[str, int]] = {}
    workspaces: Dict[Tuple[date, str, str], Set[Optional[str]]] = {}
    for row in rows:
        _merge(sums.setdefault((row.day, row.api_key_id, row.model), {}), row)
        _merge(run.raw_tokens.setdefault(row.day, {}), row)
        if row.api_key_id is not None:
            workspaces.setdefault((row.day, row.api_key_id, row.model), set()).add(row.workspace_id)
    for (day, key, model), found in sorted(workspaces.items()):
        if len(found) > 1:
            run.problems.append("key %s on %s, model %s: usage in %d workspaces; the format has one record per key and model, "
                                "so nothing is added or dropped" % (key, day.isoformat(), model, len(found)))
    return sums


def _usage_record(sums: Dict[str, int], day: date, key: Optional[str], model: str, org: str, pulled_at: datetime,
                  batch_id: str) -> UsageRecord:
    extras: Dict[str, int] = {}
    if "web" in sums:
        extras["web_search_requests"] = sums["web"]
    if sums["priority"]:
        extras["priority_tokens"] = sums["priority"]
    return UsageRecord(
        provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
        actor_kind="api_key" if key else "unassigned", actor_id=key or UNASSIGNED_ID, actor_email=None, actor_login=None,
        actor_deleted=None, product=PRODUCT_TOKENS, model=model, model_provider=None, **_measures(sums), extras=extras,
        cost=None, cost_list=None, currency="", cost_basis=None, is_final=is_final_day(day, pulled_at), cost_only=False,
        pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
    )


def _org_totals(run: NormalisedRun, per_model: Dict[Tuple[date, str], Dict[str, int]], costs: Dict[Tuple[date, str, str], Decimal],
                pulled_at: datetime, batch_id: str) -> None:
    """One OrgTotal per day and model (tokens from the usage rows, cost from the token cost rows), and one per day and
    other product (web search, code execution) with no model."""
    for day, model in sorted(set(per_model) | {(d, m) for (d, p, m) in costs if p == PRODUCT_TOKENS}):
        money = costs.get((day, PRODUCT_TOKENS, model))
        tokens = _measures(per_model[(day, model)]) if (day, model) in per_model else {}
        run.org_totals.append(OrgTotal(
            provider=PROVIDER, provider_account_id=run.provider_account_id, scope="", grain="day", period_start=day, period_end=day,
            product=PRODUCT_TOKENS, model=model, model_provider=None, **tokens, cost=money, cost_list=None,
            currency=CURRENCY if money is not None else "",
            cost_basis="billed" if money is not None else None, is_final=is_final_day(day, pulled_at), pulled_at=pulled_at,
            data_refreshed_at=None, batch_id=batch_id,
        ))
    for (day, product, model), money in sorted(costs.items()):
        if product == PRODUCT_TOKENS:
            continue
        run.org_totals.append(OrgTotal(
            provider=PROVIDER, provider_account_id=run.provider_account_id, scope="", grain="day", period_start=day, period_end=day,
            product=product, model=model, model_provider=None, cost=money, cost_list=None, currency=CURRENCY, cost_basis="billed",
            is_final=is_final_day(day, pulled_at), pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
        ))


def normalise_run(paths: Iterable[Path], batch_id: str, pulled_at: datetime) -> NormalisedRun:
    run = NormalisedRun()
    usage, cost, workspace, n_files = _read_files(run, paths)
    if not run.provider_account_id:
        return run
    if workspace:  # the cost report has no workspace filter, so it is applied here
        cost = [row for row in cost if row.workspace_id == workspace]
    sums = _group_usage(run, usage)
    per_model: Dict[Tuple[date, str], Dict[str, int]] = {}
    for (day, key, model), m in sorted(sums.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
        run.usage.append(_usage_record(m, day, key, model, run.provider_account_id, pulled_at, batch_id))
        for name, value in m.items():
            bucket = per_model.setdefault((day, model), {})
            bucket[name] = bucket.get(name, 0) + value
    costs: Dict[Tuple[date, str, str], Decimal] = {}
    for row in cost:
        dollars = row.cents.scaleb(-2)
        product = PRODUCT_TOKENS if row.cost_type == "tokens" else OTHER_PRODUCTS[row.cost_type]
        costs[(row.day, product, row.model or "")] = costs.get((row.day, product, row.model or ""), Decimal(0)) + dollars
        run.raw_cost[row.day] = run.raw_cost.get(row.day, Decimal(0)) + dollars
    _org_totals(run, per_model, costs, pulled_at, batch_id)
    run.data_days = {r.day for r in usage} | {r.day for r in cost}
    run.row_counts = {"files": n_files, "usage_rows": len(usage), "cost_rows": len(cost), "days": len({r.day for r in usage} | {r.day for r in cost})}
    logger.info("normalised %d usage and %d org-total records", len(run.usage), len(run.org_totals))
    return run


# ---- checks (spec section 4) ----------------------------------------------------------------------------------------


def run_checks(run: NormalisedRun, today: date) -> List[str]:
    """Failures of the checks before a batch is published (an empty list means all pass). Messages carry kinds,
    counts, dates and key ids, never a secret."""
    failures: List[str] = list(run.problems)
    for kind in KINDS:
        for message in batch_failures(run.records(kind), today):
            failures.append("%s: %s" % (kind, message))
    for day, raw in sorted(run.raw_tokens.items()):
        for name, rec_name in (("uncached", "tokens_input_uncached"), ("read", "tokens_cache_read"), ("c5", "tokens_cache_write_5m"),
                               ("c1", "tokens_cache_write_1h"), ("out", "tokens_output")):
            for kind in ("usage", "org_totals"):
                got = sum(getattr(r, rec_name) or 0 for r in run.records(kind) if r.period_start == day and r.product == PRODUCT_TOKENS)
                if got != raw.get(name, 0) and not run.problems:
                    failures.append("%s %s: %s totals differ (records %d, raw %d)" % (kind, day.isoformat(), rec_name, got, raw.get(name, 0)))
    for day, raw_cost in sorted(run.raw_cost.items()):
        got_cost = sum((r.cost for r in run.org_totals if r.period_start == day and r.cost is not None), Decimal(0))
        if got_cost != raw_cost:
            failures.append("org_totals %s: cost totals differ (records %s, raw %s)" % (day.isoformat(), got_cost, raw_cost))
    return failures


# ---- revisions ------------------------------------------------------------------------------------------------------


def load_published(data_dir: Path) -> Dict[str, Dict[tuple, Any]]:
    return load_published_kinds(data_dir, KINDS)


def first_open_day(published: Dict[str, Dict[tuple, Any]]) -> Optional[date]:
    """The earliest day whose latest published record is not final: it is read again until a pull makes it final."""
    days = [rec.period_start for latest in published.values() for rec in latest.values() if not rec.is_final]
    return min(days) if days else None
