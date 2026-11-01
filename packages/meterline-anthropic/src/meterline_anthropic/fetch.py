"""Fetcher: calls the API through an ``AnalyticsClient`` and stores every
response page exactly as received.

Raw file: ``<data_dir>/raw/<endpoint>/<window>/<pulled_at>.json.gz`` holding
``{"endpoint", "window", "request_url", "pulled_at", "data_refreshed_at",
"organization_id", "pages": [{"status", "body": <response text>}]}``;
``data_refreshed_at`` is null when no response carried one. The file never
holds request headers or the API key. Gzip output is deterministic (mtime 0).

Nothing here reads the wall clock: callers pass ``pulled_at`` (tz-aware UTC).
Logs carry counts and statuses only.
"""

import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .client import ActivityDateUnavailable, AnalyticsClient, Page
from .normalise import ShapeError, cents, data_list, parse_timestamp, usage_numbers

logger = logging.getLogger("meterline_anthropic.fetch")

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
PAGE_LIMIT = 1000
ORG_PAGE_LIMIT = 31  # the organization endpoints count buckets


def batch_id_for(pulled_at: datetime) -> str:
    """The batch id of a run: the pull time as ``%Y%m%dT%H%M%SZ`` (UTC)."""
    return _utc(pulled_at).strftime(STAMP_FORMAT)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("pulled_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def windows(start: date, end: date, days: int = 31) -> List[Tuple[date, date]]:
    """Split the inclusive range ``start..end`` into inclusive windows of at
    most ``days`` days."""
    if days < 1:
        raise ValueError("days must be at least 1")
    out: List[Tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=days - 1), end)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def window_label(first: date, last: Optional[date] = None) -> str:
    if last is None:
        return first.isoformat()
    return first.isoformat() + "_" + last.isoformat()


@dataclass(frozen=True)
class RawPull:
    """What one stored raw file holds, for the run manifest."""

    path: Path
    endpoint: str
    window: str
    row_count: int
    page_count: int
    data_refreshed_at: Optional[datetime]
    totals: Optional[Dict[str, Decimal]] = None
    provider_account_id: Optional[str] = None


class RawFileExists(ValueError):
    """The raw file a pull would write is already there: another pull ran in the
    same UTC second. Raw files are the evidence the records were built from and
    are never replaced."""


def _gzip_json(path: Path, obj: Dict[str, Any]) -> None:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
        gz.write(payload)
    os.replace(tmp, path)


def _refreshed(pages: List[Page]) -> Optional[str]:
    """The first non-null ``data_refreshed_at`` of the pages, else None."""
    for page in pages:
        value = page.body.get("data_refreshed_at")
        if value:
            return str(value)
    return None


def _org_of(pages: List[Page]) -> Optional[str]:
    for page in pages:
        value = page.body.get("organization_id")
        if value:
            return str(value)
    return None


def _write_raw(
    data_dir: Path,
    endpoint: str,
    window: str,
    pulled_at: datetime,
    pages: List[Page],
    provider_account_id: Optional[str],
) -> Tuple[Path, Optional[str]]:
    refreshed = _refreshed(pages)
    path = Path(data_dir) / "raw" / endpoint / window / (_utc(pulled_at).strftime(STAMP_FORMAT) + ".json.gz")
    if path.exists():
        raise RawFileExists(
            "raw file %s already exists: another pull ran in the same second, and raw files are never replaced"
            % path.relative_to(data_dir).as_posix()
        )
    content = {
        "endpoint": endpoint,
        "window": window,
        "request_url": pages[0].url,
        "pulled_at": _utc(pulled_at).strftime(TS_FORMAT),
        "data_refreshed_at": refreshed,
        "organization_id": provider_account_id,
        "pages": [{"status": p.status, "body": p.text} for p in pages],
    }
    _gzip_json(path, content)
    return path, refreshed


def _stamp(value: Optional[str]) -> Optional[datetime]:
    return parse_timestamp(value) if value else None


