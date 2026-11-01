"""Normaliser: reads raw files only (never the network) and emits meterline
records (spec v2 section 5.4). Money is ``Decimal`` end to end: response bodies
are parsed with ``parse_float=Decimal``. A field the code does not know is not an error: it is counted in
``NormalisedRun.unknown_fields`` (key paths and counts, never a value) for the run manifest, and the run goes on.

Nothing here reads the wall clock; ``pulled_at`` is passed in.
"""

import gzip
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from meterline.published import drop_unchanged as drop_unchanged
from meterline.published import load_published as load_published_kinds
from meterline.records import MAX_DECIMALS, OrgTotal, UsageRecord, batch_failures
from meterline.run import UnknownFields, note_unknown_fields

logger = logging.getLogger("meterline_litellm.normalise")

PROVIDER = "litellm"
CURRENCY = "USD"
FINAL_AFTER_DAYS = 2
UNASSIGNED_ID = "(unassigned)"

KINDS = {"usage": UsageRecord, "org_totals": OrgTotal}

MONEY = ("spend", "flat_cost")
COUNTS = (
    "prompt_tokens", "completion_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    "api_requests", "successful_requests", "failed_requests",
)
FIELDS = MONEY + COUNTS
# raw metric -> record field, summed for the "record totals equal raw totals" check
TOTAL_PAIRS = (
    ("prompt_tokens", "tokens_input_total"),
    ("cache_read_input_tokens", "tokens_cache_read"),
    ("cache_creation_input_tokens", "tokens_cache_write"),
    ("completion_tokens", "tokens_output"),
    ("api_requests", "requests"),
    ("spend", "cost"),
)

Metrics = Dict[str, Decimal]

# The shapes the connector knows (docs/api-shapes/litellm), for the unknown-field report. A dict lists the known keys,
# a one-item list describes the items of a list, None is a value that is not looked into, and "*" describes every key
# of a map whose keys are data (see meterline.run.note_unknown_fields). The breakdown kinds the connector does not map
# (api_keys, endpoints, mcp_servers, model_groups, providers) and the user table of /user/list stay in raw/ and are not
# looked into; a new kind of breakdown, a new metric and a new key in the page are reported.
_METRICS = {name: None for name in (
    "api_requests", "autorouter_savings_spend", "cache_creation_input_tokens", "cache_read_input_tokens", "completion_tokens",
    "compression_saved_tokens", "compression_savings_spend", "failed_requests", "flat_cost", "gateway_injected_caching_savings_spend",
    "prompt_caching_savings_spend", "prompt_tokens", "spend", "successful_requests", "timed_requests", "total_response_time_ms",
    "total_tokens")}
_BREAKDOWN_ITEM = {"metrics": _METRICS, "metadata": None, "api_key_breakdown": None}
_ACTIVITY_PAGE = {
    "metadata": None,
    "results": [{
        "date": None,
        "metrics": _METRICS,
        "breakdown": {"models": {"*": _BREAKDOWN_ITEM}, "entities": {"*": _BREAKDOWN_ITEM}, "api_keys": None, "endpoints": None,
                      "mcp_servers": None, "model_groups": None, "providers": None},
    }],
}
KNOWN_SHAPES: Dict[str, Any] = {
    "user_daily_activity": _ACTIVITY_PAGE,
    "customer_daily_activity": _ACTIVITY_PAGE,
    "user_list": {"users": [None], "total": None, "page": None, "page_size": None, "total_pages": None},
}

# LiteLLM sends spend as a binary float, which has 15 to 19 decimals; the format keeps MAX_DECIMALS (README, Record
# mapping). The context is wide so that rounding a large figure cannot overflow the default 28 digits.
_SCALE = Decimal(1).scaleb(-MAX_DECIMALS)
_ROUNDING = Context(prec=60, rounding=ROUND_HALF_EVEN)

# LiteLLM adds ``spend`` as binary floating point, row by row, in different
# orders for the day, the models and the users, so the sums differ by about
# 1e-16. A money difference below this is noise, not usage.
MONEY_TOLERANCE = Decimal("0.000001")


def _noise(name: str, diff: Decimal) -> bool:
    return name in MONEY and abs(diff) < MONEY_TOLERANCE


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
        return json.loads(handle.read().decode("utf-8"))


