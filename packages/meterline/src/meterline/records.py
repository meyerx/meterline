"""Record types of the v2 common format (spec v2 section 3).

All dates are UTC; ``period_end`` is inclusive. Money and credit amounts are
``Decimal`` and are serialised as strings. A nullable measure is ``None`` when
the source does not report it, never ``0``.

Five kinds: ``UsageRecord``, ``ActivityRecord``, ``SeatSnapshot``,
``SeatAssignment`` and ``OrgTotal``. ``validate`` checks one record and
``batch_failures`` checks a batch of one kind before it is published.
"""

import calendar
import re
import typing
from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

RECORD_VERSION = 2

GRAINS = ("day", "month", "window")
ACTOR_KINDS = ("user", "proxy_user", "end_user", "service_account", "api_key", "unassigned")
COST_BASES = ("billed", "consumption", "estimate")
UNASSIGNED_ID = "(unassigned)"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The one decimal scale of the format: a money or quantity figure keeps at most this many decimals. validate()
# refuses more, the Parquet columns are decimal128(38, MAX_DECIMALS), and the gold build reads the same limit.
MAX_DECIMALS = 12

Quantities = Dict[str, Decimal]
Extras = Dict[str, Union[int, Decimal]]
Counters = Dict[str, int]


class RecordError(ValueError):
    """A record is malformed or fails validation."""


def amount_str(value: Decimal) -> str:
    """Render a Decimal without exponent and without trailing zeros, but with at
    least two decimals (``Decimal("412.800000")`` becomes ``"412.80"``)."""
    text = format(value, "f")
    if "." in text:
        whole, frac = text.split(".")
        frac = frac.rstrip("0").ljust(2, "0")
    else:
        whole, frac = text, "00"
    return whole + "." + frac


def quantity_str(value: Union[int, Decimal]) -> str:
    """Render a quantity without exponent and without trailing zeros
    (``Decimal("12.50")`` becomes ``"12.5"``, ``Decimal("100.0")`` ``"100"``)."""
    number = Decimal(value)
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def grain_for(period_start: date, period_end: date) -> str:
    """The grain the dates of a record imply (3.1): ``day``, ``month`` or ``window``."""
    if period_start == period_end:
        return "day"
    last = calendar.monthrange(period_start.year, period_start.month)[1]
    if period_start.day == 1 and period_end == date(period_start.year, period_start.month, last):
        return "month"
    return "window"


def _unwrap(tp: Any) -> Tuple[Any, bool]:
    """Return (base type, is_optional) for ``Optional[X]`` or ``X``."""
    if typing.get_origin(tp) is typing.Union:
        args = typing.get_args(tp)
        if type(None) in args:
            rest = [a for a in args if a is not type(None)]
            return rest[0], True
    return tp, False


def _parse_datetime(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RecordError("bad timestamp: " + text) from exc
    if parsed.tzinfo is None:
        raise RecordError("timestamp has no timezone: " + text)
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, bool):
        raise RecordError(where + ": a boolean is not a number")
    try:
        return Decimal(str(value))
    except Exception as exc:  # decimal.InvalidOperation
        raise RecordError(where + ": not a number: " + repr(value)) from exc


def _extras_value(value: Any, where: str) -> Union[int, Decimal]:
    if isinstance(value, bool):
        raise RecordError(where + ": a boolean is not a number")
    if isinstance(value, int):
        return value
    return _decimal(value, where)