def _day_start(day: date) -> str:
    return day.isoformat() + "T00:00:00Z"


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _results_of(bucket: Any, endpoint: str) -> List[Any]:
    results = bucket.get("results") if isinstance(bucket, dict) else None
    if not isinstance(results, list):
        raise ShapeError(endpoint, "data.results", "is missing or not a list")
    return results


def _report_totals(rows: List[Dict[str, Any]], kind: str, endpoint: str) -> Dict[str, Decimal]:
    """Straight sums of the raw rows, read with the same strict readers as the
    normaliser: a row without a measure key raises ``ShapeError``, so these
    totals can never agree with a silently counted zero."""
    if kind == "usage":
        names = ("tokens_input", "tokens_output", "tokens_cache_write_5m", "tokens_cache_write_1h", "tokens_cache_read")
        totals = {name: Decimal(0) for name in names}
        for row in rows:
            numbers = usage_numbers(row, endpoint)
            for name in names:
                totals[name] += numbers[name]
        return totals
    return {
        "cost": sum((cents(r, "amount", endpoint) for r in rows), Decimal(0)),
        "cost_list": sum((cents(r, "list_amount", endpoint) for r in rows), Decimal(0)),
    }


def _fetch_report(
    client: AnalyticsClient,
    data_dir: Path,
    endpoint: str,
    kind: str,
    first: date,
    last: date,
    pulled_at: datetime,
    group: bool,
    skipped: Optional[List[str]],
) -> Optional[RawPull]:
    label = window_label(first, last)

    def skip() -> None:
        logger.info("%s %s: no data yet", endpoint, label)
        if skipped is not None:
            skipped.append("%s %s: no data yet" % (endpoint, label))

    # Probe (spec rule 8): one row; its stamp caps the window. The spec sends it without ending_at, but the docs make
    # ending_at required with bucket_width on the per-user reports, so those carry the window's end (change list).
    probe_params: Dict[str, Any] = {"starting_at": _day_start(first), "bucket_width": "1d", "limit": "1"}
    if group:
        probe_params["ending_at"] = (_midnight(last) + timedelta(days=1)).strftime(TS_FORMAT)
        probe_params["group_by[]"] = ["model", "product"]
    probe = client.get(endpoint, probe_params)
    cap = _stamp(probe.get("data_refreshed_at") or None)
    if cap is None:
        skip()
        return None
    window_end = _midnight(last) + timedelta(days=1)
    ending = min(window_end, _midnight(_utc(cap).date()))
    if ending <= _midnight(first):
        skip()
        return None

    params: Dict[str, Any] = {
        "starting_at": _day_start(first),
        "ending_at": ending.strftime(TS_FORMAT),
        "bucket_width": "1d",
        "limit": str(PAGE_LIMIT if group else ORG_PAGE_LIMIT),
    }
    if group:
        params["group_by[]"] = ["model", "product"]
    pages = client.fetch_pages(endpoint, params)
    if group:
        rows = [row for p in pages for row in data_list(p.body, endpoint)]
    else:
        rows = [row for p in pages for bucket in data_list(p.body, endpoint) for row in _results_of(bucket, endpoint)]
    provider_account_id = _org_of(pages) or (str(probe["organization_id"]) if probe.get("organization_id") else None)
    path, refreshed = _write_raw(data_dir, endpoint, label, pulled_at, pages, provider_account_id)
    logger.info("%s %s: %d rows in %d pages", endpoint, label, len(rows), len(pages))
    return RawPull(
        path=path,
        endpoint=endpoint,
        window=label,
        row_count=len(rows),
        page_count=len(pages),
        data_refreshed_at=_stamp(refreshed),
        totals=_report_totals(rows, kind, endpoint),
        provider_account_id=provider_account_id,
    )


