"""Test helper for done-check F08: the table of spec v2 section 10 as code.

It maps the synthetic inputs of ``docs/api-shapes/fit/`` to meterline records.
It is not a connector: it reads files, never a network, and nothing outside the
tests uses it.
"""

import csv
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import meterline as ml

HUNDRED = Decimal(100)
PULLED_AT = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
BATCH = "20261009T080000Z"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def _common(provider: str, provider_account_id: str, day: date, **extra: Any) -> Dict[str, Any]:
    return dict(
        provider=provider, provider_account_id=provider_account_id, grain="day", period_start=day, period_end=day,
        pulled_at=PULLED_AT, batch_id=BATCH, **extra,
    )


def _usage(provider, provider_account_id, day, actor_kind, actor_id, model, **kw):
    kw.setdefault("is_final", False)
    kw.setdefault("product", "")
    return ml.UsageRecord(
        **_common(provider, provider_account_id, day, actor_kind=actor_kind, actor_id=actor_id, model=model, **kw)
    )


def _email(value: str) -> str:
    return value.strip().lower()


def cursor_usage(path: Path) -> List[Any]:
    """Events summed per day, user and model."""
    sums: Dict[tuple, Dict[str, Decimal]] = {}
    for event in _load(path)["usageEvents"]:
        day = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).date()
        key = (day, _email(event["userEmail"]), event["model"])
        acc = sums.setdefault(key, {n: Decimal(0) for n in (
            "in", "read", "write", "out", "total", "charged", "req")})
        usage = event["tokenUsage"]
        acc["in"] += usage["inputTokens"]
        acc["read"] += usage["cacheReadTokens"]
        acc["write"] += usage["cacheWriteTokens"]
        acc["out"] += usage["outputTokens"]
        acc["total"] += Decimal(str(usage["totalCents"]))
        acc["charged"] += Decimal(str(event["chargedCents"]))
        acc["req"] += Decimal(str(event["requestsCosts"]))
    out = []
    for (day, email, model), a in sorted(sums.items()):
        out.append(_usage(
            "cursor", "team-synthetic", day, "user", email, model, actor_email=email,
            tokens_input_uncached=int(a["in"]), tokens_cache_read=int(a["read"]),
            tokens_cache_write=int(a["write"]),
            tokens_input_total=int(a["in"] + a["read"] + a["write"]), tokens_output=int(a["out"]),
            quantities={"request_unit": a["req"]},
            cost=a["charged"] / HUNDRED, cost_list=a["total"] / HUNDRED, currency="USD",
            cost_basis="billed",
        ))
    return out


def cursor_activity(path: Path) -> List[Any]:
    out = []
    for row in _load(path)["data"]:
        day = date.fromisoformat(row["date"])
        email = _email(row["email"])
        out.append(ml.ActivityRecord(
            **_common("cursor", "team-synthetic", day, actor_kind="user", actor_id=email,
                      actor_email=email, product="cursor"),
            suggestions_shown=row["totalTabsShown"], suggestions_accepted=row["totalTabsAccepted"],
            lines_added=row["acceptedLinesAdded"], lines_removed=row["acceptedLinesDeleted"],
            edits_accepted=row["totalAccepts"], edits_rejected=row["totalRejects"],
            counters={"composer_requests": row["composerRequests"],
                      "chat_requests": row["chatRequests"], "agent_requests": row["agentRequests"]},
        ))
    return out


def openai_usage(path: Path) -> List[Any]:
    out = []
    for bucket in _load(path)["data"]:
        day = datetime.fromtimestamp(bucket["start_time"], tz=timezone.utc).date()
        for row in bucket["results"]:
            if row.get("user_id"):
                kind, actor = "user", row["user_id"]
            else:
                kind, actor = "api_key", row["api_key_id"]
            out.append(_usage(
                "openai", "org-synthetic", day, kind, actor, row["model"],
                scope=row.get("project_id") or "",
                tokens_input_uncached=row["input_uncached_tokens"],
                tokens_cache_read=row["input_cached_tokens"],
                tokens_cache_write=row["input_cache_write_tokens"],
                tokens_input_total=row["input_tokens"], tokens_output=row["output_tokens"],
                requests=row["num_model_requests"],
            ))
    return out


def openai_costs(path: Path) -> List[Any]:
    out = []
    for bucket in _load(path)["data"]:
        day = datetime.fromtimestamp(bucket["start_time"], tz=timezone.utc).date()
        for row in bucket["results"]:
            out.append(ml.OrgTotal(
                **_common("openai", "org-synthetic", day, scope=row["project_id"]),
                is_final=False, cost=Decimal(str(row["amount"]["value"])),
                currency=row["amount"]["currency"].upper(), cost_basis="billed",
            ))
    return out


def augment_usage(path: Path) -> List[Any]:
    out = []
    for row in _load(path)["data"]:
        day = date.fromisoformat(row["date"])
        if row.get("service_account_name"):
            kind, actor, email = "service_account", row["service_account_name"], None
        else:
            email = _email(row["user_email"])
            kind, actor = "user", email
        tokens = row["token_metrics"]
        out.append(_usage(
            "augment", "org-synthetic", day, kind, actor, row["model"], actor_email=email,
            tokens_input_uncached=tokens["input_tokens"], tokens_cache_read=tokens["cache_read_tokens"],
            tokens_cache_write=tokens["cache_write_tokens"],
            tokens_input_total=tokens["input_tokens"] + tokens["cache_read_tokens"] + tokens["cache_write_tokens"],
            tokens_output=tokens["output_tokens"],
            quantities={"credit": Decimal(str(row["credits_consumed"]))},
            cost=Decimal(str(row["cost_metrics"]["billed_amount_usd"])), currency="USD",
            cost_basis="billed",
        ))
    return out


def kiro(path: Path) -> List[Any]:
    usage, activity = [], []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            day = date.fromisoformat(row["Date"])
            email = _email(row["User_Email"])
            usage.append(_usage(
                "kiro", "org-synthetic", day, "user", row["UserId"], "", scope=row["Client_Type"],
                actor_email=email,
                quantities={"credit": Decimal(row["Credits_Used"]),
                            "overage_credit": Decimal(row["Overage_Credits_Used"])},
            ))
            activity.append(ml.ActivityRecord(
                **_common("kiro", "org-synthetic", day, scope=row["Client_Type"], actor_kind="user",
                          actor_id=row["UserId"], actor_email=email, product="kiro"),
                messages=int(row["Total_Messages"]),
            ))
    return usage + activity


def windsurf(path: Path) -> List[Any]:
    out = []
    for row in _load(path)["data"]:
        out.append(_usage(
            "windsurf", "org-synthetic", date.fromisoformat(row["date"]), "user", row["user_id"],
            row["model_uid"], actor_email=_email(row["user_email"]),
            quantities={"prompt_credit": Decimal(str(row["prompt_credits"])),
                        "flex_credit": Decimal(str(row["flex_credits"]))},
        ))
    return out


def map_all(directory: Path) -> Dict[str, List[Any]]:
    """Records per tool, from the files of ``docs/api-shapes/fit/``."""
    d = Path(directory)
    return {
        "cursor": cursor_usage(d / "cursor_filtered_usage_events.json")
        + cursor_activity(d / "cursor_daily_usage_data.json"),
        "openai": openai_usage(d / "openai_usage_completions.json")
        + openai_costs(d / "openai_costs.json"),
        "augment": augment_usage(d / "augment_analytics.json"),
        "kiro": kiro(d / "kiro_user_activity.csv"),
        "windsurf": windsurf(d / "windsurf_consumption.json"),
    }
