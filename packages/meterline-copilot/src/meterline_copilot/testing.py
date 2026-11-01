"""A fake GitHub API for trying ``meterline_copilot.run`` without a key. Everything it serves is synthetic.

``FakeAPI`` answers the Copilot usage-report, billing and seat endpoints on an ``httpx.MockTransport``
(``FakeAPI(...).transport``); no network is touched. With no argument it serves ``sample_dataset()``: four invented
logins, the organization ``my-org`` and the days 2026-09-01 to 2026-09-14, in the shapes the connector reads::

    from datetime import date, datetime, timezone
    from meterline_copilot import run
    from meterline_copilot.testing import SAMPLE_TOKEN, FakeAPI

    fake = FakeAPI()  # expects the token SAMPLE_TOKEN
    result = run(
        "backfill", since=date(2026, 9, 1), level="organization", slug="my-org", data_dir="data",
        credentials={"COPILOT_TOKEN": SAMPLE_TOKEN},
        transport=fake.transport, now=lambda: datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc),
    )

A long backfill makes more requests than one hour's budget (``rate_per_hour``, 600 by default), so the throttle waits.
Run it on a clock that does not wait: ``clock = FakeClock()`` and ``run(..., clock=clock, sleep=clock.sleep, wall=clock)``.
A ``sleep`` that does nothing with the real clock would make the throttle spin until the real hour had passed.

It serves the API host (``api.github.com``) and the download host (``downloads.example.com``) from one
transport and records every request with its headers, so a check can assert that the download host never
sees an ``Authorization`` header.

Paths (organization level, or the same under ``/enterprises/{slug}``):

* ``copilot/metrics/reports/users-1-day?day=`` (A) and
  ``.../organization-1-day`` / ``.../enterprise-1-day`` (B): an envelope with
  signed download links; 204 for a day listed in ``no_report_days``;
* ``settings/billing/ai_credit/usage?year=&month=[&day=][&user=]`` (C);
* ``copilot/billing/seats?page=&per_page=`` (D) with a ``Link`` header;
* ``copilot/billing`` (E, organization level only).

Faults are scripted per path suffix: a status, how many times, and headers
(``retry-after``, ``x-ratelimit-reset``).

It does not prove that the live API accepts your token, that its live responses have these shapes, or how its
rate limits behave.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from meterline.http import FakeClock  # noqa: F401  re-exported: a clock whose sleep moves it, for a long backfill

API_HOST = "api.github.com"
DOWNLOAD_HOST = "downloads.example.com"
SAMPLE_TOKEN = "synthetic-token-not-real"  # nosec B105 # noqa: S105 - a synthetic value; the token FakeAPI() expects, for billing too
SAMPLE_SLUG = "my-org"
SAMPLE_LOGINS = ("octo-ada", "octo-ben", "octo-cai", "octo-dee")
SAMPLE_FIRST_DAY = date(2026, 9, 1)
SAMPLE_LAST_DAY = date(2026, 9, 14)

TOKEN_USAGE = {"avg_tokens_per_request": Decimal("4400.0"), "output_tokens_sum": 5000, "prompt_tokens_sum": 3800}


@dataclass
class LogEntry:
    time: Any
    host: str
    path: str
    params: Dict[str, str]
    status: int
    authorization: Optional[str]
    headers: Dict[str, str]


@dataclass
class _Fault:
    suffix: str
    status: int
    times: int
    headers: Dict[str, str] = field(default_factory=dict)


def feature(name: str = "code_completion", *, gen: int = 1, acc: int = 1, added: int = 8, deleted: int = 0,
            sug_add: int = 10, sug_del: int = 0, msgs: int = 0) -> Dict[str, Any]:
    return {
        "code_acceptance_activity_count": acc, "code_generation_activity_count": gen, "feature": name,
        "loc_added_sum": added, "loc_deleted_sum": deleted, "loc_suggested_to_add_sum": sug_add,
        "loc_suggested_to_delete_sum": sug_del, "user_initiated_interaction_count": msgs,
    }


def user_row(day: str, user_id: int, login: str, *, credits: Any = Decimal("1.5"), features: Optional[List[Dict[str, Any]]] = None,
             cli: bool = False, app: bool = False, level: str = "organization", distinct: bool = True,
             **top: Any) -> Dict[str, Any]:
    """One per-user row of report A; top-level counters are the feature sums unless given."""
    feats = features if features is not None else [feature()]
    row: Dict[str, Any] = {
        "ai_adoption_phase": {"phase": "Phase 2", "phase_number": 2, "version": "v1"},
        "code_acceptance_activity_count": sum(f["code_acceptance_activity_count"] for f in feats),
        "code_generation_activity_count": sum(f["code_generation_activity_count"] for f in feats),
        "day": day,
        "loc_added_sum": sum(f["loc_added_sum"] for f in feats),
        "loc_deleted_sum": sum(f["loc_deleted_sum"] for f in feats),
        "loc_suggested_to_add_sum": sum(f["loc_suggested_to_add_sum"] for f in feats),
        "loc_suggested_to_delete_sum": sum(f["loc_suggested_to_delete_sum"] for f in feats),
        "user_initiated_interaction_count": sum(f["user_initiated_interaction_count"] for f in feats),
        "enterprise_id": "1",
        "totals_by_feature": feats,
        "totals_by_ide": [{
            "code_acceptance_activity_count": 1, "code_generation_activity_count": 1, "ide": "vscode",
            "last_known_ide_version": {"ide_version": "1.85.0", "sampled_at": "2025-10-01T00:00:02.000Z"},
            "last_known_plugin_version": {"plugin": "", "plugin_version": "", "sampled_at": "2025-10-01T00:00:02.000Z"},
            "loc_added_sum": 8, "loc_deleted_sum": 0, "loc_suggested_to_add_sum": 10, "loc_suggested_to_delete_sum": 0,
            "user_initiated_interaction_count": 0,
        }],
        "totals_by_language_feature": [{
            "code_acceptance_activity_count": 1, "code_generation_activity_count": 1, "feature": "code_completion",
            "language": "unknown", "loc_added_sum": 8, "loc_deleted_sum": 0, "loc_suggested_to_add_sum": 10,
            "loc_suggested_to_delete_sum": 0,
        }],
        "totals_by_language_model": [],
        "totals_by_model_feature": [],
        "used_agent": False, "used_chat": False, "used_cli": cli, "used_copilot_app": app,
        "used_copilot_cloud_agent": False, "used_copilot_code_review_active": None,
        "used_copilot_code_review_passive": None, "used_copilot_coding_agent": False,
        "user_id": user_id, "user_login": login,
    }
    if level == "organization":
        row["organization_id"] = "2"
    if credits is not None:
        row["ai_credits_used"] = credits
    if distinct:
        row.update({
            "distinct_custom_agent_use_count": 2, "distinct_mcp_use_count": 2, "distinct_plugin_use_count": 1,
            "distinct_skill_use_count": 3, "distinct_slash_cmd_use_count": 2,
            "totals_by_custom_agent": [{"custom_agent": "general-purpose", "interaction_count": 4}],
            "totals_by_mcp": [{"interaction_count": 8, "mcp": "github-mcp-server"}],
            "totals_by_plugin": [{"interaction_count": 2, "plugin": "other"}],
            "totals_by_skill": [{"interaction_count": 5, "skill": "other"}],
            "totals_by_slash_cmd": [{"interaction_count": 3, "slash_cmd": "/plan"}],
            "totals_by_vscode_agent": {"session_count": 1, "total_user_messages": 1},
            "used_vscode_agent": True,
        })
    if cli:
        row["totals_by_cli"] = {
            "last_known_cli_version": {"cli_version": "1.0.8", "sampled_at": "2025-10-01T00:01:43.000Z"},
            "prompt_count": 2, "request_count": 2, "session_count": 2, "token_usage": dict(TOKEN_USAGE),
        }
    if app:
        row["totals_by_copilot_app"] = {
            "prompt_count": 1, "request_count": 3, "session_count": 1,
            "token_usage": {"avg_tokens_per_request": Decimal("3200.0"), "output_tokens_sum": 4200, "prompt_tokens_sum": 5400},
        }
    row.update(top)
    return row


def aggregate_row(day: str, daily: int, weekly: int, monthly: int) -> Dict[str, Any]:
    """The aggregate of one day as the ``*-1-day`` reports send it: one flat record (the 28-day report is the one that
    wraps its days in ``day_totals``)."""
    entry = {
        "code_acceptance_activity_count": 3, "code_generation_activity_count": 3,
        "daily_active_cli_users": 1, "daily_active_copilot_app_users": 1,
        "daily_active_copilot_cloud_agent_users": 0, "daily_active_copilot_code_review_users": 0,
        "daily_active_users": daily, "daily_active_vscode_agent_users": 1,
        "daily_passive_copilot_code_review_users": 0, "day": day, "enterprise_id": "1",
        "loc_added_sum": 32, "loc_deleted_sum": 6, "loc_suggested_to_add_sum": 34, "loc_suggested_to_delete_sum": 6,
        "monthly_active_agent_users": 1, "monthly_active_chat_users": 1,
        "monthly_active_copilot_cloud_agent_users": 0, "monthly_active_copilot_code_review_users": 0,
        "monthly_active_users": monthly, "monthly_active_vscode_agent_users": 1,
        "monthly_passive_copilot_code_review_users": 0,
        "pull_requests": {
            "median_minutes_to_merge": 1, "median_minutes_to_merge_copilot_authored": 1,
            "median_minutes_to_merge_copilot_reviewed": 1, "total_applied_suggestions": 1,
            "total_copilot_applied_suggestions": 1, "total_copilot_suggestions": 1, "total_created": 1,
            "total_created_by_copilot": 1, "total_merged": 1, "total_merged_created_by_copilot": 1,
            "total_merged_reviewed_by_copilot": 1, "total_reviewed": 1, "total_reviewed_by_copilot": 1,
            "total_suggestions": 1,
        },
        "totals_by_feature": [feature()],
        "distinct_custom_agent_use_count": 2, "distinct_mcp_use_count": 2, "distinct_plugin_use_count": 1,
        "distinct_skill_use_count": 3, "distinct_slash_cmd_use_count": 2,
        "totals_by_3rd_party_agent": [{"agent_id": "2246796", "agent_name": "example-agent", "session_count": 1,
                                       "user_initiated_interaction_count": 2}],
        "totals_by_ai_adoption_phase": [{
            "avg_code_acceptance_activities": 1, "avg_code_generation_activities": 1, "avg_loc_added": 1,
            "avg_loc_deleted": 1, "avg_pull_requests_created": 1, "avg_pull_requests_median_minutes_to_merge": 1,
            "avg_pull_requests_merged": 1, "avg_pull_requests_minutes_to_review": 1,
            "avg_pull_requests_review_cycles": 1, "avg_pull_requests_reviewed": 1,
            "avg_user_initiated_interactions": 1, "phase": "Phase 2", "phase_number": 2,
            "total_engaged_users": 1, "users_in_phase_28d": 1, "total_pull_requests_merged": 1}],
        "totals_by_cli": {"prompt_count": 2, "request_count": 2, "session_count": 2, "token_usage": dict(TOKEN_USAGE)},
        "totals_by_copilot_app": {"prompt_count": 1, "request_count": 3, "session_count": 1,
                                  "token_usage": dict(TOKEN_USAGE)},
        "totals_by_custom_agent": [{"custom_agent": "general-purpose", "interaction_count": 4}],
        "totals_by_ide": [{"code_acceptance_activity_count": 1, "code_generation_activity_count": 1, "ide": "vscode",
                           "loc_added_sum": 8, "loc_deleted_sum": 0, "loc_suggested_to_add_sum": 10,
                           "loc_suggested_to_delete_sum": 0, "user_initiated_interaction_count": 0}],
        "totals_by_language_feature": [{"code_acceptance_activity_count": 1, "code_generation_activity_count": 1,
                                        "feature": "code_completion", "language": "unknown", "loc_added_sum": 8,
                                        "loc_deleted_sum": 0, "loc_suggested_to_add_sum": 10,
                                        "loc_suggested_to_delete_sum": 0}],
        "totals_by_language_model": [], "totals_by_model_feature": [],
        "totals_by_mcp": [{"interaction_count": 8, "mcp": "github-mcp-server"}],
        "totals_by_vscode_agent": {"session_count": 1, "total_user_messages": 1},
        "totals_by_plugin": [{"interaction_count": 2, "plugin": "other"}],
        "totals_by_skill": [{"interaction_count": 5, "skill": "other"}],
        "totals_by_slash_cmd": [{"interaction_count": 3, "slash_cmd": "/plan"}],
        "user_initiated_interaction_count": 1,
        "weekly_active_copilot_cloud_agent_users": 0, "weekly_active_copilot_code_review_users": 0,
        "weekly_active_users": weekly, "weekly_active_vscode_agent_users": 1,
        "weekly_passive_copilot_code_review_users": 0,
    }
    entry["totals_by_client"] = []  # always present, empty when there is no usage
    return entry


def item(product: str = "Copilot", model: str = "GPT-5", unit: str = "credits", gross_q: Any = 100,
         gross_a: Any = Decimal("1"), disc_q: Any = 0, disc_a: Any = Decimal("0"), price: Any = Decimal("0.01"),
         sku: str = "Copilot AI Credits") -> Dict[str, Any]:
    return {
        "product": product, "sku": sku, "model": model, "unitType": unit, "pricePerUnit": price,
        "grossQuantity": gross_q, "grossAmount": gross_a, "discountQuantity": disc_q, "discountAmount": disc_a,
        "netQuantity": Decimal(str(gross_q)) - Decimal(str(disc_q)),
        "netAmount": Decimal(str(gross_a)) - Decimal(str(disc_a)),
    }


def seat(login: str, user_id: int, *, created: str = "2026-09-01T10:00:00+02:00", plan: Optional[str] = "business",
         team: Optional[str] = "team-one", last_activity: Optional[str] = "2026-09-18T10:00:00+02:00",
         last_auth: Optional[str] = "2026-09-18T09:00:00+02:00", cancel: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "created_at": created, "updated_at": created, "pending_cancellation_date": cancel,
        "last_activity_at": last_activity, "last_activity_editor": "vscode/1.77.3/copilot/1.86.82",
        "last_authenticated_at": last_auth,
        "assignee": {"login": login, "id": user_id, "node_id": "MDQ6VXNlcjE=", "type": "User", "site_admin": False},
    }
    if plan is not None:
        out["plan_type"] = plan
    if team is not None:
        out["assigning_team"] = {"id": 1, "name": team.replace("-", " "), "slug": team}
    return out


def seat_without_assignee(**kw: Any) -> Dict[str, Any]:
    """A seat whose ``assignee`` is null (the schema allows it): the seat exists, nobody holds it."""
    out = seat("nobody", 0, **kw)
    out["assignee"] = None
    return out


_NO_DATA: Any = object()  # the default of ``dataset``: serve sample_dataset()


def sample_dataset(level: str = "organization", slug: str = SAMPLE_SLUG) -> Dict[str, Any]:
    """A small deterministic synthetic data set: four invented logins, the days 2026-09-01 to 2026-09-14.

    The keys are the data attributes of ``FakeAPI``: ``users`` (day -> rows of report A), ``aggregates``
    (day -> row of report B), ``billing_day`` (day -> usage items), ``billing_user`` ((login, year, month) ->
    usage items) and ``seats``. Every figure is computed from the day and the person, so two calls return the
    same data; nothing depends on the clock or on a random generator.
    """
    users: Dict[str, List[Dict[str, Any]]] = {}
    aggregates: Dict[str, Dict[str, Any]] = {}
    billing_day: Dict[str, List[Dict[str, Any]]] = {}
    billing_user: Dict[Tuple[str, int, int], List[Dict[str, Any]]] = {}
    spent = {login: Decimal(0) for login in SAMPLE_LOGINS}
    day = SAMPLE_FIRST_DAY
    while day <= SAMPLE_LAST_DAY:
        text = day.isoformat()
        number = (day - SAMPLE_FIRST_DAY).days
        rows = []
        for index, login in enumerate(SAMPLE_LOGINS):
            if (number + index) % 4 == 3:  # each person is idle on some days
                continue
            credits = Decimal(5 + (number * 3 + index * 7) % 11)
            spent[login] += credits
            rows.append(user_row(
                text, 1001 + index, login, level=level, credits=credits, cli=index == 1, app=index == 2,
                features=[feature(gen=2 + index, acc=1 + index % 2, added=8 + number, sug_add=12 + number)],
            ))
        users[text] = rows
        aggregates[text] = aggregate_row(text, len(rows), min(len(SAMPLE_LOGINS), len(rows) + 1), len(SAMPLE_LOGINS))
        total = sum((r["ai_credits_used"] for r in rows), Decimal(0))
        billing_day[text] = [item(gross_q=total, gross_a=total / 100)]
        day += timedelta(days=1)
    for login, total in spent.items():
        billing_user[(login, 2026, 9)] = [item(gross_q=total, gross_a=total / 100)]
    seats = [
        seat(login, 1001 + index, created="2026-08-%02dT10:00:00+02:00" % (10 + index), team="team-%s" % ("one", "two")[index % 2],
             last_activity="2026-09-1%dT10:00:00+02:00" % index, last_auth="2026-09-1%dT09:00:00+02:00" % index)
        for index, login in enumerate(SAMPLE_LOGINS)
    ]
    return {"users": users, "aggregates": aggregates, "billing_day": billing_day, "billing_user": billing_user,
            "seats": seats}


class FakeAPI:
    """The fake GitHub API; use ``.transport`` as the ``transport`` of ``run``.

    ``level`` and ``slug`` say which organization or enterprise it serves; ``token`` and ``billing_token`` are the
    credentials it accepts (a request with another gets a 401). ``dataset`` is a ``sample_dataset()``-shaped
    dict; the default serves ``sample_dataset(level, slug)`` and ``None`` serves nothing, for a caller that
    fills ``users``, ``aggregates``, ``billing_day``, ``billing_user`` and ``seats`` itself. ``clock`` stamps
    the request log.
    """

    def __init__(self, *, level: str = "organization", slug: str = SAMPLE_SLUG, token: str = SAMPLE_TOKEN,
                 billing_token: Optional[str] = None, clock: Callable[[], Any] = time.monotonic,
                 links_per_report: int = 2, seats_per_page: int = 100, dataset: Any = _NO_DATA) -> None:
        self.level = level
        self.slug = slug
        self.token = token
        self.billing_token = billing_token or token
        self.clock = clock
        self.links_per_report = links_per_report
        self.seats_per_page = seats_per_page
        data = sample_dataset(level, slug) if dataset is _NO_DATA else (dataset or {})
        # data
        self.users: Dict[str, List[Dict[str, Any]]] = data.get("users", {})           # day -> rows of A
        self.aggregates: Dict[str, Dict[str, Any]] = data.get("aggregates", {})       # day -> one row of B
        self.no_report_days: set = set()
        self.billing_day: Dict[str, List[Dict[str, Any]]] = data.get("billing_day", {})  # day -> items
        self.billing_user: Dict[Tuple[str, int, int], List[Dict[str, Any]]] = data.get("billing_user", {})  # (login, year, month) -> items
        self.seats: List[Dict[str, Any]] = data.get("seats", [])
        self.total_seats: Optional[int] = None
        self.summary = {"total": 12, "added_this_cycle": 9, "pending_invitation": 1, "pending_cancellation": 2,
                        "active_this_cycle": 12, "inactive_this_cycle": 11}
        # behaviour
        self.log: List[LogEntry] = []
        self.download_status_once: Dict[str, int] = {}              # url path -> status for the first request
        self.broken_envelope = False
        self._faults: List[_Fault] = []
        self.transport = httpx.MockTransport(self._handle)

    # ---- scripting ---------------------------------------------------------

    def fail(self, suffix: str, status: int, *, times: int = 1, headers: Optional[Dict[str, str]] = None) -> None:
        self._faults.append(_Fault(suffix, status, times, dict(headers or {})))

    def api_requests(self, suffix: str = "") -> List[LogEntry]:
        return [e for e in self.log if e.host == API_HOST and e.path.endswith(suffix)]

    def downloads(self) -> List[LogEntry]:
        return [e for e in self.log if e.host == DOWNLOAD_HOST]

    def billing_requests(self) -> List[LogEntry]:
        return self.api_requests("/settings/billing/ai_credit/usage")

    # ---- serving -----------------------------------------------------------

    @property
    def _root(self) -> str:
        return ("/orgs/" if self.level == "organization" else "/enterprises/") + self.slug

    @property
    def _billing_root(self) -> str:
        return ("/organizations/" if self.level == "organization" else "/enterprises/") + self.slug

    def _handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        params = dict(request.url.params.items())
        auth = request.headers.get("authorization")
        headers = {k.lower(): v for k, v in request.headers.items()}

        def reply(status: int, body: Any = None, *, text: Optional[str] = None, extra: Optional[Dict[str, str]] = None) -> httpx.Response:
            self.log.append(LogEntry(self.clock(), host, path, params, status, auth, headers))
            if status == 204:
                return httpx.Response(204, headers=extra or {})
            content = text.encode("utf-8") if text is not None else _dumps(body).encode("utf-8")
            return httpx.Response(status, content=content, headers={"content-type": "application/json", **(extra or {})})

        if host == DOWNLOAD_HOST:
            return self._download(path, reply)
        if host != API_HOST:
            return reply(404, {"message": "Not Found"})
        for fault in self._faults:
            if path.endswith(fault.suffix) and fault.times > 0:
                fault.times -= 1
                return reply(fault.status, {"message": "scripted fault"}, extra=dict(fault.headers))
        is_billing = path.startswith(self._billing_root + "/settings/billing/")
        wanted = self.billing_token if is_billing else self.token
        if auth != "Bearer " + wanted:
            return reply(401, {"message": "Bad credentials"})
        if path == self._root + "/copilot/metrics/reports/users-1-day":
            return self._report(params, "users", reply)
        agg = "organization-1-day" if self.level == "organization" else "enterprise-1-day"
        if path == self._root + "/copilot/metrics/reports/" + agg:
            return self._report(params, "aggregate", reply)
        if path == self._billing_root + "/settings/billing/ai_credit/usage":
            return self._billing(params, reply)
        if path == self._root + "/copilot/billing/seats":
            return self._seats(params, reply)
        if path == self._root + "/copilot/billing" and self.level == "organization":
            return reply(200, {"seat_breakdown": self.summary, "seat_management_setting": "assign_selected",
                               "plan_type": "business"})
        return reply(404, {"message": "Not Found"})

    def _report(self, params: Dict[str, str], kind: str, reply: Callable[..., httpx.Response]) -> httpx.Response:
        day = params.get("day")
        if not day:
            return reply(422, {"message": "day is required"})
        if day in self.no_report_days:
            return reply(204)
        if self.broken_envelope:
            return reply(200, {"report_day": day})
        n = max(1, self.links_per_report)
        links = ["https://%s/%s-%s-%d.ndjson?sig=synthetic-signature" % (DOWNLOAD_HOST, kind, day, i + 1) for i in range(n)]
        return reply(200, {"download_links": links, "report_day": day})

    def _download(self, path: str, reply: Callable[..., httpx.Response]) -> httpx.Response:
        name = path.rsplit("/", 1)[-1]
        once = self.download_status_once.get(name)
        if once is not None:
            del self.download_status_once[name]
            return reply(once, {"message": "link expired"})
        stem = name[: -len(".ndjson")]
        kind, rest = stem.split("-", 1)
        day, index = rest.rsplit("-", 1)
        rows = self.users.get(day, []) if kind == "users" else ([self.aggregates[day]] if day in self.aggregates else [])
        n = max(1, self.links_per_report)
        chunk = [r for i, r in enumerate(rows) if i % n == int(index) - 1]
        text = "".join(_dumps(r, sort=True) + "\n" for r in chunk)
        return reply(200, text=text)

    def _billing(self, params: Dict[str, str], reply: Callable[..., httpx.Response]) -> httpx.Response:
        if "year" not in params or "month" not in params:
            return reply(422, {"message": "year and month are required"})
        year, month = int(params["year"]), int(params["month"])
        period = {"year": year, "month": month}
        if "user" in params:
            items = self.billing_user.get((params["user"], year, month), [])
        elif "day" in params:
            period["day"] = int(params["day"])
            items = self.billing_day.get("%04d-%02d-%02d" % (year, month, int(params["day"])), [])
        else:
            items = []
        body = {"timePeriod": period, ("organization" if self.level == "organization" else "enterprise"): self.slug,
                "usageItems": items}
        return reply(200, body)

    def _seats(self, params: Dict[str, str], reply: Callable[..., httpx.Response]) -> httpx.Response:
        page = int(params.get("page", "1"))
        size = min(int(params.get("per_page", "30")), 100, self.seats_per_page)
        chunk = self.seats[(page - 1) * size: page * size]
        people = {s["assignee"]["id"] for s in self.seats if s["assignee"] is not None}
        unheld = sum(1 for s in self.seats if s["assignee"] is None)  # total_seats counts a seat nobody holds, too
        total = self.total_seats if self.total_seats is not None else len(people) + unheld
        extra: Dict[str, str] = {}
        if page * size < len(self.seats):
            extra["link"] = '<https://api.github.com%s?page=%d&per_page=%d>; rel="next"' % (
                self._root + "/copilot/billing/seats", page + 1, size)
        return reply(200, {"total_seats": total, "seats": chunk}, extra=extra)


def _dumps(value: Any, sort: bool = False) -> str:
    """JSON text of a body; a Decimal is written as the number it holds, so no figure passes through a binary float."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        items = sorted(value.items()) if sort else value.items()
        return "{" + ", ".join("%s: %s" % (json.dumps(k), _dumps(v, sort)) for k, v in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_dumps(v, sort) for v in value) + "]"
    return json.dumps(value)