def fetch_usage_window(
    client: AnalyticsClient,
    data_dir: Path,
    first: date,
    last: date,
    pulled_at: datetime,
    skipped: Optional[List[str]] = None,
) -> List[RawPull]:
    """Pull ``user_usage_report`` and ``user_cost_report`` for the inclusive
    window ``first..last``; one raw file each. A window whose probe has no
    refresh stamp (or none past its start) is skipped and named in ``skipped``."""
    out: List[RawPull] = []
    for endpoint, kind in (("user_usage_report", "usage"), ("user_cost_report", "cost")):
        pull = _fetch_report(client, data_dir, endpoint, kind, first, last, pulled_at, True, skipped)
        if pull is not None:
            out.append(pull)
    return out


def fetch_org_totals(
    client: AnalyticsClient,
    data_dir: Path,
    first: date,
    last: date,
    pulled_at: datetime,
    skipped: Optional[List[str]] = None,
) -> List[RawPull]:
    """Pull organization totals (``usage_report`` and ``cost_report``)."""
    out: List[RawPull] = []
    for endpoint, kind in (("usage_report", "usage"), ("cost_report", "cost")):
        pull = _fetch_report(client, data_dir, endpoint, kind, first, last, pulled_at, False, skipped)
        if pull is not None:
            out.append(pull)
    return out


def _learn_provider_account_id(client: AnalyticsClient, day: date) -> str:
    """One ``usage_report`` request for a single day, only to read the
    top-level ``organization_id``. Not stored as raw."""
    body = client.get("usage_report", {"starting_at": _day_start(day), "bucket_width": "1d", "limit": "1"})
    value = body.get("organization_id")
    if not value:
        raise ValueError("the usage_report response carried no organization_id")
    return str(value)


def fetch_activity_day(
    client: AnalyticsClient,
    data_dir: Path,
    day: date,
    pulled_at: datetime,
    on_unavailable: Optional[Callable[[date], None]] = None,
    provider_account_id: Optional[str] = None,
) -> Optional[RawPull]:
    """Pull the ``users`` activity for one date. When the API answers that the
    date is not yet available, return None and call ``on_unavailable`` with the
    latest available date (the window ends cleanly; nothing is written)."""
    if provider_account_id is None:
        provider_account_id = _learn_provider_account_id(client, day)
    params = {"date": day.isoformat(), "limit": str(PAGE_LIMIT)}
    try:
        pages = client.fetch_pages("users", params)
    except ActivityDateUnavailable as exc:
        logger.info("users %s: not yet available (latest %s)", day.isoformat(), exc.latest_date.isoformat())
        if on_unavailable is not None:
            on_unavailable(exc.latest_date)
        return None
    rows = [row for p in pages for row in data_list(p.body, "users")]
    label = window_label(day)
    path, refreshed = _write_raw(data_dir, "users", label, pulled_at, pages, provider_account_id)
    logger.info("users %s: %d rows in %d pages", label, len(rows), len(pages))
    return RawPull(
        path=path, endpoint="users", window=label, row_count=len(rows),
        page_count=len(pages), data_refreshed_at=_stamp(refreshed), provider_account_id=provider_account_id,
    )


