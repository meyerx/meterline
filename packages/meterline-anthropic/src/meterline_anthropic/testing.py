"""A fake Enterprise Analytics API for trying this connector without a key or a network.

It stands in for the real API on an ``httpx.MockTransport``: hand ``FakeAPI(...).transport`` to ``run``::

    from meterline_anthropic import run
    from meterline_anthropic.testing import FakeAPI

    fake = FakeAPI(api_key="demo")
    result = run("backfill", since="2026-09-01", data_dir="out", credentials={"ANTHROPIC_ANALYTICS_KEY": "demo"},
                 transport=fake.transport)

With no argument ``FakeAPI`` serves ``sample_dataset()``: 4 invented people (``@example.com`` addresses), 14 days
(2026-09-01 to 2026-09-14) and the totals the connector checks. All of it is synthetic and built in code; nothing
comes from a real account. Pass ``dataset=`` to serve your own data in the same structure. ``api_key=`` makes the
fake answer 401 to any other key.

The fake answers in the documented response shapes (``docs/api-shapes/``):

* ``user_usage_report`` / ``user_cost_report``: flat rows, each with its own ``starting_at`` / ``ending_at``;
  ``limit`` counts rows;
* ``usage_report`` / ``cost_report``: day buckets with ``results``; ``limit`` counts buckets;
* all four carry ``organization_id`` and ``data_refreshed_at`` at the top level;
* ``users`` pages with ``next_page`` only (no ``has_more``, no ``data_refreshed_at``, no ``organization_id``);
  ``summaries`` answers a whole range in one response (``starting_date`` inclusive, ``ending_date`` exclusive);
* a cursor is bound to every other query parameter (400 on mismatch).

Scripted faults, to see how a run copes: ``fail_page``, ``expire_cursor_on_page``, ``set_latest_activity_date``
(``users`` answers 400 for later dates), ``set_refreshed_at_for_window`` / ``omit_refreshed_at`` (a null or missing
``data_refreshed_at``), ``mutate(fn)`` (change the dataset between runs) and a request log (``log``,
``requests_for``) with the injected clock's time for each request.

What it does not prove: that a real key is accepted, that the live API still has these shapes, or how the live rate
limits behave. Check those against the real API.
"""

import base64
import copy
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

import httpx

__all__ = ["FakeAPI", "FakeApi", "LogEntry", "sample_dataset"]

DEFAULT_REFRESHED_AT = "2026-10-09T12:00:00Z"

USAGE_ENDPOINTS = {
    "user_usage_report": ("usage", True),
    "usage_report": ("org_usage", False),
}
COST_ENDPOINTS = {
    "user_cost_report": ("cost", True),
    "cost_report": ("org_cost", False),
}
KNOWN_ENDPOINTS = set(USAGE_ENDPOINTS) | set(COST_ENDPOINTS) | {"users", "summaries"}


SAMPLE_PROVIDER_ACCOUNT_ID = "org-synthetic-0001"
SAMPLE_FIRST_DAY = date(2026, 9, 1)
SAMPLE_LAST_DAY = date(2026, 9, 14)
SAMPLE_REFRESHED_AT = "2026-09-15T05:00:00Z"
_SAMPLE_USERS = (
    ("user-001", "Alex Archer", "alex.archer@example.com"),
    ("user-002", "Blake Brook", "blake.brook@example.com"),
    ("user-003", "Casey Clarke", "casey.clarke@example.com"),
    ("user-004", "Drew Dalton", "drew.dalton@example.com"),
)
_SAMPLE_MODELS = ("model-a-large", "model-b-small")
# integer micro-cents per token: input, output, cache write 5m, cache write 1h, cache read
_SAMPLE_RATES = {"model-a-large": (300, 1500, 375, 600, 30), "model-b-small": (100, 500, 125, 200, 10)}
_EDIT_TOOLS = ("edit_tool", "multi_edit_tool", "notebook_edit_tool", "write_tool")
_OFFICE_APPS = ("excel", "outlook", "powerpoint", "word")


