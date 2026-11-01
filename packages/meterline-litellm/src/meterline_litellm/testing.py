"""A fake LiteLLM proxy for testing code that calls ``meterline_litellm.run``, with no key and no network.

``FakeAPI`` answers ``GET user/daily/activity``, ``GET customer/daily/activity`` and ``GET user/list`` on an
``httpx.MockTransport`` in the documented response shapes of ``docs/api-shapes/litellm/``. Everything it serves is
synthetic: ``FakeAPI()`` holds the dataset of ``sample_dataset()`` (four proxy users at ``example.com`` addresses, two
models, 2026-09-01 to 2026-09-14), built in code and the same on every call. Pass your own rows to serve other data.

Use it with ``run`` by handing its transport over::

    from datetime import date
    from meterline_litellm import run
    from meterline_litellm.testing import FakeAPI

    fake = FakeAPI()
    result = run("backfill", since=date(2026, 9, 1), proxy_name="my-proxy", data_dir="out",
                 credentials={"LITELLM_API_KEY": fake.api_key, "LITELLM_BASE_URL": "https://proxy.example.com"},
                 transport=fake.transport)

What it does not prove: that a live proxy accepts your key, that a live proxy answers in these shapes, or how a live
proxy rate-limits. Its aggregation follows the source it was transcribed from (``common_daily_activity.py``, v1.104.2):

* the table holds raw rows (date, user, model, key, counters); a page is a slice of ``page_size`` raw rows ordered by
  date and insertion, so a day can be cut across two pages with partial numbers;
* ``metrics`` of a day sum every raw row, ``breakdown.models`` skips a row with a null model (the row still counts in
  ``metrics``);
* ``breakdown.entities`` is keyed by user id (A) or end-user id (B); a row with no owner gets the key ``Unassigned``
  (``unassigned_key`` changes it);
* ``page_cap`` limits the page size the proxy honours;
* scripted faults (``fail``), a replayed page (``replay``) and a request log with the injected clock's time.
"""

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

import httpx

PATH_A = "/user/daily/activity"
PATH_B = "/customer/daily/activity"
PATH_C = "/user/list"

DEFAULT_API_KEY = "sk-synthetic-not-a-real-key"  # the key a ``FakeAPI`` expects unless ``api_key`` says otherwise

COUNT_FIELDS = (
    "prompt_tokens", "completion_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    "successful_requests", "failed_requests", "api_requests",
)
MONEY_FIELDS = ("spend", "flat_cost")
OTHER_ZERO_COUNTS = (
    "compression_saved_tokens", "total_response_time_ms", "timed_requests",
)
OTHER_ZERO_MONEY = (
    "compression_savings_spend", "prompt_caching_savings_spend",
    "gateway_injected_caching_savings_spend", "autorouter_savings_spend",
)


@dataclass
class LogEntry:
    time: Any  # the injected clock's reading
    path: str
    params: Dict[str, str]
    status: int
    authorization: Optional[str]


@dataclass
class _Fault:
    path: str
    status: int
    times: int
    retry_after: Optional[int] = None
    headers: Optional[Dict[str, str]] = None


def row(date: str, user: Optional[str], model: Optional[str], *, key: str = "key-hash-1", **numbers: Any) -> Dict[str, Any]:
    """One raw table row; unnamed counters are 0."""
    out: Dict[str, Any] = {"date": date, "user_id": user, "model": model, "api_key": key}
    for name in COUNT_FIELDS:
        out[name] = 0
    for name in MONEY_FIELDS:
        out[name] = Decimal(0)
    out.update(numbers)
    return out


def end_row(date: str, end_user: str, model: Optional[str], **numbers: Any) -> Dict[str, Any]:
    out = row(date, None, model, **numbers)
    out["end_user_id"] = end_user
    return out


def _num(value: Any) -> Any:
    return value if isinstance(value, int) else Decimal(str(value))


def _metrics(rows: List[Dict[str, Any]], *, omit: tuple = ()) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in MONEY_FIELDS:
        out[name] = sum((_num(r.get(name, 0)) for r in rows), Decimal(0))
    for name in COUNT_FIELDS:
        out[name] = sum(int(r.get(name, 0)) for r in rows)
    out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    for name in OTHER_ZERO_COUNTS:
        out[name] = 0
    for name in OTHER_ZERO_MONEY:
        out[name] = Decimal(0)
    for name in omit:
        out.pop(name, None)
    return out