def is_final_day(day: date, pulled_at: datetime) -> bool:
    """True when the day ended at least 2 full UTC days before the pull."""
    ended = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
    return pulled_at.astimezone(timezone.utc) >= ended + timedelta(days=FINAL_AFTER_DAYS)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def result_fingerprint(result: Any) -> str:
    """One date result of a page as text, for telling a replayed page from a date cut across two pages.

    The proxy gives no row identity, and a date can be cut across pages, but each page carries different rows of it, so
    two pages never hold the same date result digit for digit: when they do, a page was sent twice."""
    return json.dumps(result, sort_keys=True, default=lambda number: format(number.normalize(), "f"))


# ---- reading a view -------------------------------------------------------------


@dataclass
class Day:
    """One date of one view, pages merged: metrics, per-model metrics and the
    entity keys with their metadata."""

    metrics: Metrics = field(default_factory=dict)
    models: Dict[str, Metrics] = field(default_factory=dict)
    entities: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class ShapeError(ValueError):
    """A response lacks a field the documented shape promises, or holds it with
    the wrong type. The message names the endpoint and the field, never a
    value. A run that meets one publishes nothing (exit 2)."""

    def __init__(self, endpoint: str, field_name: str, problem: str) -> None:
        self.endpoint = endpoint
        self.field_name = field_name
        super().__init__("%s: %s %s" % (endpoint, field_name, problem))


def _read_metrics(raw: Any, endpoint: str, where: str) -> Metrics:
    """The nine metrics of one ``metrics`` object, read strictly: every key
    must be present and a number (a whole number for the counts); a missing
    one is never counted as zero."""
    if not isinstance(raw, dict):
        raise ShapeError(endpoint, where, "is missing or not an object")
    out: Metrics = {}
    for name in FIELDS:
        path = "%s.%s" % (where, name)
        if name not in raw:
            raise ShapeError(endpoint, path, "is missing")
        value = raw[name]
        if value is None:
            raise ShapeError(endpoint, path, "is null")
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ShapeError(endpoint, path, "is not a number")
        number = Decimal(value)
        if not number.is_finite():
            raise ShapeError(endpoint, path, "is not a finite number")
        if name in MONEY and number.as_tuple().exponent < -MAX_DECIMALS:
            number = number.quantize(_SCALE, context=_ROUNDING)
        if name in COUNTS and number != number.to_integral_value():
            raise ShapeError(endpoint, path, "is not a whole number")
        out[name] = number
    return out


def _add(dst: Metrics, src: Metrics) -> None:
    for name, value in src.items():
        dst[name] = dst.get(name, Decimal(0)) + value


def view_days(raw: Dict[str, Any]) -> Dict[date, Day]:
    """Merge the pages of one raw file by date: every metric summed, breakdown
    maps merged key by key (a date can be cut across pages)."""
    endpoint = str(raw.get("endpoint"))
    days: Dict[date, Day] = {}
    seen: Dict[str, int] = {}
    for number, page in enumerate(raw["pages"], 1):
        body = json.loads(page["body"], parse_float=Decimal)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ShapeError(endpoint, "results", "is missing or not a list")
        for result in body["results"]:
            if "date" not in result:
                raise ShapeError(endpoint, "date", "is missing")
            fingerprint = result_fingerprint(result)
            if fingerprint in seen:
                raise ShapeError(endpoint, "results", "of page %d repeat the date %s of page %d: a page was sent twice"
                                 % (number, str(result["date"])[:10], seen[fingerprint]))
            seen[fingerprint] = number
            try:
                when = date.fromisoformat(str(result["date"])[:10])
            except ValueError:
                raise ShapeError(endpoint, "date", "is not a date") from None
            day = days.setdefault(when, Day())
            _add(day.metrics, _read_metrics(result.get("metrics"), endpoint, "metrics"))
            breakdown = result.get("breakdown") or {}
            for model, item in (breakdown.get("models") or {}).items():
                _add(day.models.setdefault(str(model), {}),
                     _read_metrics((item or {}).get("metrics"), endpoint, "breakdown.models.metrics"))
            for entity, item in (breakdown.get("entities") or {}).items():
                meta = day.entities.setdefault(str(entity), {})
                meta.update((item or {}).get("metadata") or {})
    return days


