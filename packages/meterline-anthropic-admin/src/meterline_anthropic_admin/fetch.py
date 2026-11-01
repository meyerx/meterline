"""Fetcher: calls the API through an ``AdminClient`` and stores every response page exactly as received.

Raw file: ``<data_dir>/raw/<endpoint>/<window>/<pulled_at>.json.gz`` holding ``{"endpoint", "window", "request_url",
"pulled_at", "provider_account_id", "workspace_id", "pages": [{"status", "body": <text>}]}``. ``endpoint`` is
``usage_report_messages`` or ``cost_report``; ``provider_account_id`` is the label of the run and ``workspace_id`` the filter it
ran with (null for none), so ``rebuild`` needs neither the API nor the command line. No request header and no key is
stored. Gzip output is deterministic (mtime 0).

Nothing here reads the wall clock: callers pass ``pulled_at`` (tz-aware UTC). Logs carry counts and statuses only.
"""

import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from meterline import http

from .client import COST_PATH, USAGE_PATH, AdminClient, Page
from .normalise import parse_timestamp

logger = logging.getLogger("meterline_anthropic_admin.fetch")

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
MAX_BUCKETS = 31  # the most day buckets one page holds, and so the longest window of one query

USAGE = "usage_report_messages"
COST = "cost_report"
PATHS = {USAGE: USAGE_PATH, COST: COST_PATH}
# the usage query keeps the service tier so Priority Tier tokens can be named; the other dimensions are summed away
USAGE_GROUP_BY = ["api_key_id", "workspace_id", "model", "service_tier"]
COST_GROUP_BY = ["description", "workspace_id"]


def batch_id_for(pulled_at: datetime) -> str:
    return _utc(pulled_at).strftime(STAMP_FORMAT)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("pulled_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def window_label(first: date, last: date) -> str:
    return first.isoformat() + "_" + last.isoformat()


def forward_windows(start: date, end: date, days: int = MAX_BUCKETS) -> List[Tuple[date, date]]:
    """Inclusive windows of at most ``days`` days covering ``start..end``, oldest first."""
    out: List[Tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=days - 1), end)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def backward_windows(start: date, end: date, days: int = MAX_BUCKETS) -> List[Tuple[date, date]]:
    """Inclusive windows of at most ``days`` days walking back from ``end`` to ``start``, newest first."""
    out: List[Tuple[date, date]] = []
    cursor = end
    while cursor >= start:
        first = max(cursor - timedelta(days=days - 1), start)
        out.append((first, cursor))
        cursor = first - timedelta(days=1)
    return out


@dataclass(frozen=True)
class RawPull:
    path: Path
    endpoint: str
    window: str
    page_count: int


class RawFileExists(ValueError):
    """The raw file a pull would write is already there: another pull ran in the same UTC second. Raw files are the
    evidence the records were built from and are never replaced."""


def _gzip_json(path: Path, obj: Dict[str, Any]) -> None:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
        gz.write(payload)
    os.replace(tmp, path)


def query_params(endpoint: str, first: date, last: date, workspace_id: Optional[str]) -> Dict[str, Any]:
    """The query of one window: day buckets from ``first`` up to and including ``last`` (``ending_at`` is exclusive)."""
    params: Dict[str, Any] = {
        "starting_at": first.isoformat() + "T00:00:00Z",
        "ending_at": (last + timedelta(days=1)).isoformat() + "T00:00:00Z",
        "bucket_width": "1d",
        "limit": MAX_BUCKETS,
        "group_by[]": list(USAGE_GROUP_BY if endpoint == USAGE else COST_GROUP_BY),
    }
    if endpoint == USAGE and workspace_id:
        params["workspace_ids[]"] = [workspace_id]  # the cost report has no workspace filter; the normaliser applies it
    return params


def _write_raw(data_dir: Path, endpoint: str, window: str, pulled_at: datetime, pages: List[Page], provider_account_id: str,
               workspace_id: Optional[str]) -> Path:
    path = Path(data_dir) / "raw" / endpoint / window / (_utc(pulled_at).strftime(STAMP_FORMAT) + ".json.gz")
    if path.exists():
        raise RawFileExists(
            "raw file %s already exists: another pull ran in the same second, and raw files are never replaced"
            % path.relative_to(data_dir).as_posix()
        )
    _gzip_json(path, {
        "endpoint": endpoint,
        "window": window,
        "request_url": http.without_userinfo(pages[0].url),
        "pulled_at": _utc(pulled_at).strftime(TS_FORMAT),
        "provider_account_id": provider_account_id,
        "workspace_id": workspace_id,
        "pages": [{"status": p.status, "body": p.text} for p in pages],
    })
    return path


def fetch_endpoint(
    client: AdminClient, data_dir: Path, endpoint: str, *, first: date, last: date, pulled_at: datetime,
    provider_account_id: str, workspace_id: Optional[str],
) -> RawPull:
    """One report over one window (at most 31 days), every page stored as received."""
    pages = client.fetch_pages(PATHS[endpoint], query_params(endpoint, first, last, workspace_id))
    label = window_label(first, last)
    raw_path = _write_raw(data_dir, endpoint, label, pulled_at, pages, provider_account_id, workspace_id)
    logger.info("%s %s: %d pages", endpoint, label, len(pages))
    return RawPull(raw_path, endpoint, label, len(pages))


@dataclass
class RunManifest:
    """Record of one run, written to ``runs/<batch_id>.json``. Figures and file names only, never an id or a key."""

    command: str
    batch_id: str
    pulled_at: datetime
    status: str = "published"  # "published" or "failed"
    windows: List[str] = field(default_factory=list)
    pages: int = 0
    row_counts: Dict[str, int] = field(default_factory=dict)
    totals: Dict[str, str] = field(default_factory=dict)
    empty_days: int = 0  # days loaded from an answer with no rows that can no longer change
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
