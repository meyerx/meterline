"""Fetcher: calls the GitHub API through a ``CopilotClient`` and stores every
response exactly as received.

Raw files, all gzip JSON with deterministic bytes (mtime 0):

* ``raw/users_1_day/<day>/<stamp>.json.gz`` and ``raw/aggregate_1_day/<day>/...``:
  ``{"endpoint", "provider_account_id", "level", "slug", "day", "status", "request_url",
  "download_links", "files": [<NDJSON text>, ...], "pulled_at",
  "data_refreshed_at"}``; ``status`` is 200 or 204 (no report);
* ``raw/billing_day/<day>/...`` and ``raw/billing_user_month/<YYYY-MM>/<hash of
  the login>/...``: ``{..., "year", "month", "day", "user", "pages": [{"status",
  "body": <text>}]}``;
* ``raw/seats/<pull date>/...`` and ``raw/seat_summary/<pull date>/...``: pages.

No request header and no token is stored; the signed download links are kept
without their query string (the signature lives there); file names carry
hashes, never logins. Nothing here reads the wall clock: callers pass
``pulled_at`` (tz-aware UTC). Logs carry counts and statuses only.
"""

import gzip
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from meterline.run import requested_command

from .client import ApiError, CopilotClient, Page, clean_url
from .normalise import parse_ndjson, parse_timestamp

logger = logging.getLogger("meterline_copilot.fetch")

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
SEATS_PAGE_SIZE = 100
MAX_PAGES = 10000  # pages of one query; more is an error, not a long wait
EARLIEST_REPORT_DAY = date(2025, 10, 10)


class BadShape(Exception):
    """A response body does not hold what the documented shape promises."""


class PaginationError(BadShape):
    """The pages or files of one query do not add up: one repeats, one is
    missing, or there are more than the cap. Nothing from the query is used."""