def buckets(day: Day, problems: List[str], where: str) -> Dict[str, Metrics]:
    """Metrics per model. Usage that sits in ``metrics`` but in no model (a raw
    row with a null model is skipped by the proxy's model breakdown) goes into
    the bucket with model ``""``. A model sum above the day's metrics is a
    disagreement and is reported in ``problems``."""
    out: Dict[str, Metrics] = {m: dict(v) for m, v in day.models.items()}
    rest: Metrics = {}
    for name, total in day.metrics.items():
        part = sum((v.get(name, Decimal(0)) for v in day.models.values()), Decimal(0))
        diff = total - part
        if _noise(name, diff):
            continue
        if diff < 0:
            problems.append("%s: models add up to more than the day's %s" % (where, name))
        elif diff > 0:
            rest[name] = diff
    if rest:
        out[""] = rest
    return out


# ---- result ---------------------------------------------------------------------


@dataclass
class NormalisedRun:
    usage: List[UsageRecord] = field(default_factory=list)
    org_totals: List[OrgTotal] = field(default_factory=list)
    global_days: Dict[date, Metrics] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)
    unknown_models: List[str] = field(default_factory=list)
    token_anomalies: List[str] = field(default_factory=list)
    modelless_days: List[str] = field(default_factory=list)
    row_counts: Dict[str, int] = field(default_factory=dict)
    proxy_name: str = ""
    unknown_fields: UnknownFields = field(default_factory=dict)  # (endpoint, key path) -> count of keys the connector does not know

    def records(self, kind: str) -> List[Any]:
        return getattr(self, kind)


# ---- mapping ----------------------------------------------------------------------


def _count(value: Optional[Decimal]) -> Optional[int]:
    return None if value is None else int(value)


def _measures(m: Metrics, anomalies: List[str], tag: str) -> Dict[str, Any]:
    total = _count(m.get("prompt_tokens"))
    read = _count(m.get("cache_read_input_tokens"))
    write = _count(m.get("cache_creation_input_tokens"))
    uncached: Optional[int] = None
    if total is not None and read is not None and write is not None:
        uncached = total - read - write
        if uncached < 0:
            uncached = None
            anomalies.append(tag)
    extras: Dict[str, Any] = {}
    for name in ("successful_requests", "failed_requests"):
        if name in m:
            extras[name] = int(m[name])
    if m.get("flat_cost"):
        extras["flat_cost"] = m["flat_cost"]
    spend = m.get("spend")
    return {
        "tokens_input_uncached": uncached,
        "tokens_cache_read": read,
        "tokens_cache_write": write,
        "tokens_input_total": total,
        "tokens_output": _count(m.get("completion_tokens")),
        "requests": _count(m.get("api_requests")),
        "extras": extras,
        "cost": spend,
        "cost_list": None,
        "currency": CURRENCY if spend is not None else "",
        "cost_basis": "estimate" if spend is not None else None,
    }


def _nonzero(m: Metrics) -> bool:
    return any(v != 0 for v in m.values())


def _read_user_list(raw: Dict[str, Any], emails: Dict[str, str]) -> None:
    for page in raw["pages"]:
        body = json.loads(page["body"], parse_float=Decimal)
        for user in body.get("users") or []:
            if not isinstance(user, dict) or not user.get("user_id"):
                raise ShapeError("user_list", "users.user_id", "is missing")
            mail = user.get("user_email")
            if mail:
                emails[str(user["user_id"])] = str(mail).strip().lower()


def _note_unknown(found: UnknownFields, endpoint: Any, raw: Dict[str, Any]) -> None:
    """Count the keys of every page of a raw file that ``KNOWN_SHAPES`` does not list."""
    shape = KNOWN_SHAPES.get(endpoint)
    if shape is not None:
        for page in raw["pages"]:
            note_unknown_fields(found, str(endpoint), json.loads(page["body"], parse_float=Decimal), shape)