def fetch_summaries(
    client: AnalyticsClient,
    data_dir: Path,
    first: date,
    last: date,
    pulled_at: datetime,
    provider_account_id: Optional[str] = None,
    on_unavailable: Optional[Callable[[date], None]] = None,
) -> Optional[RawPull]:
    """Pull seat snapshots for the inclusive date range ``first..last``: one
    request, ``ending_date`` being exclusive. When the API answers that the
    latest date is not yet available (as it does for ``users``), ask again for the
    days up to that date, or return None when none of ``first..last`` is available;
    ``on_unavailable`` is called with the latest available date."""
    if provider_account_id is None:
        provider_account_id = _learn_provider_account_id(client, first)
    params = {
        "starting_date": first.isoformat(),
        "ending_date": (last + timedelta(days=1)).isoformat(),
    }
    try:
        pages = client.fetch_pages("summaries", params)
    except ActivityDateUnavailable as exc:
        logger.info("summaries %s: not yet available (latest %s)", window_label(first, last), exc.latest_date.isoformat())
        if on_unavailable is not None:
            on_unavailable(exc.latest_date)
        if exc.latest_date < first or exc.latest_date >= last:  # nothing to keep, or a refusal that makes no sense
            return None
        return fetch_summaries(client, data_dir, first, exc.latest_date, pulled_at, provider_account_id=provider_account_id)
    rows = [row for p in pages for row in data_list(p.body, "summaries")]
    label = window_label(first, last)
    path, refreshed = _write_raw(data_dir, "summaries", label, pulled_at, pages, provider_account_id)
    logger.info("summaries %s: %d rows", label, len(rows))
    return RawPull(
        path=path, endpoint="summaries", window=label, row_count=len(rows),
        page_count=len(pages), data_refreshed_at=_stamp(refreshed), provider_account_id=provider_account_id,
    )


SUMMARIES_MAX_DAYS = 366  # spec 2.1 F: ending_date at most 366 days after starting_date


def fetch_summaries_windows(
    client: AnalyticsClient,
    data_dir: Path,
    first: date,
    last: date,
    pulled_at: datetime,
    provider_account_id: Optional[str] = None,
    on_unavailable: Optional[Callable[[date], None]] = None,
) -> List[RawPull]:
    """``fetch_summaries`` for ``first..last``, cut into ranges of at most 366 days (one request each)."""
    pulls = [
        fetch_summaries(client, data_dir, a, b, pulled_at, provider_account_id=provider_account_id, on_unavailable=on_unavailable)
        for a, b in windows(first, last, SUMMARIES_MAX_DAYS)
    ]
    return [p for p in pulls if p is not None]


@dataclass
class RunManifest:
    """Record of one run, written to ``runs/<batch_id>.json``."""

    command: str
    batch_id: str
    pulled_at: datetime
    status: str = "published"  # "published" or "failed"
    windows: List[str] = field(default_factory=list)
    pages: int = 0
    row_counts: Dict[str, int] = field(default_factory=dict)
    totals: Dict[str, str] = field(default_factory=dict)
    unknown_products: List[str] = field(default_factory=list)
    unknown_models: List[str] = field(default_factory=list)
    skipped_windows: List[str] = field(default_factory=list)
    cost_only: List[str] = field(default_factory=list)
    empty_days: int = 0  # days loaded from an empty answer that can no longer change (users plus summaries)
    raw_files: List[str] = field(default_factory=list)
    published_counts: Dict[str, int] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    unknown_fields: List[Dict[str, Any]] = field(default_factory=list)  # fields the connector does not know: endpoint, path, count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "batch_id": self.batch_id,
            "pulled_at": _utc(self.pulled_at).strftime(TS_FORMAT),
            "failure_kind": "none" if self.status == "published" else "input",  # a failed manifest is a failed check
            "status": self.status,
            "windows": list(self.windows),
            "pages": self.pages,
            "row_counts": dict(self.row_counts),
            "totals": dict(self.totals),
            "unknown_products": sorted(self.unknown_products),
            "unknown_models": sorted(self.unknown_models),
            "skipped_windows": sorted(self.skipped_windows),
            "cost_only": sorted(self.cost_only),
            "empty_days": self.empty_days,
            "raw_files": list(self.raw_files),
            "published_counts": dict(self.published_counts),
            "failures": list(self.failures),
            "unknown_fields": [dict(item) for item in self.unknown_fields],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"

    def write(self, data_dir: Path) -> Path:
        path = Path(data_dir) / "runs" / (self.batch_id + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(self.to_json(), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return path

    @classmethod
    def read(cls, path: Path) -> "RunManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["pulled_at"] = parse_timestamp(data["pulled_at"])
        data.pop("failure_kind", None)  # derived from the status when written
        return cls(**data)