class _Record:
    """Shared behaviour; subclasses are keyword-only dataclasses."""

    KEY_FIELDS: Tuple[str, ...] = ()

    def key(self) -> tuple:
        return tuple(getattr(self, name) for name in self.KEY_FIELDS)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            if value is None:
                out[f.name] = None
            elif isinstance(value, datetime):
                out[f.name] = value.astimezone(timezone.utc).isoformat()
            elif isinstance(value, date):
                out[f.name] = value.isoformat()
            elif isinstance(value, Decimal):
                out[f.name] = amount_str(value)
            elif isinstance(value, dict):
                out[f.name] = {
                    k: v if f.name == "counters"
                    or (f.name == "extras" and isinstance(v, int) and not isinstance(v, bool))
                    else quantity_str(v)
                    for k, v in value.items()
                }
            else:
                out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        cname = cls.__name__
        if "record_version" not in data:
            raise RecordError(cname + ": no record_version; this is a v1 line, use meterline.convert_v1")
        version = data["record_version"]
        if isinstance(version, int) and not isinstance(version, bool) and version > RECORD_VERSION:
            # docs/records.md, Compatibility: a newer version is refused with an instruction, never guessed at
            raise RecordError(
                "%s: record_version %d is newer than this meterline reads (%d); "
                "upgrade meterline to read it: pip install --upgrade meterline" % (cname, version, RECORD_VERSION))
        if version != RECORD_VERSION:
            raise RecordError("%s: record_version %r is not %d" % (cname, version, RECORD_VERSION))
        # A field this class does not declare is ignored (docs/records.md, Compatibility): only declared fields
        # are read below, so a line from a newer writer that added a nullable field still reads.
        hints = typing.get_type_hints(cls)
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):  # type: ignore[arg-type]
            base, optional = _unwrap(hints[f.name])
            if f.name not in data:
                if optional:
                    kwargs[f.name] = None
                    continue
                raise RecordError(cname + ": missing field " + f.name)
            raw = data[f.name]
            if raw is None:
                if not optional:
                    raise RecordError(cname + ": null for " + f.name)
                kwargs[f.name] = None
            elif base is datetime:
                kwargs[f.name] = _parse_datetime(str(raw))
            elif base is date:
                kwargs[f.name] = date.fromisoformat(str(raw))
            elif base is Decimal:
                kwargs[f.name] = _decimal(raw, cname + "." + f.name)
            elif base is bool:
                if not isinstance(raw, bool):
                    raise RecordError("%s.%s: expected true or false, got %s" % (cname, f.name, type(raw).__name__))
                kwargs[f.name] = raw
            elif base is int:
                if isinstance(raw, bool) or not isinstance(raw, int):
                    raise RecordError("%s.%s: expected an integer, got %s" % (cname, f.name, type(raw).__name__))
                kwargs[f.name] = raw
            elif typing.get_origin(base) is dict:
                kwargs[f.name] = _parse_map(base, raw, cname + "." + f.name)
            else:
                kwargs[f.name] = str(raw)
        return cls(**kwargs)