def _cents(micro: int) -> str:
    return "%d.%06d" % (micro // 1_000_000, micro % 1_000_000)


def _zero_block(*names: str) -> Dict[str, int]:
    return dict.fromkeys(names, 0)


def _sample_activity(u: int, d: int, uid: str, email: str, key: str) -> Dict[str, Any]:
    """One user's activity row for one day of the sample."""
    chat_on = u % 2 == 0
    return {
        "user": {"type": "user", "id": uid, "email_address": email},
        "chat_metrics": {
            "distinct_conversation_count": 2 + u if chat_on else 0, "message_count": 10 + d if chat_on else 0,
            "distinct_artifacts_created_count": u % 3 if chat_on else 0,
            "distinct_skills_used_count": 0, "distinct_connectors_used_count": 0,
        },
        "claude_code_metrics": {
            "core_metrics": {
                "distinct_session_count": 1 + u, "commit_count": 2 + d % 5, "pull_request_count": u % 2,
                "artifacts_created_count": 0,
                "lines_of_code": {"added_count": 120 + 17 * d, "removed_count": 40 + 5 * u},
            },
            "tool_actions": {
                t: {"accepted_count": 3 + i + u, "rejected_count": i % 2} for i, t in enumerate(_EDIT_TOOLS)
            },
        },
        "cowork_metrics": _zero_block(
            "distinct_session_count", "message_count", "artifacts_created_count",
            "distinct_skills_used_count", "distinct_connectors_used_count",
        ),
        "office_metrics": {
            app: _zero_block(
                "distinct_session_count", "message_count", "distinct_skills_used_count",
                "distinct_connectors_used_count",
            )
            for app in _OFFICE_APPS
        },
        "design_metrics": {"distinct_session_count": None, "message_count": 0},
        "science_metrics": {"distinct_session_count": None, "message_count": 0},
        "web_search_count": 2 + u if chat_on else 0,
        "last_activity_date": key,
    }


def sample_dataset() -> Dict[str, Any]:
    """A small synthetic dataset: 4 invented people (``@example.com``), 2026-09-01 to 2026-09-14.

    It has the shapes of the documented responses (``docs/api-shapes/``), the same structure as the large fixture
    of the test suite, with the totals the connector checks (the organisation rows are the user rows plus some
    traffic that belongs to no user). It is built by arithmetic on the person and day numbers, so it is the same
    on every call: no random numbers, no clock. Everything is invented; none of it comes from a real account.
    """
    users = [{"user_id": uid, "email": email, "name": name, "deleted": False} for uid, name, email in _SAMPLE_USERS]
    usage: Dict[str, List[Dict[str, Any]]] = {}
    cost: Dict[str, List[Dict[str, Any]]] = {}
    activity: Dict[str, List[Dict[str, Any]]] = {}
    org_usage: Dict[str, List[Dict[str, Any]]] = {}
    org_cost: Dict[str, List[Dict[str, Any]]] = {}
    summaries: List[Dict[str, Any]] = []
    active_by_day: List[int] = []
    for d in range((SAMPLE_LAST_DAY - SAMPLE_FIRST_DAY).days + 1):
        day = SAMPLE_FIRST_DAY + timedelta(days=d)
        key = day.isoformat()
        usage[key], cost[key], activity[key] = [], [], []
        for u, (uid, name, email) in enumerate(_SAMPLE_USERS):
            if (u + d) % 4 == 3 or (day.weekday() >= 5 and u % 2):
                continue  # not active that day
            actor = {"type": "user_actor", "user_id": uid, "email_address": email, "name": name, "deleted": False}
            products = ("claude_code", "chat") if u % 2 == 0 else ("claude_code",)
            for p, product in enumerate(products):
                model = _SAMPLE_MODELS[(u + p + d) % 2]
                t_in, t_out = 20000 + 1300 * u + 70 * d, 4000 + 250 * p + 30 * d
                t_c5, t_c1, t_cr = 9000 + 100 * d, 500 * (u % 2), 150000 + 4000 * u + 900 * d
                reqs, web = 12 + 3 * u + d, (2 + u if product == "chat" else 0)
                rate = _SAMPLE_RATES[model]
                micro = t_in * rate[0] + t_out * rate[1] + t_c5 * rate[2] + t_c1 * rate[3] + t_cr * rate[4]
                usage[key].append({
                    "actor": actor, "product": product, "model": model,
                    "uncached_input_tokens": t_in, "output_tokens": t_out,
                    "cache_creation": {"ephemeral_5m_input_tokens": t_c5, "ephemeral_1h_input_tokens": t_c1},
                    "cache_read_input_tokens": t_cr, "total_tokens": t_in + t_out + t_c5 + t_c1 + t_cr,
                    "requests": reqs, "server_tool_use": {"web_search_requests": web},
                })
                cost[key].append({
                    "actor": actor, "product": product, "model": model, "amount": _cents(micro),
                    "list_amount": _cents(micro * 110 // 100), "currency": "USD", "requests": reqs,
                })
            activity[key].append(_sample_activity(u, d, uid, email, key))
        # the organisation rows: the user rows plus traffic through API keys that belongs to no user
        sums = [0] * 7
        for row in usage[key]:
            for i, value in enumerate((
                row["uncached_input_tokens"], row["output_tokens"],
                row["cache_creation"]["ephemeral_5m_input_tokens"],
                row["cache_creation"]["ephemeral_1h_input_tokens"],
                row["cache_read_input_tokens"], row["requests"], row["server_tool_use"]["web_search_requests"],
            )):
                sums[i] += value
        extra_in, extra_out, extra_reqs = 50000 + 100 * d, 12000, 40 + d
        extra_micro = extra_in * _SAMPLE_RATES["model-b-small"][0] + extra_out * _SAMPLE_RATES["model-b-small"][1]
        amount = sum(_micro(c["amount"]) for c in cost[key]) + extra_micro
        listed = sum(_micro(c["list_amount"]) for c in cost[key]) + extra_micro
        org_usage[key] = [{
            "uncached_input_tokens": sums[0] + extra_in, "output_tokens": sums[1] + extra_out,
            "cache_creation": {"ephemeral_5m_input_tokens": sums[2], "ephemeral_1h_input_tokens": sums[3]},
            "cache_read_input_tokens": sums[4], "requests": sums[5] + extra_reqs,
            "server_tool_use": {"web_search_requests": sums[6]},
        }]
        org_cost[key] = [{
            "amount": _cents(amount), "list_amount": _cents(listed), "currency": "USD",
            "requests": sums[5] + extra_reqs,
        }]
        active_by_day.append(len(activity[key]))
        summaries.append({
            "starting_at": key + "T00:00:00Z",
            "ending_at": (day + timedelta(days=1)).isoformat() + "T00:00:00Z",
            "assigned_seat_count": len(users), "pending_invite_count": d % 3,
            "daily_active_user_count": active_by_day[d],
            "weekly_active_user_count": len(users), "monthly_active_user_count": len(users),
        })
    return {
        "meta": {
            "synthetic": True, "start": SAMPLE_FIRST_DAY.isoformat(), "end": SAMPLE_LAST_DAY.isoformat(),
            "currency": "USD", "amount_unit": "cents (decimal strings, six decimals)",
        },
        "provider_account_id": SAMPLE_PROVIDER_ACCOUNT_ID, "users": users, "usage": usage, "cost": cost, "org_usage": org_usage,
        "org_cost": org_cost, "activity": activity, "summaries": summaries,
    }


def _micro(cents_text: str) -> int:
    whole, frac = cents_text.split(".")
    return int(whole) * 1_000_000 + int(frac)


@dataclass
class LogEntry:
    time: Any  # seconds, from the injected clock
    endpoint: str
    params: Dict[str, Any]
    page: int  # 1-based page number within the sequence; 0 when not paged
    status: int


@dataclass
class _Fault:
    status: int
    retry_after: Optional[Any]
    times: int


class FakeAPI:
    """Wraps a dataset dict (a private deep copy) in an httpx transport; with no dataset it serves
    ``sample_dataset()``."""

    def __init__(
        self,
        dataset: Optional[Dict[str, Any]] = None,
        *,
        page_limit: Optional[int] = None,
        clock: Callable[[], Any] = time.monotonic,
        api_key: Optional[str] = None,
        refreshed_at: Optional[str] = None,
    ) -> None:
        self.dataset = copy.deepcopy(dataset) if dataset is not None else sample_dataset()
        self.page_limit = page_limit
        self.clock = clock
        self.api_key = api_key
        if refreshed_at is None:
            refreshed_at = DEFAULT_REFRESHED_AT if dataset is not None else SAMPLE_REFRESHED_AT
        self.refreshed_at = refreshed_at
        self.latest_activity_date: Optional[date] = None
        self.omit_refreshed_at = False
        self._refreshed_for_window: Dict[str, Optional[str]] = {}
        self.log: List[LogEntry] = []
        self._faults: Dict[Tuple[str, int], List[_Fault]] = {}
        self._expire: Dict[Tuple[str, int], int] = {}
        self.transport = httpx.MockTransport(self._handle)

    # ---- scripting -------------------------------------------------------

    def fail_page(
        self,
        endpoint: str,
        n: int,
        status: int,
        retry_after: Optional[Any] = None,
        times: int = 1,
    ) -> None:
        """Answer ``status`` the next ``times`` requests for page ``n`` (1-based)
        of ``endpoint``, with a ``retry-after`` header when given."""
        self._faults.setdefault((endpoint, n), []).append(_Fault(status, retry_after, times))

    def expire_cursor_on_page(self, endpoint: str, n: int, times: int = 1) -> None:
        """Answer HTTP 410 the next ``times`` requests for page ``n`` (n >= 2)."""
        self._expire[(endpoint, n)] = self._expire.get((endpoint, n), 0) + times

    def mutate(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Run ``fn(dataset)`` to change fixture values in place."""
        fn(self.dataset)

    def set_latest_activity_date(self, day: Optional[date]) -> None:
        self.latest_activity_date = day

    def set_refreshed_at_for_window(self, first_day: date, value: Optional[str]) -> None:
        """Answer ``data_refreshed_at`` = ``value`` (None gives JSON null) to
        every report request whose ``starting_at`` falls on ``first_day``."""
        self._refreshed_for_window[first_day.isoformat()] = value

    def _refreshed(self, start: datetime) -> Optional[str]:
        return self._refreshed_for_window.get(start.date().isoformat(), self.refreshed_at)

    def requests_for(self, endpoint: str) -> List[LogEntry]:
        return [e for e in self.log if e.endpoint == endpoint]

    # ---- request handling ------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        params = {k: (v if len(v) > 1 else v[0]) for k, v in parse_qs(request.url.query.decode()).items()}
        now = self.clock()

        def reply(status: int, body: Dict[str, Any], page: int = 0, headers=None) -> httpx.Response:
            self.log.append(LogEntry(now, endpoint, params, page, status))
            return httpx.Response(status, json=body, headers=headers or {})

        def error(status: int, kind: str, message: str, page: int = 0, headers=None):
            return reply(status, {"error": {"type": kind, "message": message}}, page, headers)

        if self.api_key is not None and request.headers.get("x-api-key") != self.api_key:
            return error(401, "authentication_error", "invalid x-api-key")
        if "anthropic-version" not in request.headers:
            return error(400, "invalid_request_error", "anthropic-version header is required")
        if endpoint not in KNOWN_ENDPOINTS:
            return error(404, "not_found_error", "unknown endpoint")

        query_params = {k: v for k, v in params.items() if k != "page"}
        cursor = params.get("page")
        offset, page_no = 0, 1
        if cursor is not None:
            decoded = self._decode_cursor(str(cursor))
            if decoded is None:
                return error(400, "invalid_request_error", "malformed page cursor")
            offset, page_no, bound = decoded
            if bound != self._bind(endpoint, query_params):
                return error(
                    400, "invalid_request_error",
                    "page cursor does not match the other query parameters", page_no,
                )

        # scripted cursor expiry
        left = self._expire.get((endpoint, page_no), 0)
        if left > 0:
            self._expire[(endpoint, page_no)] = left - 1
            return error(410, "gone", "page cursor expired", page_no)
        # scripted faults
        for fault in self._faults.get((endpoint, page_no), []):
            if fault.times > 0:
                fault.times -= 1
                headers = {}
                if fault.retry_after is not None:
                    headers["retry-after"] = str(fault.retry_after)
                kind = "rate_limit_error" if fault.status == 429 else "api_error"
                return error(fault.status, kind, "scripted fault", page_no, headers)

        if endpoint in USAGE_ENDPOINTS or endpoint in COST_ENDPOINTS:
            return self._report(endpoint, params, query_params, offset, page_no, reply, error)
        if endpoint == "users":
            return self._users(endpoint, params, query_params, offset, page_no, reply, error)
        return self._summaries(params, reply, error)

    # ---- endpoints -------------------------------------------------------

    def _limit(self, params: Dict[str, Any]) -> int:
        requested = int(params["limit"]) if "limit" in params else 1000
        if self.page_limit is not None:
            return min(requested, self.page_limit) if "limit" in params else self.page_limit
        return requested

    def _page(self, endpoint, rows, params, query_params, offset, page_no):
        limit = self._limit(params)
        chunk = rows[offset: offset + limit]
        more = offset + limit < len(rows)
        next_page = None
        if more:
            next_page = self._encode_cursor(offset + limit, page_no + 1, self._bind(endpoint, query_params))
        return chunk, more, next_page

    def _report(self, endpoint, params, query_params, offset, page_no, reply, error):
        table, per_user = (USAGE_ENDPOINTS.get(endpoint) or COST_ENDPOINTS[endpoint])
        start = _parse_ts(params.get("starting_at"))
        end = _parse_ts(params.get("ending_at"))
        if start is None:
            return error(400, "invalid_request_error", "starting_at is required", page_no)
        if per_user and "bucket_width" in params and end is None:
            # the docs: ending_at is required whenever bucket_width is set (a probe without it would be a 400)
            return error(400, "invalid_request_error", "ending_at is required when bucket_width is set", page_no)
        selected = []
        for dkey in sorted(self.dataset[table]):
            bucket_start = datetime.fromisoformat(dkey).replace(tzinfo=timezone.utc)
            if bucket_start < start.replace(hour=0, minute=0, second=0, microsecond=0):
                continue
            if end is not None and bucket_start >= end:
                continue
            selected.append((dkey, bucket_start))
        items: List[Dict[str, Any]] = []
        for dkey, bucket_start in selected:
            stamps = {"starting_at": _iso(bucket_start), "ending_at": _iso(bucket_start + timedelta(days=1))}
            if per_user:
                for row in self.dataset[table][dkey]:
                    items.append(dict(row, **stamps))
            else:
                items.append(dict(stamps, results=list(self.dataset[table][dkey])))
        chunk, more, next_page = self._page(endpoint, items, params, query_params, offset, page_no)
        body: Dict[str, Any] = {
            "data": chunk,
            "has_more": more,
            "next_page": next_page,
            "organization_id": self.dataset["provider_account_id"],
        }
        if not self.omit_refreshed_at:
            body["data_refreshed_at"] = self._refreshed(start)
        return reply(200, body, page_no)

    def _users(self, endpoint, params, query_params, offset, page_no, reply, error):
        raw_date = params.get("date")
        if raw_date is None:
            return error(400, "invalid_request_error", "date is required", page_no)
        try:
            day = date.fromisoformat(str(raw_date))
        except ValueError:
            return error(400, "invalid_request_error", "date must be YYYY-MM-DD", page_no)
        latest = self.latest_activity_date
        if latest is not None and day > latest:
            return error(
                400, "invalid_request_error",
                "date is not yet available; latest available date is " + latest.isoformat(),
                page_no,
            )
        rows = self.dataset["activity"].get(day.isoformat(), [])
        chunk, _more, next_page = self._page(endpoint, rows, params, query_params, offset, page_no)
        return reply(200, {"data": chunk, "next_page": next_page}, page_no)

    def _summaries(self, params, reply, error):
        first = params.get("starting_date")
        last = params.get("ending_date")
        if first is None or last is None:
            return error(400, "invalid_request_error", "starting_date and ending_date are required", 1)
        latest = self.latest_activity_date
        if latest is not None and date.fromisoformat(str(last)) - timedelta(days=1) > latest:
            return error(
                400, "invalid_request_error",
                "date is not yet available; latest available date is " + latest.isoformat(), 1,
            )
        rows = [r for r in self.dataset["summaries"] if str(first) <= r["starting_at"][:10] < str(last)]
        return reply(200, {"data": rows, "next_page": None}, 1)

    # ---- cursors ---------------------------------------------------------

    @staticmethod
    def _bind(endpoint: str, query_params: Dict[str, Any]) -> str:
        canonical = json.dumps([endpoint, sorted(query_params.items())], sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _encode_cursor(offset: int, page_no: int, bound: str) -> str:
        raw = json.dumps({"o": offset, "n": page_no, "q": bound}).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(text: str) -> Optional[Tuple[int, int, str]]:
        try:
            padded = text + "=" * (-len(text) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return int(data["o"]), int(data["n"]), str(data["q"])
        except Exception:
            return None


FakeApi = FakeAPI  # the name the tests of this package used before the fake was public


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")
