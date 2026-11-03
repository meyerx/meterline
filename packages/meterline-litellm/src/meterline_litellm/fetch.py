"""Fetcher: calls the proxy through a ``LitellmClient`` and stores every
response page exactly as received.

Raw file: ``<data_dir>/raw/<endpoint>/<window>/<view>/<pulled_at>.json.gz``
holding ``{"endpoint", "window", "view", "entity", "request_url", "pulled_at",
"data_refreshed_at", "proxy_name", "pages": [{"status", "body": <text>}]}``.
``view`` is ``global`` or ``entity`` (a per-user or per-end-user call, its id in
``entity``); the file name carries only a hash of that id. No request header
and no key is stored. Gzip output is deterministic (mtime 0).

Nothing here reads the wall clock: callers pass ``pulled_at`` (tz-aware UTC).
Logs carry counts and statuses only.
"""

import gzip
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from meterline import http
from meterline.run import requested_command

from .client import ApiError, LitellmClient, Page, PaginationError
from .normalise import parse_timestamp, read_raw, result_fingerprint

logger = logging.getLogger("meterline_litellm.fetch")

MIN_VERSION = "v1.67.0-stable"
PATH_NOT_FOUND = "path not found: wrong base URL, or a proxy older than " + MIN_VERSION

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
ACTIVITY_PAGE_SIZE = 1000
USER_LIST_PAGE_SIZE = 100
MAX_PAGES = 10000  # pages of one query; more is an error, not a long wait

PATH_USER = "user/daily/activity"
PATH_CUSTOMER = "customer/daily/activity"
PATH_USER_LIST = "user/list"
ENDPOINT_NAMES = {
    PATH_USER: "user_daily_activity",
    PATH_CUSTOMER: "customer_daily_activity",
    PATH_USER_LIST: "user_list",
}

UNASSIGNED_KEYS = ("", "Unassigned")


class PathNotFound(Exception):
    """The proxy answered 404 on ``/user/daily/activity``."""


def batch_id_for(pulled_at: datetime) -> str:
    return _utc(pulled_at).strftime(STAMP_FORMAT)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("pulled_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def window_label(first: date, last: Optional[date] = None) -> str:
    if last is None:
        return first.isoformat()
    return first.isoformat() + "_" + last.isoformat()


def forward_windows(start: date, end: date, days: int = 31) -> List[Tuple[date, date]]:
    """Inclusive windows of at most ``days`` days covering ``start..end``, oldest first."""
    out: List[Tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=days - 1), end)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def backward_windows(start: date, end: date, days: int = 31) -> List[Tuple[date, date]]:
    """Inclusive windows of at most ``days`` days walking back from ``end``
    to ``start``, newest first."""
    out: List[Tuple[date, date]] = []
    cursor = end
    while cursor >= start:
        first = max(cursor - timedelta(days=days - 1), start)
        out.append((first, cursor))
        cursor = first - timedelta(days=1)
    return out