def _parse_map(tp: Any, raw: Any, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecordError(where + ": expected a map")
    _key, value_type = typing.get_args(tp)
    out: Dict[str, Any] = {}
    for name, value in raw.items():
        if value_type is Decimal:
            out[str(name)] = _decimal(value, where)
        elif value_type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise RecordError(where + ": counter values are integers")
            out[str(name)] = value
        else:
            out[str(name)] = _extras_value(value, where)
    return out


@dataclass(frozen=True, kw_only=True)
class _Measures(_Record):
    """Token, quantity and cost fields shared by ``UsageRecord`` and ``OrgTotal``."""

    tokens_input_uncached: Optional[int] = None
    tokens_cache_read: Optional[int] = None
    tokens_cache_write: Optional[int] = None
    tokens_cache_write_5m: Optional[int] = None
    tokens_cache_write_1h: Optional[int] = None
    tokens_input_total: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_reasoning: Optional[int] = None
    requests: Optional[int] = None
    quantities: Quantities = field(default_factory=dict)
    cost: Optional[Decimal] = None
    cost_list: Optional[Decimal] = None
    currency: str = ""
    cost_basis: Optional[str] = None
    extras: Extras = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class UsageRecord(_Measures):
    provider: str
    provider_account_id: str
    scope: str = ""
    grain: str
    period_start: date
    period_end: date
    actor_kind: str
    actor_id: str
    actor_email: Optional[str] = None
    actor_login: Optional[str] = None
    actor_deleted: Optional[bool] = None
    product: str
    model: str
    model_provider: Optional[str] = None
    is_final: bool
    cost_only: bool = False
    pulled_at: datetime
    data_refreshed_at: Optional[datetime] = None
    batch_id: str
    record_version: int = RECORD_VERSION

    KEY_FIELDS = (
        "provider", "provider_account_id", "scope", "grain", "period_start",
        "actor_kind", "actor_id", "product", "model",
    )


@dataclass(frozen=True, kw_only=True)
class ActivityRecord(_Record):
    provider: str
    provider_account_id: str
    scope: str = ""
    grain: str
    period_start: date
    period_end: date
    actor_kind: str
    actor_id: str
    actor_email: Optional[str] = None
    actor_login: Optional[str] = None
    product: str
    sessions: Optional[int] = None
    messages: Optional[int] = None
    requests: Optional[int] = None
    suggestions_shown: Optional[int] = None
    suggestions_accepted: Optional[int] = None
    lines_added: Optional[int] = None
    lines_removed: Optional[int] = None
    edits_accepted: Optional[int] = None
    edits_rejected: Optional[int] = None
    commits: Optional[int] = None
    pull_requests: Optional[int] = None
    counters: Counters = field(default_factory=dict)
    pulled_at: datetime
    data_refreshed_at: Optional[datetime] = None
    batch_id: str
    record_version: int = RECORD_VERSION

    KEY_FIELDS = (
        "provider", "provider_account_id", "scope", "grain", "period_start",
        "actor_kind", "actor_id", "product",
    )


@dataclass(frozen=True, kw_only=True)
class SeatSnapshot(_Record):
    provider: str
    provider_account_id: str
    scope: str = ""
    grain: str = "day"
    period_start: date
    period_end: date
    seats: Optional[int] = None
    pending_invites: Optional[int] = None
    pending_cancellations: Optional[int] = None
    daily_active: Optional[int] = None
    weekly_active: Optional[int] = None
    monthly_active: Optional[int] = None
    pulled_at: datetime
    data_refreshed_at: Optional[datetime] = None
    batch_id: str
    record_version: int = RECORD_VERSION

    KEY_FIELDS = ("provider", "provider_account_id", "scope", "period_start")


@dataclass(frozen=True, kw_only=True)
class SeatAssignment(_Record):
    provider: str
    provider_account_id: str
    scope: str = ""
    grain: str = "day"
    period_start: date
    period_end: date
    actor_kind: str = "user"
    actor_id: str
    actor_email: Optional[str] = None
    actor_login: Optional[str] = None
    plan: Optional[str] = None
    assigned_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    last_authenticated_at: Optional[datetime] = None
    pending_cancellation_date: Optional[date] = None
    granted_by: Optional[str] = None
    pulled_at: datetime
    data_refreshed_at: Optional[datetime] = None
    batch_id: str
    record_version: int = RECORD_VERSION

    KEY_FIELDS = ("provider", "provider_account_id", "scope", "period_start", "actor_id")


@dataclass(frozen=True, kw_only=True)
class OrgTotal(_Measures):
    provider: str
    provider_account_id: str
    scope: str = ""
    grain: str
    period_start: date
    period_end: date
    product: str = ""
    model: str = ""
    model_provider: Optional[str] = None
    is_final: bool
    pulled_at: datetime
    data_refreshed_at: Optional[datetime] = None
    batch_id: str
    record_version: int = RECORD_VERSION

    KEY_FIELDS = ("provider", "provider_account_id", "scope", "grain", "period_start", "product", "model")


KINDS = {
    "usage": UsageRecord,
    "activity": ActivityRecord,
    "seats": SeatSnapshot,
    "seat_assignments": SeatAssignment,
    "org_totals": OrgTotal,
}


# ---- validation (spec 3.7) -------------------------------------------------


def _fail(record: _Record, message: str) -> RecordError:
    return RecordError(type(record).__name__ + ": " + message)


def _too_precise(value: Decimal) -> bool:
    """True when ``value`` has a non-zero digit beyond ``MAX_DECIMALS`` decimals (trailing zeros past it carry none)."""
    parts = value.as_tuple()
    exponent = parts.exponent
    if not isinstance(exponent, int) or exponent >= -MAX_DECIMALS:
        return False
    return any(parts.digits[exponent + MAX_DECIMALS:])  # the last digits, those that sit past the limit


def _check_types(record: _Record) -> None:
    """An int field holds an int and a bool field a bool: no string, float or boolean stands in for either."""
    hints = typing.get_type_hints(type(record))
    for f in fields(record):  # type: ignore[arg-type]
        value = getattr(record, f.name)
        if value is None:
            continue
        base, _optional = _unwrap(hints[f.name])
        if base is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise _fail(record, "%s must be an integer, got %s" % (f.name, type(value).__name__))
        if base is bool and not isinstance(value, bool):
            raise _fail(record, "%s must be true or false, got %s" % (f.name, type(value).__name__))


def _check_numbers(record: _Record) -> None:
    for f in fields(record):  # type: ignore[arg-type]
        value = getattr(record, f.name)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, dict):
            for name, item in value.items():
                if isinstance(item, bool) or not isinstance(item, (int, Decimal)):
                    raise _fail(record, "%s[%s] is not a number" % (f.name, name))
                if f.name == "counters" and not isinstance(item, int):
                    raise _fail(record, "counters[%s] is not an integer" % name)
                if isinstance(item, Decimal) and not item.is_finite():
                    raise _fail(record, "non-finite %s[%s]" % (f.name, name))
                if isinstance(item, Decimal) and _too_precise(item):
                    raise _fail(record, "%s[%s] has more than %d decimals" % (f.name, name, MAX_DECIMALS))
                if item < 0:
                    raise _fail(record, "negative %s[%s]" % (f.name, name))
            continue
        if type(value).__name__ == "float":  # by name: the cost-path scan forbids the type name in source
            raise _fail(record, "float in " + f.name + " (use Decimal)")
        if isinstance(value, Decimal) and not value.is_finite():
            raise _fail(record, "non-finite " + f.name)
        if isinstance(value, Decimal) and _too_precise(value):
            raise _fail(record, "%s has more than %d decimals" % (f.name, MAX_DECIMALS))
        if isinstance(value, (int, Decimal)) and value < 0:
            raise _fail(record, "negative " + f.name)