def _wrap_models(rows: List[Dict[str, Any]], omit: tuple) -> Dict[str, Any]:
    models: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.get("model"):
            models.setdefault(r["model"], []).append(r)
    out = {}
    for model, items in sorted(models.items()):
        keys: Dict[str, List[Dict[str, Any]]] = {}
        for r in items:
            keys.setdefault(r["api_key"], []).append(r)
        out[model] = {
            "metrics": _metrics(items, omit=omit),
            "metadata": {},
            "api_key_breakdown": {
                k: {"metrics": _metrics(v, omit=omit), "metadata": _key_meta(k)} for k, v in sorted(keys.items())
            },
        }
    return out


def _key_meta(key: str) -> Dict[str, Any]:
    return {"key_alias": "alias-" + key, "team_id": None, "user_id": None, "user_email": None, "key_exists": True}


def sample_dataset() -> Dict[str, Any]:
    """The small synthetic dataset ``FakeAPI()`` serves: ``{"rows", "users", "end_rows", "end_aliases"}``.

    Four proxy users (``user-1`` to ``user-4``, addresses ``person-N@example.com``), two models and every day from
    2026-09-01 to 2026-09-14. The counters follow from the day and the user by plain arithmetic, so the data is the
    same on every call and no clock or random source is read. Each cache figure stays below its prompt tokens.
    """
    models = ("model-a", "model-b")
    rows: List[Dict[str, Any]] = []
    for day in range(1, 15):
        for u in range(1, 5):
            for m, model in enumerate(models):
                if (day + u + m) % 4 == 0:  # a few user-model-days are idle
                    continue
                prompt = 400 + 37 * day + 90 * u + 150 * m
                done = 20 + 3 * day + 5 * u
                ok = 1 + (day + u) % 4
                rows.append(row(
                    "2026-09-%02d" % day, "user-%d" % u, model, key="key-hash-%d" % u,
                    prompt_tokens=prompt, completion_tokens=done, cache_read_input_tokens=prompt // 3,
                    cache_creation_input_tokens=prompt // 10, api_requests=ok + (day % 2),
                    successful_requests=ok, failed_requests=day % 2,
                    spend=Decimal(prompt * 3 + done * 15) / Decimal(100000),
                ))
    users = [{"user_id": "user-%d" % u, "user_email": "person-%d@example.com" % u} for u in range(1, 5)]
    return {"rows": rows, "users": users, "end_rows": [], "end_aliases": {}}