def batch_id_for(pulled_at: datetime) -> str:
    return _utc(pulled_at).strftime(STAMP_FORMAT)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("pulled_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def login_tag(login: str) -> str:
    return hashlib.sha256(login.lower().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Level:
    """The organization or enterprise a run reads."""

    kind: str
    slug: str

    def __post_init__(self) -> None:
        if self.kind not in ("organization", "enterprise"):
            raise ValueError("level must be organization or enterprise")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.slug or ""):
            raise ValueError("slug is empty or has characters a login or slug cannot have")

    @property
    def provider_account_id(self) -> str:
        return ("org:" if self.kind == "organization" else "enterprise:") + self.slug

    @property
    def _slug(self) -> str:
        return quote(self.slug, safe="")

    @property
    def root(self) -> str:
        return ("/orgs/" if self.kind == "organization" else "/enterprises/") + self._slug

    @property
    def billing_root(self) -> str:
        return ("/organizations/" if self.kind == "organization" else "/enterprises/") + self._slug

    def report_path(self, kind: str) -> str:
        if kind == "users":  # noqa: SIM108 - one branch per report family reads better than a nested conditional
            name = "users-1-day"
        else:
            name = "organization-1-day" if self.kind == "organization" else "enterprise-1-day"
        return "%s/copilot/metrics/reports/%s" % (self.root, name)


@dataclass(frozen=True)
class RawPull:
    path: Path
    endpoint: str
    label: str
    pages: int


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


def _write_raw(data_dir: Path, level: Level, endpoint: str, label: str, sub: Optional[str], pulled_at: datetime,
               payload: Dict[str, Any]) -> Path:
    folder = Path(data_dir) / "raw" / endpoint / label
    if sub:
        folder = folder / sub
    path = folder / (_utc(pulled_at).strftime(STAMP_FORMAT) + ".json.gz")
    if path.exists():
        raise RawFileExists(
            "raw file %s already exists: another pull ran in the same second, and raw files are never replaced"
            % path.relative_to(data_dir).as_posix()
        )
    body = {
        "endpoint": endpoint, "provider_account_id": level.provider_account_id, "level": level.kind, "slug": level.slug,
        "pulled_at": _utc(pulled_at).strftime(TS_FORMAT), "data_refreshed_at": None,
    }
    body.update(payload)
    _gzip_json(path, body)
    return path


# ---- reports (A and B) ---------------------------------------------------------------


def fetch_report(client: CopilotClient, data_dir: Path, level: Level, kind: str, day: date,
                 pulled_at: datetime) -> RawPull:
    """One usage report for one day: the call, then every download link at
    once. A 403 or 404 from the download host restarts the day from the
    report call once, then fails the run."""
    endpoint = "users_1_day" if kind == "users" else "aggregate_1_day"
    path = level.report_path(kind)
    last: Optional[ApiError] = None
    for _attempt in range(2):
        page = client.get_page(path, {"day": day.isoformat()})
        if page.status == 204:
            raw_path = _write_raw(data_dir, level, endpoint, day.isoformat(), None, pulled_at, {
                "day": day.isoformat(), "status": 204, "request_url": page.url, "download_links": [], "files": [],
            })
            logger.info("%s %s: no report (204)", endpoint, day.isoformat())
            return RawPull(raw_path, endpoint, day.isoformat(), 1)
        links = page.body.get("download_links") if isinstance(page.body, dict) else None
        if not isinstance(links, list) or not all(isinstance(u, str) for u in links):
            raise BadShape("%s %s: the report reply has no download_links list" % (endpoint, day.isoformat()))
        names = [clean_url(u, keep_query=False) for u in links]
        if len(set(names)) != len(names):
            raise PaginationError("%s %s: a download link is listed twice" % (endpoint, day.isoformat()))
        files: List[str] = []
        try:
            for link in links:
                files.append(client.download(link))
        except ApiError as exc:
            if exc.status in (403, 404):
                last = exc
                logger.warning("%s %s: download answered %d, asking for the report again", endpoint, day.isoformat(), exc.status)
                continue
            raise
        filled = [text for text in files if text.strip()]
        if len(set(filled)) != len(filled):
            raise PaginationError("%s %s: two download links gave the same content" % (endpoint, day.isoformat()))
        for index, text in enumerate(files):
            try:
                parse_ndjson(text, "%s %s file %d" % (endpoint, day.isoformat(), index + 1))
            except ValueError as exc:
                raise BadShape(str(exc)) from None
        raw_path = _write_raw(data_dir, level, endpoint, day.isoformat(), None, pulled_at, {
            "day": day.isoformat(), "status": 200, "request_url": page.url,
            "download_links": [clean_url(u, keep_query=False) for u in links], "files": files,
        })
        logger.info("%s %s: %d files", endpoint, day.isoformat(), len(files))
        return RawPull(raw_path, endpoint, day.isoformat(), 1 + len(files))
    assert last is not None  # nosec B101 # noqa: S101 - for the type checker only: the loop ends only after a 403/404 set it
    raise last


# ---- billing (C) -------------------------------------------------------------------------


def fetch_billing(client: CopilotClient, data_dir: Path, level: Level, year: int, month: int, *,
                  day: Optional[int] = None, user: Optional[str] = None, pulled_at: datetime) -> RawPull:
    params: Dict[str, Any] = {"year": str(year), "month": str(month)}
    if day is not None:
        params["day"] = str(day)
    if user is not None:
        params["user"] = user
    page = client.get_page(level.billing_root + "/settings/billing/ai_credit/usage", params, billing=True)
    if not isinstance(page.body, dict) or not isinstance(page.body.get("usageItems"), list):
        raise BadShape("billing %04d-%02d: the reply has no usageItems list" % (year, month))
    payload = {
        "year": year, "month": month, "day": day, "user": user, "request_url": _without_user(page.url),
        "pages": [{"status": page.status, "body": page.text}],
    }
    if user is None:
        label, sub, endpoint = "%04d-%02d-%02d" % (year, month, day or 0), None, "billing_day"
    else:
        label, sub, endpoint = "%04d-%02d" % (year, month), login_tag(user), "billing_user_month"
    raw_path = _write_raw(data_dir, level, endpoint, label, sub, pulled_at, payload)
    logger.info("%s %s: 1 page", endpoint, label)
    return RawPull(raw_path, endpoint, label, 1)


def _without_user(url: str) -> str:
    """The request URL with no ``user`` query parameter: a login is data, not a log line."""
    return re.sub(r"([?&])user=[^&]*&?", r"\1", url).rstrip("?&")


# ---- seats (D and E) ----------------------------------------------------------------------


def _wants_next(page: Page) -> bool:
    return 'rel="next"' in page.headers.get("link", "")


def fetch_seats(client: CopilotClient, data_dir: Path, level: Level, pulled_at: datetime) -> RawPull:
    pages: List[Page] = []
    number = 1
    while True:
        page = client.get_page(level.root + "/copilot/billing/seats", {"page": str(number), "per_page": str(SEATS_PAGE_SIZE)})
        body = page.body
        if not isinstance(body, dict) or not isinstance(body.get("seats"), list):
            raise BadShape("seats: the reply has no seats list")
        if pages and page.text == pages[-1].text:
            raise PaginationError("seats: page %d is the same as page %d" % (number, number - 1))
        pages.append(page)
        if not _wants_next(page):
            break
        number += 1
        if number > MAX_PAGES:
            raise PaginationError("seats: more than %d pages in one query" % MAX_PAGES)
    # total_seats counts each person once; fewer people than that means a page went missing
    total = pages[0].body.get("total_seats")
    if isinstance(total, int) and not isinstance(total, bool):
        people = {
            str(seat["assignee"].get("id")) for p in pages for seat in p.body["seats"]
            if isinstance(seat, dict) and isinstance(seat.get("assignee"), dict)
        }
        unheld = sum(1 for p in pages for seat in p.body["seats"] if isinstance(seat, dict) and seat.get("assignee") is None)
        if len(people) + unheld < total:  # a seat nobody holds counts in total_seats but names no one
            held = "%d distinct people" % len(people) + (" and %d seats nobody holds," % unheld if unheld else "")
            raise PaginationError("seats: the pages hold %s but total_seats says %d" % (held.rstrip(","), total))
    label = _utc(pulled_at).date().isoformat()
    raw_path = _write_raw(data_dir, level, "seats", label, None, pulled_at, {
        "request_url": pages[0].url, "pages": [{"status": p.status, "body": p.text} for p in pages],
    })
    logger.info("seats %s: %d pages", label, len(pages))
    return RawPull(raw_path, "seats", label, len(pages))


def fetch_seat_summary(client: CopilotClient, data_dir: Path, level: Level, pulled_at: datetime) -> RawPull:
    page = client.get_page(level.root + "/copilot/billing")
    if not isinstance(page.body, dict) or not isinstance(page.body.get("seat_breakdown"), dict):
        raise BadShape("seat summary: the reply has no seat_breakdown")
    label = _utc(pulled_at).date().isoformat()
    raw_path = _write_raw(data_dir, level, "seat_summary", label, None, pulled_at, {
        "request_url": page.url, "pages": [{"status": page.status, "body": page.text}],
    })
    logger.info("seat_summary %s: 1 page", label)
    return RawPull(raw_path, "seat_summary", label, 1)


# ---- run manifest ----------------------------------------------------------------------------


@dataclass
class RunManifest:
    """Record of one run, written to ``runs/<batch_id>.json``."""

    command: str
    batch_id: str
    pulled_at: datetime
    status: str = "published"  # "published" or "failed"
    level: str = ""
    provider_account_id: str = ""
    windows: List[str] = field(default_factory=list)
    pages: int = 0
    row_counts: Dict[str, int] = field(default_factory=dict)
    totals: Dict[str, str] = field(default_factory=dict)
    unknown_products: List[str] = field(default_factory=list)
    unknown_models: List[str] = field(default_factory=list)
    unknown_units: List[str] = field(default_factory=list)
    feature_sum_mismatch: List[str] = field(default_factory=list)
    aggregate_missing: List[str] = field(default_factory=list)
    seats_without_assignee: int = 0  # seats with a null assignee: kept out of the seat assignments
    ai_credits_absent: int = 0  # per-user rows before 2026-06-19 without ai_credits_used: no credit quantity
    unread_token_families: Dict[str, int] = field(default_factory=dict)  # tokens in families no record is built from
    notes: Dict[str, str] = field(default_factory=dict)
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
            "level": self.level,
            "provider_account_id": self.provider_account_id,
            "windows": list(self.windows),
            "pages": self.pages,
            "row_counts": dict(self.row_counts),
            "totals": dict(self.totals),
            "unknown_products": sorted(self.unknown_products),
            "unknown_models": sorted(self.unknown_models),
            "unknown_units": sorted(self.unknown_units),
            "feature_sum_mismatch": sorted(self.feature_sum_mismatch),
            "aggregate_missing": sorted(self.aggregate_missing),
            "seats_without_assignee": self.seats_without_assignee,
            "ai_credits_absent": self.ai_credits_absent,
            "unread_token_families": dict(sorted(self.unread_token_families.items())),
            "notes": dict(self.notes),
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
        if "provider_account_id" not in data and "org_id" in data:  # compatibility: a manifest written before the MF-13 rename
            data["provider_account_id"] = data.pop("org_id")
        data["pulled_at"] = parse_timestamp(data["pulled_at"])
        data.pop("failure_kind", None)  # derived from the status when written
        data.setdefault("requested", None)  # a manifest written before auto existed has no requested
        return cls(**data)