def _check_names(record: _Record) -> None:
    for f in fields(record):  # type: ignore[arg-type]
        if f.name in ("quantities", "extras", "counters"):
            for name in getattr(record, f.name):
                if not _NAME_RE.match(str(name)):
                    raise _fail(record, "%s key %r is not lower-case with underscores" % (f.name, name))


def _check_measures(record: _Measures) -> None:
    parts = (record.tokens_input_uncached, record.tokens_cache_read, record.tokens_cache_write)
    if all(p is not None for p in parts) and record.tokens_input_total is not None \
            and record.tokens_input_total != sum(parts):  # type: ignore[arg-type]
        raise _fail(record, "tokens_input_total differs from the sum of the input buckets")
    if record.tokens_cache_write_5m is not None and record.tokens_cache_write_1h is not None \
            and record.tokens_cache_write != record.tokens_cache_write_5m + record.tokens_cache_write_1h:
        raise _fail(record, "tokens_cache_write_5m + tokens_cache_write_1h differs from tokens_cache_write")
    no_cost = record.cost is None and record.cost_list is None
    if no_cost != (record.cost_basis is None):
        raise _fail(record, "cost_basis must be null exactly when cost and cost_list are null")
    if record.cost_basis is not None and record.cost_basis not in COST_BASES:
        raise _fail(record, "unknown cost_basis " + repr(record.cost_basis))
    if no_cost != (record.currency == ""):
        raise _fail(record, "currency must be empty exactly when cost and cost_list are null")


def _check_actor(record: Any) -> None:
    if record.actor_kind not in ACTOR_KINDS:
        raise _fail(record, "unknown actor_kind " + repr(record.actor_kind))
    if not record.actor_id:
        raise _fail(record, "empty actor_id")
    if record.actor_kind == "unassigned" and record.actor_id != UNASSIGNED_ID:
        raise _fail(record, "an unassigned record has actor_id " + UNASSIGNED_ID)
    email = record.actor_email
    if email is not None and email != email.strip().lower():
        raise _fail(record, "actor_email is not trimmed and lower-cased")


def _check_zones(record: _Record) -> None:
    """Every datetime field carries a zone: a naive value would be written shifted by the host's time zone."""
    for f in fields(record):  # type: ignore[arg-type]
        value = getattr(record, f.name)
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise _fail(record, f.name + " has no timezone")


