"""Generate the synthetic fixture dataset for the fake analytics API.

Everything here is invented: users, emails (example.com only), ids, usage and
cost. No network, no real data. Output is deterministic (seeded, sorted keys,
gzip with mtime 0), so re-running the script gives byte-identical files.

The rows follow the documented response shapes of spec v1.1 section 2.1
(``docs/api-shapes/``). A dataset row holds what the API puts in a row, minus
the day fields: the fake API stamps ``starting_at`` and ``ending_at`` from the
dataset's day key, and adds ``organization_id`` at the top level of a response.

Money never touches a binary-float type: costs are integer micro-cents, shown
as decimal cent strings with six decimals.

Run:  python generate.py        (writes into ./data next to this file)
"""

import gzip
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

SEED = 20261009
START = date(2026, 7, 1)
END = date(2026, 10, 8)
PROVIDER_ACCOUNT_ID = "org-synthetic-0001"
CURRENCY = "USD"

FIRST = ["alex", "blake", "casey", "drew", "emery", "finley", "gray", "harper", "indy", "jules"]
LAST = ["archer", "brook", "clarke", "dalton", "ellis"]
PRODUCTS = ["claude_code", "chat"]
MODELS = ["model-a-large", "model-b-small", "model-c-mid"]
# integer micro-cents per token: input, output, cache write 5m, cache write 1h, cache read
RATES = {
    "model-a-large": (300, 1500, 375, 600, 30),
    "model-b-small": (100, 500, 125, 200, 10),
    "model-c-mid": (1500, 7500, 1875, 3000, 150),
}
DELETED_USERS = {"user-047", "user-048", "user-049", "user-050"}
DELETED_AFTER = date(2026, 8, 31)
PINNED = {
    "actor_id": "user-001",
    "date": "2026-09-15",
    "product": "claude_code",
    "model": "model-a-large",
    "amount": "41280.000000",
}
USAGE_WITHOUT_COST_INDEXES = (100, 2000)
EDIT_TOOLS = ("edit_tool", "multi_edit_tool", "notebook_edit_tool", "write_tool")
OFFICE_APPS = ("excel", "outlook", "powerpoint", "word")


