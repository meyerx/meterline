"""Helpers shared by the LiteLLM tests: dataset rows, CLI runs, record reads."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

from meterline_litellm.testing import row

PROXY = "proxy-one"
D1 = "2026-09-15"
D2 = "2026-09-16"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


NOW = at("2026-09-20T06:00:00")


def users(*names: str) -> List[Dict[str, Any]]:
    return [{"user_id": n, "user_email": "%s@example.com" % n.replace("user-", "person-")} for n in names]


def basic_rows() -> List[Dict[str, Any]]:
    """Two users, two models, two days, cache figures included."""
    return [
        row(D1, "user-a", "model-a", prompt_tokens=1000, completion_tokens=50, cache_read_input_tokens=600,
            cache_creation_input_tokens=300, api_requests=4, successful_requests=3, failed_requests=1, spend=Decimal("0.50")),
        row(D1, "user-a", "model-b", prompt_tokens=200, completion_tokens=20, api_requests=2, successful_requests=2, spend=Decimal("0.07")),
        row(D1, "user-b", "model-a", prompt_tokens=400, completion_tokens=40, cache_read_input_tokens=100,
            api_requests=1, successful_requests=1, spend=Decimal("0.21")),
        row(D2, "user-a", "model-a", prompt_tokens=300, completion_tokens=30, api_requests=1, successful_requests=1, spend=Decimal("0.12")),
    ]


def run(argv, proxy, clock, now=NOW, **kw):
    from meterline_litellm.cli import main

    return main(list(argv), transport=proxy.transport, clock=clock, sleep=clock.sleep,
                now=lambda: now, jitter=lambda: 0, **kw)


def daily(data_dir, proxy, clock, now=NOW, *extra):
    return run(["daily", "--data-dir", str(data_dir), "--proxy-name", PROXY, *extra], proxy, clock, now)


def backfill(data_dir, proxy, clock, since="2026-09-14", now=NOW, *extra):
    return run(["backfill", "--since", since, "--data-dir", str(data_dir), "--proxy-name", PROXY, *extra],
               proxy, clock, now)


def records(data_dir, kind: str) -> list:
    from meterline import KINDS, read_jsonl

    out: list = []
    folder = Path(data_dir) / "records" / kind
    for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        out.extend(read_jsonl(path, KINDS[kind]))
    return out


def latest(data_dir, kind: str) -> dict:
    from meterline import latest_per_key

    return latest_per_key(records(data_dir, kind))


def manifests(data_dir) -> List[Dict[str, Any]]:
    folder = Path(data_dir) / "runs"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def sig(rec) -> dict:
    """A record as a dict without batch fields, for comparing two runs."""
    d = rec.to_dict()
    for name in ("pulled_at", "data_refreshed_at", "batch_id"):
        d.pop(name, None)
    return d