def _read_views(run: NormalisedRun, paths: Iterable[Path]) -> Tuple[
    Dict[date, Day], Dict[str, Dict[date, Day]], Dict[date, Day], Dict[str, Dict[date, Day]], Dict[str, str], int
]:
    """Read the raw files into the global view, the per-user views, the end-user
    global view, the per-end-user views, the user emails and the file count."""
    global_view: Dict[date, Day] = {}
    user_views: Dict[str, Dict[date, Day]] = {}
    end_global: Dict[date, Day] = {}
    end_views: Dict[str, Dict[date, Day]] = {}
    emails: Dict[str, str] = {}
    n_files = 0
    for path in paths:
        raw = read_raw(path)
        n_files += 1
        name = raw["proxy_name"]
        if run.proxy_name and run.proxy_name != name:
            raise ValueError("raw files of two proxies in one run")
        run.proxy_name = name
        endpoint = raw["endpoint"]
        _note_unknown(run.unknown_fields, endpoint, raw)
        if endpoint == "user_list":
            _read_user_list(raw, emails)
            continue
        days = view_days(raw)
        if endpoint == "user_daily_activity":
            target = global_view if raw["entity"] is None else user_views.setdefault(raw["entity"], {})
        elif endpoint == "customer_daily_activity":
            target = end_global if raw["entity"] is None else end_views.setdefault(raw["entity"], {})
        else:
            raise ValueError("unknown endpoint in raw file: " + str(endpoint))
        for day, content in days.items():
            if day in target:
                raise ValueError("the same view and date appear twice in one run")
            target[day] = content
    return global_view, user_views, end_global, end_views, emails, n_files


def _unassigned_diff(run: NormalisedRun, where: str, model: str, have: Metrics, covered: Metrics) -> Metrics:
    """What the global view holds beyond the per-user views for one model."""
    diff: Metrics = {}
    for name in FIELDS:
        if name not in have and name not in covered:
            continue
        value = have.get(name, Decimal(0)) - covered.get(name, Decimal(0))
        if _noise(name, value):
            value = Decimal(0)
        if value < 0:
            run.problems.append(
                "unassigned %s %s: the per-user views add up to more than the global %s"
                % (where, "(no model)" if model == "" else "model", name)
            )
            value = Decimal(0)
        diff[name] = value
    return diff


def _global_day(
    run: NormalisedRun, day: date, content: Day, user_views: Dict[str, Dict[date, Day]],
    emails: Dict[str, str], usage: Any, anomalies: List[str], org: str, pulled_at: datetime, batch_id: str,
) -> None:
    """The records of one date of the global view: per-user usage, the unassigned
    remainder and the per-model organization totals."""
    run.global_days[day] = content.metrics
    where = day.isoformat()
    gb = buckets(content, run.problems, "global " + where)
    if "" in gb:
        run.modelless_days.append(where)
    covered: Dict[str, Metrics] = {}
    for uid in sorted(user_views):
        view = user_views[uid].get(day)
        if view is None:
            continue
        ub = buckets(view, run.problems, "user view " + where)
        for model, m in sorted(ub.items()):
            run.usage.append(usage("proxy_user", uid, emails.get(uid), None, day, model, m))
            _add(covered.setdefault(model, {}), m)
    for model in sorted(set(gb) | set(covered)):
        diff = _unassigned_diff(run, where, model, gb.get(model, {}), covered.get(model, {}))
        if _nonzero(diff):
            run.usage.append(usage("unassigned", UNASSIGNED_ID, None, None, day, model, diff))
    for model, m in sorted(content.models.items()):
        run.org_totals.append(
            OrgTotal(
                provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
                product="", model=model, model_provider=None,
                **_measures(m, anomalies, "%s|%s|org_total|%s" % (where, model, _hash(org))),
                is_final=is_final_day(day, pulled_at),
                pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
            )
        )


def _end_user_records(
    run: NormalisedRun, end_views: Dict[str, Dict[date, Day]], end_global: Dict[date, Day], usage: Any
) -> None:
    """End users: one view per end user; no unassigned record is derived."""
    for eid in sorted(end_views):
        for day in sorted(end_views[eid]):
            view = end_views[eid][day]
            alias = view.entities.get(eid, {}).get("alias")
            if alias is None:
                alias = (end_global.get(day, Day()).entities.get(eid) or {}).get("alias")
            login = None if alias is None else str(alias)
            for model, m in sorted(buckets(view, run.problems, "end user view " + day.isoformat()).items()):
                run.usage.append(usage("end_user", eid, None, login, day, model, m))