def cents_str(micro):
    """Integer micro-cents -> decimal cent string with six decimals."""
    return "{}.{:06d}".format(micro // 1_000_000, micro % 1_000_000)


def daterange(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def build_users():
    users = []
    n = 0
    for first in FIRST:
        for last in LAST:
            n += 1
            user_id = "user-{:03d}".format(n)
            users.append(
                {
                    "user_id": user_id,
                    "email": "{}.{}@example.com".format(first, last),
                    "name": "{} {}".format(first.title(), last.title()),
                    "deleted": user_id in DELETED_USERS,
                }
            )
    return users


def actor_for(user, messy_email):
    if user["deleted"]:
        email = None
    elif messy_email:
        # exercises trim + lower-case in the normaliser; the domain stays lower-case
        email = "  {}@example.com ".format(user["email"].split("@")[0].title())
    else:
        email = user["email"]
    return {
        "type": "user_actor",
        "user_id": user["user_id"],
        "email_address": email,
        "name": None if user["deleted"] else user["name"],
        "deleted": user["deleted"],
    }


def _chat_block(rng):
    return {
        "distinct_conversation_count": rng.randrange(1, 10),
        "message_count": rng.randrange(1, 80),
        "distinct_artifacts_created_count": rng.randrange(0, 5),
        "distinct_skills_used_count": rng.randrange(0, 2),
        "distinct_connectors_used_count": rng.randrange(0, 3),
    }


def _zero_chat():
    return dict.fromkeys(_chat_block(random.Random(0)), 0)


def _cowork_block(rng):
    return {
        "distinct_session_count": rng.randrange(1, 5),
        "message_count": rng.randrange(1, 40),
        "artifacts_created_count": rng.randrange(0, 3),
        "distinct_skills_used_count": rng.randrange(0, 3),
        "distinct_connectors_used_count": rng.randrange(0, 3),
    }


def _zero_cowork():
    return dict.fromkeys(_cowork_block(random.Random(0)), 0)


def _app_block(rng, on):
    if not on:
        return {
            "distinct_session_count": 0, "message_count": 0,
            "distinct_skills_used_count": 0, "distinct_connectors_used_count": 0,
        }
    return {
        "distinct_session_count": rng.randrange(1, 4),
        "message_count": rng.randrange(1, 25),
        "distinct_skills_used_count": rng.randrange(0, 2),
        "distinct_connectors_used_count": rng.randrange(0, 2),
    }


def _code_block(rng, on):
    if not on:
        return {
            "core_metrics": {
                "distinct_session_count": 0, "commit_count": 0, "pull_request_count": 0,
                "artifacts_created_count": 0,
                "lines_of_code": {"added_count": 0, "removed_count": 0},
            },
            "tool_actions": {t: {"accepted_count": 0, "rejected_count": 0} for t in EDIT_TOOLS},
        }
    return {
        "core_metrics": {
            "distinct_session_count": rng.randrange(1, 8),
            "commit_count": rng.randrange(0, 12),
            "pull_request_count": rng.randrange(0, 4),
            "artifacts_created_count": rng.randrange(0, 2),
            "lines_of_code": {
                "added_count": rng.randrange(0, 1500),
                "removed_count": rng.randrange(0, 800),
            },
        },
        "tool_actions": {
            t: {"accepted_count": rng.randrange(0, 20), "rejected_count": rng.randrange(0, 5)}
            for t in EDIT_TOOLS
        },
    }


def activity_row(user, habit, day, rng):
    """One ``users`` row: every product block as a sibling field."""
    uses_code = "claude_code" in habit["products"]
    uses_chat = "chat" in habit["products"]
    uses_cowork = habit["cowork"]
    uses_office = habit["office"]
    row = {
        "user": {"type": "user", "id": user["user_id"], "email_address": user["email"]},
        "chat_metrics": _chat_block(rng) if uses_chat else _zero_chat(),
        "claude_code_metrics": _code_block(rng, uses_code),
        "cowork_metrics": _cowork_block(rng) if uses_cowork else _zero_cowork(),
        "office_metrics": {
            app: _app_block(rng, uses_office and app == "excel") for app in OFFICE_APPS
        },
    }
    # design and science: sessions are null when there is no message
    row["design_metrics"] = {"distinct_session_count": None, "message_count": 0}
    row["science_metrics"] = {"distinct_session_count": None, "message_count": 0}
    if habit["unified"]:
        # present but all zero: a block that must yield no record
        row["chat_cowork_unified_metrics"] = {"chat": _zero_chat(), "sessions": _zero_cowork()}
    row["web_search_count"] = rng.randrange(0, 10) if uses_chat else 0
    row["last_activity_date"] = day.isoformat()
    return row


def _build_habits(rng, users):
    """Per-user habits, drawn in user order from the seeded generator."""
    habits = {}
    for u in users:
        uses_code = rng.randrange(100) < 70
        uses_chat = rng.randrange(100) < 80 or not uses_code
        habits[u["user_id"]] = {
            "active_pct": rng.randrange(40, 96),
            "products": [p for p, on in (("claude_code", uses_code), ("chat", uses_chat)) if on],
            "models": {
                p: rng.sample(MODELS, rng.randrange(1, 4)) for p in PRODUCTS
            },
            "cowork": rng.randrange(100) < 25,
            "office": rng.randrange(100) < 15,
            "unified": rng.randrange(100) < 10,
        }
    # the pinned row's user must use that product and model
    h1 = habits[PINNED["actor_id"]]
    if PINNED["product"] not in h1["products"]:
        h1["products"].append(PINNED["product"])
    if PINNED["model"] not in h1["models"][PINNED["product"]]:
        h1["models"][PINNED["product"]].append(PINNED["model"])
    return habits


def _usage_cost_pair(rng, actor, uid, dkey, product, model):
    """One usage row and its cost row for one actor, day, product and model."""
    t_in = rng.randrange(1000, 400000)
    t_out = rng.randrange(500, 100000)
    t_c5 = rng.randrange(0, 200000)
    t_c1 = rng.randrange(0, 50000) if rng.randrange(100) < 20 else 0
    t_cr = rng.randrange(0, 2000000)
    reqs = rng.randrange(1, 300)
    web = rng.randrange(0, 10) if product == "chat" else 0
    rate = RATES[model]
    micro = (
        t_in * rate[0] + t_out * rate[1] + t_c5 * rate[2]
        + t_c1 * rate[3] + t_cr * rate[4]
    )
    list_micro = micro * 110 // 100 if rng.randrange(100) < 50 else micro
    if (
        uid == PINNED["actor_id"] and dkey == PINNED["date"]
        and product == PINNED["product"] and model == PINNED["model"]
    ):
        micro = 41280 * 1_000_000
        list_micro = micro * 110 // 100
    urow = {
        "actor": actor,
        "product": product,
        "model": model,
        "uncached_input_tokens": t_in,
        "output_tokens": t_out,
        "cache_creation": {
            "ephemeral_5m_input_tokens": t_c5,
            "ephemeral_1h_input_tokens": t_c1,
        },
        "cache_read_input_tokens": t_cr,
        "total_tokens": t_in + t_out + t_c5 + t_c1 + t_cr,
        "requests": reqs,
        "server_tool_use": {"web_search_requests": web},
    }
    crow = {
        "actor": actor,
        "product": product,
        "model": model,
        "amount": cents_str(micro),
        "list_amount": cents_str(list_micro),
        "currency": CURRENCY,
        "requests": reqs,
    }
    return urow, crow


def _user_day_rows(rng, u, h, day, dkey, forced, usage_rows, cost_rows, all_usage_flat):
    """Append the usage and cost rows of one active user on one day."""
    uid = u["user_id"]
    messy = uid == "user-007"
    actor = actor_for(u, messy)
    for product in h["products"]:
        chosen = [m for m in h["models"][product] if rng.randrange(100) < 70]
        if not chosen:
            chosen = [h["models"][product][0]]
        if forced and product == PINNED["product"] and PINNED["model"] not in chosen:
            chosen.append(PINNED["model"])
        for model in sorted(chosen):
            urow, crow = _usage_cost_pair(rng, actor, uid, dkey, product, model)
            usage_rows.append(urow)
            cost_rows.append(crow)
            all_usage_flat.append((dkey, urow, crow))


def _build_day_rows(rng, users, habits):
    """Usage, cost and activity rows per day, and the active users per day."""
    usage = {}
    cost = {}
    activity = {}
    active_users_by_day = {}
    all_usage_flat = []

    for day in daterange(START, END):
        dkey = day.isoformat()
        weekend = day.weekday() >= 5
        usage[dkey], cost[dkey], activity[dkey] = [], [], []
        active_today = set()
        for u in users:
            uid = u["user_id"]
            if u["deleted"] and day > DELETED_AFTER:
                continue
            h = habits[uid]
            pct = h["active_pct"] // 5 if weekend else h["active_pct"]
            forced = uid == PINNED["actor_id"] and dkey == PINNED["date"]
            if not forced and rng.randrange(100) >= pct:
                continue
            active_today.add(uid)
            _user_day_rows(rng, u, h, day, dkey, forced, usage[dkey], cost[dkey], all_usage_flat)
            activity[dkey].append(activity_row(u, h, day, rng))
        active_users_by_day[dkey] = active_today
    return usage, cost, activity, active_users_by_day, all_usage_flat


def _drop_some_costs(cost, all_usage_flat):
    """A few usage rows get no cost row (spec rule 3: cost stays null, never 0)."""
    without_cost = []
    for idx in USAGE_WITHOUT_COST_INDEXES:
        dkey, urow, crow = all_usage_flat[idx]
        assert not (
            urow["actor"]["user_id"] == PINNED["actor_id"] and dkey == PINNED["date"]
            and urow["product"] == PINNED["product"] and urow["model"] == PINNED["model"]
        )
        cost[dkey] = [c for c in cost[dkey] if c is not crow]
        without_cost.append(
            {
                "date": dkey,
                "actor_id": urow["actor"]["user_id"],
                "product": urow["product"],
                "model": urow["model"],
            }
        )
    return without_cost


def _build_org_rows(rng, usage, cost):
    """Organization totals (no group_by: one row per day): exact sum of the user
    rows plus API-key traffic that is never attributed to a user."""
    org_usage = {}
    org_cost = {}
    for day in daterange(START, END):
        dkey = day.isoformat()
        a = [0, 0, 0, 0, 0, 0, 0]
        for row in usage[dkey]:
            a[0] += row["uncached_input_tokens"]
            a[1] += row["output_tokens"]
            a[2] += row["cache_creation"]["ephemeral_5m_input_tokens"]
            a[3] += row["cache_creation"]["ephemeral_1h_input_tokens"]
            a[4] += row["cache_read_input_tokens"]
            a[5] += row["requests"]
            a[6] += row["server_tool_use"]["web_search_requests"]
        c = [0, 0]
        for row in cost[dkey]:
            c[0] += _micro(row["amount"])
            c[1] += _micro(row["list_amount"])
        for model in MODELS:
            t_in = rng.randrange(100000, 2000000)
            t_out = rng.randrange(50000, 800000)
            t_cr = rng.randrange(0, 3000000)
            reqs = rng.randrange(100, 2000)
            rate = RATES[model]
            micro = t_in * rate[0] + t_out * rate[1] + t_cr * rate[4]
            a[0] += t_in
            a[1] += t_out
            a[4] += t_cr
            a[5] += reqs
            c[0] += micro
            c[1] += micro
        org_usage[dkey] = [
            {
                "uncached_input_tokens": a[0],
                "output_tokens": a[1],
                "cache_creation": {
                    "ephemeral_5m_input_tokens": a[2],
                    "ephemeral_1h_input_tokens": a[3],
                },
                "cache_read_input_tokens": a[4],
                "requests": a[5],
                "server_tool_use": {"web_search_requests": a[6]},
            }
        ]
        org_cost[dkey] = [
            {
                "amount": cents_str(c[0]),
                "list_amount": cents_str(c[1]),
                "currency": CURRENCY,
                "requests": a[5],
            }
        ]
    return org_usage, org_cost


def _build_summaries(rng, users, active_users_by_day):
    """Seat snapshots, in the documented shape (ending_at is the next midnight)."""
    summaries = []
    days = list(daterange(START, END))
    for i, day in enumerate(days):
        dkey = day.isoformat()
        week = set().union(*[active_users_by_day[d.isoformat()] for d in days[max(0, i - 6): i + 1]])
        month = set().union(*[active_users_by_day[d.isoformat()] for d in days[max(0, i - 29): i + 1]])
        invites = rng.randrange(0, 6)
        summaries.append(
            {
                "starting_at": dkey + "T00:00:00Z",
                "ending_at": (day + timedelta(days=1)).isoformat() + "T00:00:00Z",
                "assigned_seat_count": len(users) - len([u for u in users if u["deleted"] and day > DELETED_AFTER]),
                # a null pending count on every tenth day exercises the null path
                "pending_invite_count": None if i % 10 == 9 else invites,
                "daily_active_user_count": len(active_users_by_day[dkey]),
                "weekly_active_user_count": len(week),
                "monthly_active_user_count": len(month),
            }
        )
    return summaries


def build():
    rng = random.Random(SEED)
    users = build_users()
    habits = _build_habits(rng, users)
    usage, cost, activity, active_users_by_day, all_usage_flat = _build_day_rows(rng, users, habits)
    without_cost = _drop_some_costs(cost, all_usage_flat)
    org_usage, org_cost = _build_org_rows(rng, usage, cost)
    summaries = _build_summaries(rng, users, active_users_by_day)

    # sort rows for determinism
    def _urow_key(r):
        return (r["actor"]["user_id"], r["product"], r["model"])

    for dkey in usage:
        usage[dkey].sort(key=_urow_key)
        cost[dkey].sort(key=_urow_key)
        activity[dkey].sort(key=lambda r: r["user"]["id"])

    dataset = {
        "meta": {
            "synthetic": True,
            "seed": SEED,
            "start": START.isoformat(),
            "end": END.isoformat(),
            "currency": CURRENCY,
            "amount_unit": "cents (decimal strings, six decimals)",
            "pinned_cost_row": PINNED,
            "usage_rows_without_cost": sorted(
                without_cost, key=lambda r: (r["date"], r["actor_id"], r["product"], r["model"])
            ),
        },
        "provider_account_id": PROVIDER_ACCOUNT_ID,
        "users": users,
        "usage": usage,
        "cost": cost,
        "org_usage": org_usage,
        "org_cost": org_cost,
        "activity": activity,
        "summaries": summaries,
    }
    return dataset, expected_totals(dataset)


def _micro(cents_text):
    whole, frac = cents_text.split(".")
    return int(whole) * 1_000_000 + int(frac)


def expected_totals(dataset):
    """Cost per (actor, model) in major currency units (cents / 100), Decimal
    strings. Rows without a cost row contribute nothing."""
    sums = {}
    for dkey in dataset["cost"]:
        for row in dataset["cost"][dkey]:
            actor = row["actor"]["user_id"]
            sums.setdefault(actor, {}).setdefault(row["model"], Decimal(0))
            sums[actor][row["model"]] += Decimal(row["amount"]) / Decimal(100)
    return {
        "currency": CURRENCY,
        "unit": "major currency units (cents / 100)",
        "cost_by_actor_model": {
            actor: {model: str(total) for model, total in sorted(models.items())}
            for actor, models in sorted(sums.items())
        },
    }


def write_gzip_json(path, obj):
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            gz.write(payload)


def main():
    out = Path(__file__).resolve().parent / "data"
    out.mkdir(parents=True, exist_ok=True)
    dataset, expected = build()
    write_gzip_json(out / "dataset.json.gz", dataset)
    (out / "expected_totals.json").write_text(
        json.dumps(expected, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (out / "SYNTHETIC.json").write_text(
        json.dumps(
            {"synthetic": True, "generator": "generate.py", "seed": SEED},
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    n_usage = sum(len(v) for v in dataset["usage"].values())
    print("wrote {} (usage rows: {})".format(out, n_usage))


if __name__ == "__main__":
    main()
