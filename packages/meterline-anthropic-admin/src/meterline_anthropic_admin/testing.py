"""A fake Anthropic Admin API for testing code that uses ``meterline_anthropic_admin.run``, with no key and no network.

``FakeAPI`` answers ``GET /v1/organizations/usage_report/messages`` and ``GET /v1/organizations/cost_report`` on an
``httpx.MockTransport`` in the documented response shapes. Everything it serves is synthetic: with no argument it serves
the small dataset of ``sample_dataset()`` (invented API keys and models, 2026-09-01 to 2026-09-14). It proves that your
code drives the connector and reads its records; it does not prove live authentication, live response shapes or rate
limits.

Use it with ``run``::

    from datetime import datetime, timezone
    from meterline_anthropic_admin import run
    from meterline_anthropic_admin.testing import FakeAPI

    fake = FakeAPI()  # expects the synthetic key in fake.api_key unless you pass api_key=
    result = run("backfill", since="2026-09-01", provider_account_id="my-org", data_dir="out",
                 credentials={"ANTHROPIC_ADMIN_KEY": fake.api_key}, transport=fake.transport,
                 now=lambda: datetime(2026, 9, 15, 6, tzinfo=timezone.utc))

How it behaves:

* the table holds rows at the finest grain (day, key, workspace, model, tier); a query sums them over the
  dimensions it does not group by and shows those dimensions as null;
* a page holds ``limit`` day buckets (default 7, at most 31), every day of the range has a bucket, empty or not;
* ``cost_report`` amounts are decimal strings in cents;
* scripted faults (``fail``), a repeating cursor (``repeat_cursor``), a replayed page (``replay``) and a request log with
  the injected clock.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

USAGE_PATH = "/v1/organizations/usage_report/messages"
COST_PATH = "/v1/organizations/cost_report"
USAGE_DIMS = ("api_key_id", "workspace_id", "model", "service_tier", "context_window", "inference_geo", "speed")
MAX_BUCKETS = 31
DEFAULT_BUCKETS = 7
SAMPLE_KEY = "sk" + "-ant-admin01-synthetic-not-a-real-key"  # a made-up value, not a key


@dataclass
class LogEntry:
    time: Any
    path: str
    params: Dict[str, List[str]]
    status: int
    headers: Dict[str, str]


@dataclass
class _Fault:
    path: str
    status: int
    times: int
    retry_after: Optional[Any] = None


def urow(day: str, key: Optional[str], model: Optional[str], *, ws: Optional[str] = "wrkspc_a", tier: str = "standard",
         uncached: int = 0, c5: int = 0, c1: int = 0, read: int = 0, out: int = 0, web: Optional[int] = None) -> Dict[str, Any]:
    """One raw usage row; unnamed counts are 0."""
    return {"day": day, "api_key_id": key, "workspace_id": ws, "model": model, "service_tier": tier,
            "context_window": "0-200k", "inference_geo": "global", "speed": "standard",
            "uncached": uncached, "c5": c5, "c1": c1, "read": read, "out": out, "web": web}


def crow(day: str, model: Optional[str], token_type: Optional[str], cents: str, *, cost_type: str = "tokens",
         ws: Optional[str] = "wrkspc_a", description: Optional[str] = None) -> Dict[str, Any]:
    """One raw cost row; ``cents`` is the amount as the API sends it, a decimal string in cents."""
    return {"day": day, "model": model, "token_type": token_type, "amount": cents, "cost_type": cost_type,
            "workspace_id": ws, "description": description or ("%s %s" % (model, token_type) if model else cost_type)}


def _day(text: str) -> date:
    return date.fromisoformat(text[:10])


def _stamp(day: date) -> str:
    return day.isoformat() + "T00:00:00Z"


def sample_dataset() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The synthetic ``(usage_rows, cost_rows)`` that ``FakeAPI()`` serves: three API keys, two models, the days
    2026-09-01 to 2026-09-14, plus console rows without a key. Token counts follow a fixed formula of the day number,
    so the same call always gives the same rows; nothing is random and nothing reads the clock."""
    usage: List[Dict[str, Any]] = []
    cost: List[Dict[str, Any]] = []
    keys = ("apikey_demo_a", "apikey_demo_b", "apikey_demo_c")
    models = ("model-example-large", "model-example-small")
    for n in range(14):
        day = date(2026, 9, 1 + n).isoformat()
        for k, key in enumerate(keys):
            model = models[(n + k) % 2]
            usage.append(urow(day, key, model, uncached=1000 + 37 * n + 100 * k, c5=200 + 11 * n, c1=50 * k, read=500 + 23 * n,
                              out=100 + 5 * n + 10 * k, web=k if k else None))
        usage.append(urow(day, None, models[0], ws=None, uncached=40 + n, out=4 + n))
        for model in models:
            cost.append(crow(day, model, "uncached_input_tokens", "%d.%d" % (100 + 3 * n, 10 + n)))
            cost.append(crow(day, model, "output_tokens", "%d.5" % (20 + n)))
    return usage, cost