def entity_tag(entity: str) -> str:
    return hashlib.sha256(entity.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RawPull:
    path: Path
    endpoint: str
    window: str
    page_count: int
    entity: Optional[str] = None
    entities: Tuple[str, ...] = ()


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


def _clean_url(url: str) -> str:
    """The request URL without any user name or password of the base URL."""
    return http.without_userinfo(url)


def _write_raw(
    data_dir: Path, endpoint: str, window: str, view: str, entity: Optional[str],
    pulled_at: datetime, pages: List[Page], proxy_name: str,
) -> Path:
    tag = "global" if entity is None else entity_tag(entity)
    path = Path(data_dir) / "raw" / endpoint / window / tag / (_utc(pulled_at).strftime(STAMP_FORMAT) + ".json.gz")
    if path.exists():
        raise RawFileExists(
            "raw file %s already exists: another pull ran in the same second, and raw files are never replaced"
            % path.relative_to(data_dir).as_posix()
        )
    _gzip_json(path, {
        "endpoint": endpoint,
        "window": window,
        "view": view,
        "entity": entity,
        "request_url": _clean_url(pages[0].url),
        "pulled_at": _utc(pulled_at).strftime(TS_FORMAT),
        "data_refreshed_at": None,
        "proxy_name": proxy_name,
        "pages": [{"status": p.status, "body": p.text} for p in pages],
    })
    return path


def _all_pages(client: LitellmClient, path: str, params: Dict[str, Any], page_size: int) -> List[Page]:
    """Pages 1.. while ``metadata.has_more`` is true (activity endpoints) or
    ``page`` is below ``total_pages`` (user list)."""
    pages: List[Page] = []
    seen: Dict[str, int] = {}
    number = 1
    while True:
        query = dict(params)
        query["page"] = str(number)
        query["page_size"] = str(page_size)
        page = client.get_page(path, query)
        body = page.body
        # the answer carries the page it is: a different number, or the text of the page before, means the
        # server ignored the request, and a skipped or repeated page would change the record counts
        answered = body.get("page") if path == PATH_USER_LIST else (body.get("metadata") or {}).get("page")
        if isinstance(answered, int) and not isinstance(answered, bool) and answered != number:
            raise PaginationError("%s: asked for page %d, the server answered page %d" % (path, number, answered))
        if pages and page.text == pages[-1].text:
            raise PaginationError("%s: page %d is the same as page %d" % (path, number, number - 1))
        for result in (body.get("results") or []) if path != PATH_USER_LIST else ():
            # a replayed page can echo the right number: only its content shows it (see result_fingerprint)
            fingerprint = result_fingerprint(result)
            if fingerprint in seen:
                raise PaginationError("%s: page %d repeats the date %s of page %d"
                                      % (path, number, str(result.get("date"))[:10], seen[fingerprint]))
            seen[fingerprint] = number
        if path != PATH_USER_LIST and not isinstance(body.get("metadata"), dict):
            # without it the loop could not tell whether more pages follow, and would end quietly after this one
            raise PaginationError("%s: page %d has no metadata object, so the paging cannot be read" % (path, number))
        pages.append(page)
        if path == PATH_USER_LIST:  # noqa: SIM108 - two endpoints, two paging rules: kept as two branches
            more = number < int(body.get("total_pages") or 1)
        else:
            more = bool(body["metadata"].get("has_more"))
        if not more:
            return pages
        number += 1
        if number > MAX_PAGES:
            raise PaginationError("%s: more than %d pages in one query" % (path, MAX_PAGES))


def entities_of(raw_path: Path) -> List[str]:
    """Entity keys of ``breakdown.entities`` over all pages of a raw file, sorted."""
    import json as _json
    from decimal import Decimal

    found = set()
    for page in read_raw(raw_path)["pages"]:
        body = _json.loads(page["body"], parse_float=Decimal)
        for result in body.get("results") or []:
            found.update(((result.get("breakdown") or {}).get("entities") or {}).keys())
    return sorted(found)


def fetch_activity(
    client: LitellmClient,
    data_dir: Path,
    *,
    customer: bool,
    first: date,
    last: date,
    entity: Optional[str],
    pulled_at: datetime,
    proxy_name: str,
) -> RawPull:
    """One view of one window: the global view when ``entity`` is None, else
    the view of one user (``user_id``) or one end user (``end_user_ids``)."""
    path = PATH_CUSTOMER if customer else PATH_USER
    params: Dict[str, Any] = {"start_date": first.isoformat(), "end_date": last.isoformat()}
    if entity is not None:
        params["end_user_ids" if customer else "user_id"] = entity
    try:
        pages = _all_pages(client, path, params, ACTIVITY_PAGE_SIZE)
    except ApiError as exc:
        if exc.status == 404 and not customer:
            raise PathNotFound(PATH_NOT_FOUND) from None
        raise
    label = window_label(first, last)
    view = "global" if entity is None else "entity"
    raw_path = _write_raw(data_dir, ENDPOINT_NAMES[path], label, view, entity, pulled_at, pages, proxy_name)
    logger.info("%s %s %s: %d pages", ENDPOINT_NAMES[path], label, view, len(pages))
    return RawPull(raw_path, ENDPOINT_NAMES[path], label, len(pages), entity)


def fetch_user_list(
    client: LitellmClient, data_dir: Path, pulled_at: datetime, proxy_name: str
) -> RawPull:
    pages = _all_pages(client, PATH_USER_LIST, {}, USER_LIST_PAGE_SIZE)
    label = _utc(pulled_at).date().isoformat()
    raw_path = _write_raw(data_dir, "user_list", label, "list", None, pulled_at, pages, proxy_name)
    logger.info("user_list %s: %d pages", label, len(pages))
    return RawPull(raw_path, "user_list", label, len(pages))


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
    token_anomalies: List[str] = field(default_factory=list)
    modelless_days: List[str] = field(default_factory=list)
    skipped_entities: Dict[str, int] = field(default_factory=dict)
    empty_days: int = 0  # days loaded from an answer with no rows that can no longer change
    raw_files: List[str] = field(default_factory=list)
    published_counts: Dict[str, int] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    unknown_fields: List[Dict[str, Any]] = field(default_factory=list)  # fields the connector does not know: endpoint, path, count
    requested: Optional[str] = field(default_factory=requested_command)  # "auto" when auto chose the command; None otherwise

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
            "token_anomalies": sorted(self.token_anomalies),
            "modelless_days": sorted(self.modelless_days),
            "skipped_entities": dict(self.skipped_entities),
            "empty_days": self.empty_days,
            "raw_files": list(self.raw_files),
            "published_counts": dict(self.published_counts),
            "failures": list(self.failures),
            "unknown_fields": [dict(item) for item in self.unknown_fields],
            "requested": self.requested,
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
        data.setdefault("requested", None)  # a manifest written before auto existed has no requested
        return cls(**data)