class FakeAPI:
    """The fake proxy. With no ``rows`` it serves ``sample_dataset()``; ``transport`` is the ``httpx.MockTransport``
    to pass to ``run``. ``api_key`` is the key it accepts (``DEFAULT_API_KEY``), ``clock`` stamps the request log."""

    def __init__(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        *,
        users: Optional[List[Dict[str, Any]]] = None,
        end_rows: Optional[List[Dict[str, Any]]] = None,
        end_aliases: Optional[Dict[str, str]] = None,
        api_key: str = DEFAULT_API_KEY,
        clock: Callable[[], Any] = time.monotonic,
        page_cap: Optional[int] = None,
        role: str = "proxy_admin_viewer",
        unassigned_key: str = "Unassigned",
        omit_metrics: tuple = (),
        spend_noise: Decimal = Decimal(0),
    ) -> None:
        if rows is None:
            sample = sample_dataset()
            rows = sample["rows"]
            users = users if users is not None else sample["users"]
        self.rows = rows
        self.users = users if users is not None else []
        self.end_rows = end_rows if end_rows is not None else []
        self.end_aliases = end_aliases or {}
        self.api_key = api_key
        self.page_cap = page_cap
        self.role = role
        self.unassigned_key = unassigned_key
        self.omit_metrics = omit_metrics
        self.spend_noise = spend_noise
        self.log: List[LogEntry] = []
        self.missing_paths: set = set()
        self.replays: Dict[str, int] = {}  # path -> later pages still to be answered with the first page's rows
        self._key = api_key
        self._clock = clock
        self._faults: List[_Fault] = []
        self.transport = httpx.MockTransport(self._handle)

    # ---- scripting -------------------------------------------------------

    def fail(self, path: str, status: int, *, times: int = 1, retry_after: Optional[int] = None,
             headers: Optional[Dict[str, str]] = None) -> None:
        self._faults.append(_Fault(path, status, times, retry_after, headers))

    def replay(self, path: str, times: int = 1) -> None:
        """Answer the next ``times`` requests for a page after the first of ``path`` with the rows of the first page,
        keeping the page number and paging fields of the page asked for: the same days arrive twice in one run. The
        data needs two pages for this (``page_cap`` makes a page hold fewer rows)."""
        self.replays[path] = self.replays.get(path, 0) + times

    def requests_for(self, path: str) -> List[LogEntry]:
        return [e for e in self.log if e.path == path]

    # ---- serving -----------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for known in (PATH_A, PATH_B, PATH_C):
            if path.endswith(known):
                path = known
        params = {k: v for k, v in request.url.params.items()}
        auth = request.headers.get("authorization")

        def reply(status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
            self.log.append(LogEntry(self._clock(), path, params, status, auth))
            return httpx.Response(
                status, content=_dumps(body).encode("utf-8"),
                headers={"content-type": "application/json", **(headers or {})},
            )

        for fault in self._faults:
            if fault.path == path and fault.times > 0:
                fault.times -= 1
                headers = dict(fault.headers or {})
                if fault.retry_after is not None:
                    headers["retry-after"] = str(fault.retry_after)
                return reply(fault.status, {"error": "scripted fault"}, headers)
        if auth != "Bearer " + self._key:
            return reply(401, {"error": {"message": "Authentication Error, Invalid proxy server token passed"}})
        if path in self.missing_paths or path not in (PATH_A, PATH_B, PATH_C):
            return reply(404, {"detail": "Not Found"})
        if path == PATH_C:
            return self._user_list(params, reply)
        if path == PATH_B and self.role not in ("proxy_admin", "proxy_admin_viewer"):
            return reply(401, {"error": "Admin-only endpoint. Your user role=" + self.role})
        return self._daily(path, params, reply)

    def _daily(self, path: str, params: Dict[str, str], reply: Callable[..., httpx.Response]) -> httpx.Response:
        if "start_date" not in params or "end_date" not in params:
            return reply(400, {"error": "Please provide start_date and end_date"})
        page = int(params.get("page", "1"))
        size = int(params.get("page_size", "50"))
        if page < 1 or size < 1 or size > 1000:
            return reply(422, {"detail": "page or page_size out of range"})
        if self.page_cap is not None:
            size = min(size, self.page_cap)
        first, last = params["start_date"], params["end_date"]
        if path == PATH_A:
            table = [r for r in self.rows if first <= r["date"] <= last]
            if "user_id" in params:
                table = [r for r in table if (r.get("user_id") or "") == params["user_id"]]
            owner = "user_id"
        else:
            table = [r for r in self.end_rows if first <= r["date"] <= last]
            if params.get("end_user_ids"):
                wanted = params["end_user_ids"].split(",")
                table = [r for r in table if r.get("end_user_id") in wanted]
            owner = "end_user_id"
        table = sorted(enumerate(table), key=lambda item: (item[1]["date"], item[0]))
        ordered = [r for _, r in table]
        total_pages = max(1, -(-len(ordered) // size))
        shown = page
        if page > 1 and self.replays.get(path, 0) > 0:
            self.replays[path] -= 1
            shown = 1
        chunk = ordered[(shown - 1) * size: shown * size]
        days: Dict[str, List[Dict[str, Any]]] = {}
        for r in chunk:
            days.setdefault(r["date"], []).append(r)
        results = []
        for day, items in sorted(days.items()):
            day_metrics = _metrics(items, omit=self.omit_metrics)
            if "spend" in day_metrics:
                day_metrics["spend"] += self.spend_noise  # binary-float rounding in the real proxy
            results.append({
                "date": day,
                "metrics": day_metrics,
                "breakdown": self._breakdown(items, owner),
            })
        body = {
            "results": results,
            "metadata": {
                "total_spend": sum((_num(r["spend"]) for r in chunk), Decimal(0)),
                "total_flat_cost": Decimal(0),
                "total_prompt_tokens": sum(int(r["prompt_tokens"]) for r in chunk),
                "total_completion_tokens": sum(int(r["completion_tokens"]) for r in chunk),
                "total_tokens": sum(int(r["prompt_tokens"]) + int(r["completion_tokens"]) for r in chunk),
                "total_api_requests": sum(int(r["api_requests"]) for r in chunk),
                "total_successful_requests": sum(int(r["successful_requests"]) for r in chunk),
                "total_failed_requests": sum(int(r["failed_requests"]) for r in chunk),
                "total_cache_read_input_tokens": sum(int(r["cache_read_input_tokens"]) for r in chunk),
                "total_cache_creation_input_tokens": sum(int(r["cache_creation_input_tokens"]) for r in chunk),
                "total_compression_saved_tokens": 0,
                "total_compression_savings_spend": Decimal(0),
                "total_prompt_caching_savings_spend": Decimal(0),
                "total_gateway_injected_caching_savings_spend": Decimal(0),
                "total_autorouter_savings_spend": Decimal(0),
                "total_response_time_ms": 0,
                "total_timed_requests": 0,
                "page": page,
                "total_pages": total_pages,
                "has_more": page < total_pages,
            },
        }
        return reply(200, body)

    def _breakdown(self, items: List[Dict[str, Any]], owner: str) -> Dict[str, Any]:
        omit = self.omit_metrics
        providers = {"provider-a": {"metrics": _metrics(items, omit=omit), "metadata": {}, "api_key_breakdown": {}}}
        endpoints = {"/v1/chat/completions": {"metrics": _metrics(items, omit=omit), "metadata": {}, "api_key_breakdown": {}}}
        keys: Dict[str, List[Dict[str, Any]]] = {}
        entities: Dict[str, List[Dict[str, Any]]] = {}
        for r in items:
            keys.setdefault(r["api_key"], []).append(r)
            entity = r.get(owner) or self.unassigned_key
            entities.setdefault(entity, []).append(r)
        entity_meta = {}
        for name in entities:
            if owner == "user_id":
                user = next((u for u in self.users if u["user_id"] == name), None)
                entity_meta[name] = {"user_email": user["user_email"], "user_alias": None} if user else {}
            else:
                entity_meta[name] = {"alias": self.end_aliases.get(name)}
        return {
            "mcp_servers": {},
            "models": _wrap_models(items, omit),
            "model_groups": _wrap_models(items, omit),
            "providers": providers,
            "endpoints": endpoints,
            "api_keys": {k: {"metrics": _metrics(v, omit=omit), "metadata": _key_meta(k)} for k, v in sorted(keys.items())},
            "entities": {
                name: {"metrics": _metrics(v, omit=omit), "metadata": entity_meta[name], "api_key_breakdown": {}}
                for name, v in sorted(entities.items())
            },
        }

    def _user_list(self, params: Dict[str, str], reply: Callable[..., httpx.Response]) -> httpx.Response:
        page = int(params.get("page", "1"))
        size = int(params.get("page_size", "25"))
        if size < 1 or size > 100 or page < 1:
            return reply(422, {"detail": "page_size must be between 1 and 100"})
        chunk = self.users[(page - 1) * size: page * size]
        users = []
        for u in chunk:
            users.append({
                "user_id": u["user_id"], "user_alias": None, "team_id": None, "sso_user_id": None,
                "organization_id": None, "object_permission_id": None, "password_reset_required": False,
                "last_breach_check_at": None, "teams": [], "user_role": "internal_user", "max_budget": None,
                "spend": Decimal(0), "user_email": u["user_email"], "models": [], "metadata": {},
                "max_parallel_requests": None, "tpm_limit": None, "rpm_limit": None, "budget_duration": None,
                "budget_reset_at": None, "allowed_cache_controls": [], "policies": [], "model_spend": {},
                "model_max_budget": {}, "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z",
                "organization_memberships": None, "object_permission": None, "key_count": 1,
            })
        total = len(self.users)
        return reply(200, {
            "users": users, "total": total, "page": page, "page_size": size,
            "total_pages": max(1, -(-total // size)),
        })


def _dumps(value: Any) -> str:
    """JSON text of a body; a Decimal is written as the number it holds, so no figure passes through a binary float."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return "{" + ", ".join("%s: %s" % (json.dumps(k), _dumps(v)) for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_dumps(v) for v in value) + "]"
    return json.dumps(value)


FakeProxy = FakeAPI  # the name the tests used before the fake became public