class FakeAPI:
    """The fake API. ``usage_rows`` and ``cost_rows`` default to ``sample_dataset()``; ``api_key`` is the key the fake
    accepts (a request with another key gets a 401); ``clock`` only stamps the request log."""

    def __init__(self, usage_rows: Optional[List[Dict[str, Any]]] = None, cost_rows: Optional[List[Dict[str, Any]]] = None, *,
                 api_key: str = SAMPLE_KEY, clock: Callable[[], Any] = time.monotonic, repeat_cursor: bool = False,
                 force_limit: Optional[int] = None) -> None:
        if usage_rows is None and cost_rows is None:
            usage_rows, cost_rows = sample_dataset()
        self.usage_rows = usage_rows or []
        self.cost_rows = cost_rows or []
        self.api_key = api_key
        self.clock = clock
        self.repeat_cursor = repeat_cursor
        self.force_limit = force_limit  # the most buckets a page holds, whatever ``limit`` asks for
        self.log: List[LogEntry] = []
        self.faults: List[_Fault] = []
        self.replays: Dict[str, int] = {}  # path -> later pages still to be answered with the first page's buckets
        self.extra_usage_fields: Dict[str, Any] = {}  # planted into every usage result
        self.extra_cost_fields: Dict[str, Any] = {}
        self.drop_usage_fields: List[str] = []  # removed from every usage result
        self.transport = httpx.MockTransport(self._handle)

    def fail(self, path: str, status: int, times: int = 1, retry_after: Optional[Any] = None) -> None:
        self.faults.append(_Fault(path, status, times, retry_after))

    def replay(self, path: str, times: int = 1) -> None:
        """Answer the next ``times`` requests for a later page of ``path`` with the day buckets of the first page, keeping
        the paging fields of the page asked for: the same days arrive twice in one run. A window needs two pages for
        this (``force_limit`` makes a page hold fewer buckets)."""
        self.replays[path] = self.replays.get(path, 0) + times

    def requests_for(self, path: str) -> List[LogEntry]:
        return [e for e in self.log if e.path == path]

    # ---- serving ---------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params: Dict[str, List[str]] = {}
        for name, value in request.url.params.multi_items():
            params.setdefault(name, []).append(value)
        headers = {k.lower(): v for k, v in request.headers.items()}
        response = self._respond(path, params, headers)
        self.log.append(LogEntry(self.clock(), path, params, response.status_code, headers))
        return response

    def _respond(self, path: str, params: Dict[str, List[str]], headers: Dict[str, str]) -> httpx.Response:
        for fault in self.faults:
            if fault.path == path and fault.times > 0:
                fault.times -= 1
                extra = {} if fault.retry_after is None else {"retry-after": str(fault.retry_after)}
                return httpx.Response(fault.status, headers=extra, json={"type": "error", "error": {"type": "api_error", "message": "scripted"}})
        if headers.get("x-api-key") != self.api_key:
            return self._error(401, "authentication_error", "invalid x-api-key")
        if not headers.get("anthropic-version"):
            return self._error(400, "invalid_request_error", "anthropic-version header is required")
        if path not in (USAGE_PATH, COST_PATH):
            return self._error(404, "not_found_error", "no such route")
        if "starting_at" not in params:
            return self._error(400, "invalid_request_error", "starting_at is required")
        limit = int((params.get("limit") or [DEFAULT_BUCKETS])[0])
        if not 1 <= limit <= MAX_BUCKETS:
            return self._error(400, "invalid_request_error", "limit must be between 1 and %d" % MAX_BUCKETS)
        first = _day(params["starting_at"][0])
        last = _day(params["ending_at"][0]) if "ending_at" in params else first + timedelta(days=DEFAULT_BUCKETS)
        days = [first + timedelta(days=n) for n in range((last - first).days)]
        limit = min(limit, self.force_limit or limit)
        start = self._start_of(params.get("page"))
        first_bucket = start
        if start and self.replays.get(path, 0) > 0:
            self.replays[path] -= 1
            first_bucket = 0
        chunk = days[first_bucket:first_bucket + limit]
        more = start + limit < len(days)
        group_by = params.get("group_by[]", [])
        build = self._usage_results if path == USAGE_PATH else self._cost_results
        data = [{"starting_at": _stamp(d), "ending_at": _stamp(d + timedelta(days=1)), "results": build(d, group_by, params)}
                for d in chunk]
        cursor = "page_%d" % (start + limit)
        if self.repeat_cursor:
            cursor = "page_loop"
        return httpx.Response(200, json={"data": data, "has_more": more, "next_page": cursor if more else None})

    @staticmethod
    def _start_of(page: Optional[List[str]]) -> int:
        if not page:
            return 0
        return 0 if page[0] == "page_loop" else int(page[0].split("_")[1])

    @staticmethod
    def _error(status: int, kind: str, message: str) -> httpx.Response:
        return httpx.Response(status, json={"type": "error", "error": {"type": kind, "message": message}})

    def _usage_results(self, day: date, group_by: List[str], params: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        wanted_ws = params.get("workspace_ids[]")
        sums: Dict[tuple, Dict[str, Any]] = {}
        for r in self.usage_rows:
            if _day(r["day"]) != day or (wanted_ws and r["workspace_id"] not in wanted_ws):
                continue
            dims = tuple(r[d] if d in group_by else None for d in USAGE_DIMS)
            acc = sums.setdefault(dims, {"uncached": 0, "c5": 0, "c1": 0, "read": 0, "out": 0, "web": None})
            for name in ("uncached", "c5", "c1", "read", "out"):
                acc[name] += r[name]
            if r["web"] is not None:
                acc["web"] = (acc["web"] or 0) + r["web"]
        results = []
        for dims, m in sorted(sums.items(), key=lambda kv: [str(x) for x in kv[0]]):
            result: Dict[str, Any] = {
                "uncached_input_tokens": m["uncached"],
                "cache_creation": {"ephemeral_5m_input_tokens": m["c5"], "ephemeral_1h_input_tokens": m["c1"]},
                "cache_read_input_tokens": m["read"],
                "output_tokens": m["out"],
                "server_tool_use": {"web_search_requests": m["web"] or 0},
            }
            result.update(zip(USAGE_DIMS, dims, strict=True))
            result["account_id"] = None
            result["service_account_id"] = None
            for name in self.drop_usage_fields:
                result.pop(name, None)
            result.update(self.extra_usage_fields)
            results.append(result)
        return results

    def _cost_results(self, day: date, group_by: List[str], params: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        sums: Dict[tuple, Decimal] = {}
        for r in self.cost_rows:
            if _day(r["day"]) != day:
                continue
            by_description = "description" in group_by
            ws = r["workspace_id"] if "workspace_id" in group_by else None
            key = (r["cost_type"], r["description"] if by_description else None, r["model"] if by_description else None,
                   r["token_type"] if by_description else None, ws)
            sums[key] = sums.get(key, Decimal(0)) + Decimal(r["amount"])
        results = []
        for (cost_type, description, model, token_type, ws), amount in sorted(sums.items(), key=lambda kv: [str(x) for x in kv[0]]):
            result: Dict[str, Any] = {
                "amount": format(amount, "f"), "currency": "USD", "cost_type": cost_type, "description": description,
                "model": model, "token_type": token_type, "service_tier": None, "context_window": None,
                "inference_geo": None, "speed": None, "workspace_id": ws,
            }
            result.update(self.extra_cost_fields)
            results.append(result)
        return results


@dataclass
class Clock:
    """Controllable time source; ``sleep`` advances it and is recorded."""

    now: Any = 1000
    sleeps: List[Any] = field(default_factory=list)

    def __call__(self) -> Any:
        return self.now

    def sleep(self, seconds: Any) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def skeleton(value: Any) -> Any:
    """Keys and nesting of a JSON value, scalars as '.', lists as the union of their items: what a shape check compares."""
    if isinstance(value, dict):
        return {k: skeleton(v) for k, v in value.items()}
    if isinstance(value, list):
        merged: Dict[str, Any] = {}
        for item in value:
            piece = skeleton(item)
            if isinstance(piece, dict):
                merged.update(piece)
        return [merged] if merged else []
    return "."


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)