def validate(record: _Record, today: Optional[date] = None) -> None:
    """Raise ``RecordError`` when a record breaks a rule of spec 3.1 or 3.7.
    ``today`` is the UTC date of the pull; it defaults to the date of the
    record's ``pulled_at``."""
    _check_zones(record)
    pulled_at = record.pulled_at
    if today is None:
        today = pulled_at.astimezone(timezone.utc).date()
    if record.record_version != RECORD_VERSION:
        raise _fail(record, "record_version is not %d" % RECORD_VERSION)
    if not record.provider or not record.provider_account_id or record.provider_account_id == "None":  # type: ignore[attr-defined]
        raise _fail(record, "empty provider or provider_account_id")
    start = record.period_start
    end = record.period_end
    grain = record.grain
    if end < start:
        raise _fail(record, "period_end before period_start")
    if grain not in GRAINS:
        raise _fail(record, "unknown grain " + repr(grain))
    if grain != grain_for(start, end):
        raise _fail(record, "dates do not match grain " + grain)
    if start > today or (end > today and grain != "month"):
        raise _fail(record, "date in the future")
    _check_types(record)
    _check_numbers(record)
    _check_names(record)
    if isinstance(record, (UsageRecord, OrgTotal)):
        _check_measures(record)
    if isinstance(record, (UsageRecord, ActivityRecord, SeatAssignment)):
        _check_actor(record)
    if isinstance(record, (SeatSnapshot, SeatAssignment)) and grain != "day":
        raise _fail(record, "grain must be day")
    if isinstance(record, SeatAssignment):
        if record.actor_kind != "user":
            raise _fail(record, "actor_kind must be user")
        if start != pulled_at.astimezone(timezone.utc).date():
            raise _fail(record, "period_start must be the UTC date of pulled_at")


def batch_failures(records: Iterable[_Record], today: Optional[date] = None) -> List[str]:
    """Failures of a batch of one kind (an empty list means it may be
    published): every record validates, no key repeats, and an ``OrgTotal``
    period and product has either per-model rows or one all-models row.
    Messages carry kinds, counts and field names, never emails."""
    items = list(records)
    failures: List[str] = []
    problems: Counter = Counter()
    for rec in items:
        try:
            validate(rec, today)
        except RecordError as exc:
            problems[str(exc)] += 1
    for message, n in sorted(problems.items()):
        failures.append("%s (%d records)" % (message, n))
    seen = Counter(rec.key() for rec in items)
    dupes = sum(1 for n in seen.values() if n > 1)
    if dupes:
        failures.append("%d duplicate record keys in the batch" % dupes)
    totals = [r for r in items if isinstance(r, OrgTotal)]
    if totals:
        groups: Dict[tuple, set] = {}
        for rec in totals:
            groups.setdefault(rec.key()[:-1], set()).add(rec.model == "")
        mixed = sum(1 for flags in groups.values() if len(flags) > 1)
        if mixed:
            failures.append("%d org-total periods mix per-model rows with an all-models row" % mixed)
    return failures


def validate_batch(records: Iterable[_Record], today: Optional[date] = None) -> None:
    """Raise ``RecordError`` listing every failure of ``batch_failures``."""
    failures = batch_failures(records, today)
    if failures:
        raise RecordError("; ".join(failures))


def actor_cost_failures(usage: Iterable[UsageRecord], org_totals: Iterable[OrgTotal]) -> List[str]:
    """Spec 3.7 rule 5: one actor's cost on a day never exceeds the
    organization's cost. Compared only between records of the same provider,
    org, scope, grain, day, ``cost_basis`` and ``currency``, summed over
    products and models; ``end_user`` records are never counted."""
    limits: Dict[tuple, Decimal] = {}
    for tot in org_totals:
        if tot.cost is None:
            continue
        gkey = (tot.provider, tot.provider_account_id, tot.scope, tot.grain, tot.period_start, tot.cost_basis, tot.currency)
        limits[gkey] = limits.get(gkey, Decimal(0)) + tot.cost
    sums: Dict[tuple, Decimal] = {}
    for rec in usage:
        if rec.cost is None or rec.actor_kind == "end_user":
            continue
        gkey = (rec.provider, rec.provider_account_id, rec.scope, rec.grain, rec.period_start, rec.cost_basis, rec.currency)
        akey = gkey + (rec.actor_kind, rec.actor_id)
        sums[akey] = sums.get(akey, Decimal(0)) + rec.cost
    over = sum(1 for akey, amount in sums.items() if akey[:7] in limits and amount > limits[akey[:7]])
    if over:
        return ["%d actor-periods with cost above the organization total" % over]
    return []


def latest_per_key(records: Iterable[_Record]) -> Dict[tuple, _Record]:
    """Map each record key to its record with the latest ``batch_id``
    (batch ids sort chronologically; on a tie the later item wins)."""
    best: Dict[tuple, _Record] = {}
    for rec in records:
        k = rec.key()
        cur = best.get(k)
        if cur is None or rec.batch_id >= cur.batch_id:  # type: ignore[attr-defined]
            best[k] = rec
    return best