def normalise_run(
    paths: Iterable[Path],
    batch_id: str,
    pulled_at: datetime,
    published: Optional[Dict[str, Dict[tuple, Any]]] = None,
) -> NormalisedRun:
    run = NormalisedRun()
    global_view, user_views, end_global, end_views, emails, n_files = _read_views(run, paths)
    if not run.proxy_name:
        return run
    org = run.proxy_name
    seen_models = {r.model for r in ((published or {}).get("usage") or {}).values()}
    anomalies: List[str] = []

    def usage(kind: str, actor_id: str, email: Optional[str], login: Optional[str], day: date,
              model: str, m: Metrics) -> UsageRecord:
        tag = "%s|%s|%s|%s" % (day.isoformat(), model, kind, _hash(actor_id))
        return UsageRecord(
            provider=PROVIDER, provider_account_id=org, scope="", grain="day", period_start=day, period_end=day,
            actor_kind=kind, actor_id=actor_id, actor_email=email, actor_login=login, actor_deleted=None,
            product="", model=model, model_provider=None,
            **_measures(m, anomalies, tag),
            is_final=is_final_day(day, pulled_at), cost_only=False,
            pulled_at=pulled_at, data_refreshed_at=None, batch_id=batch_id,
        )

    for day in sorted(user_views_dates(user_views) - set(global_view)):
        run.problems.append("a per-user view has the date %s that the global view lacks" % day.isoformat())
    for day in sorted(global_view):
        _global_day(run, day, global_view[day], user_views, emails, usage, anomalies, org, pulled_at, batch_id)
    _end_user_records(run, end_views, end_global, usage)

    run.token_anomalies = sorted(set(anomalies))
    run.modelless_days = sorted(set(run.modelless_days))
    run.unknown_models = sorted({r.model for r in run.usage if r.model and r.model not in seen_models})
    run.row_counts = {
        "files": n_files,
        "global_days": len(global_view),
        "user_views": len(user_views),
        "end_user_views": len(end_views),
    }
    logger.info("normalised %d usage and %d org-total records", len(run.usage), len(run.org_totals))
    return run


def user_views_dates(views: Dict[str, Dict[date, Day]]) -> set:
    return {d for v in views.values() for d in v}


# ---- checks (spec 5.4 rule 9, 3.7) ----------------------------------------------------


def run_checks(run: NormalisedRun, today: date) -> List[str]:
    """Failures of the checks before a batch is published (an empty list means
    all pass). Messages carry kinds, counts and dates, never emails."""
    failures: List[str] = list(run.problems)
    for kind in KINDS:
        for message in batch_failures(run.records(kind), today):
            failures.append("%s: %s" % (kind, message))

    # record totals equal raw totals: proxy_user + unassigned per date (never end_user)
    sums: Dict[date, Metrics] = {}
    for rec in run.usage:
        if rec.actor_kind == "end_user":
            continue
        s = sums.setdefault(rec.period_start, {})
        for raw_name, rec_name in TOTAL_PAIRS:
            value = getattr(rec, rec_name)
            if value is not None:
                s[raw_name] = s.get(raw_name, Decimal(0)) + Decimal(value)
    for day, metrics in sorted(run.global_days.items()):
        for raw_name, _rec_name in TOTAL_PAIRS:
            got = sums.get(day, {}).get(raw_name, Decimal(0))
            want = metrics.get(raw_name, Decimal(0))
            if got != want and not _noise(raw_name, got - want):
                failures.append("usage %s: %s totals differ (records %s, raw %s)" % (day.isoformat(), raw_name, got, want))

    # one actor's cost on a day never exceeds the day's global cost
    per_actor: Dict[Tuple[date, str, str], Decimal] = {}
    for rec in run.usage:
        if rec.cost is None or rec.actor_kind == "end_user":
            continue
        key = (rec.period_start, rec.actor_kind, rec.actor_id)
        per_actor[key] = per_actor.get(key, Decimal(0)) + rec.cost
    over = sum(
        1 for (day, _k, _a), amount in per_actor.items()
        if day in run.global_days and "spend" in run.global_days[day]
        and amount - run.global_days[day]["spend"] >= MONEY_TOLERANCE
    )
    if over:
        failures.append("usage: %d actor-days with cost above the global cost" % over)
    return failures


# ---- revisions ------------------------------------------------------------------------


def load_published(data_dir: Path) -> Dict[str, Dict[tuple, Any]]:
    return load_published_kinds(data_dir, KINDS)
