"""Helpers shared by the tests: a small synthetic dataset, CLI runs, record reads."""

import json
from pathlib import Path
from typing import Any, Dict, List

from meterline_anthropic_admin.testing import crow, urow, utc

FAKE_KEY = "sk" + "-ant-admin01-synthetic-not-a-real-key"
BASE_URL = "https://api.anthropic.com"
ORG = "org-label"
D1 = "2026-09-15"
D2 = "2026-09-16"
NOW = utc("2026-09-20T06:00:00")
USAGE = "usage"
TOTALS = "org_totals"


def basic_usage() -> List[Dict[str, Any]]:
    """Two keys, two models, two days, cache figures and one console (no key) row."""
    return [
        urow(D1, "apikey_a", "model-a", uncached=1000, c5=300, c1=100, read=600, out=50, web=2),
        urow(D1, "apikey_a", "model-b", uncached=200, out=20),
        urow(D1, "apikey_b", "model-a", uncached=400, read=100, out=40),
        urow(D1, None, "model-a", ws=None, uncached=50, out=5),
        urow(D2, "apikey_a", "model-a", uncached=300, out=30),
    ]


def basic_cost() -> List[Dict[str, Any]]:
    """Cents, as strings. model-a on D1 is 134.28912 cents; model-b has no cost row on D1 or D2."""
    return [
        crow(D1, "model-a", "uncached_input_tokens", "123.78912"),
        crow(D1, "model-a", "output_tokens", "10.5"),
        crow(D1, None, None, "5", cost_type="web_search"),
        crow(D2, "model-a", "uncached_input_tokens", "40"),
    ]


def run(argv, api, clock, now=NOW, **kw):
    from meterline_anthropic_admin.cli import main

    return main(list(argv), transport=api.transport, clock=clock, sleep=clock.sleep,
                now=lambda: now, jitter=lambda: 0, **kw)


def daily(data_dir, api, clock, now=NOW, *extra):
    return run(["daily", "--data-dir", str(data_dir), "--provider-account-id", ORG, *extra], api, clock, now)


def backfill(data_dir, api, clock, since="2026-09-14", now=NOW, *extra):
    return run(["backfill", "--since", since, "--data-dir", str(data_dir), "--provider-account-id", ORG, *extra], api, clock, now)


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


def usage_by_key(data_dir) -> Dict[tuple, Any]:
    return {(r.actor_id, r.period_start.isoformat(), r.model): r for r in records(data_dir, USAGE)}


def totals_by_key(data_dir) -> Dict[tuple, Any]:
    return {(r.product, r.period_start.isoformat(), r.model): r for r in records(data_dir, TOTALS)}


def no_records(data_dir) -> bool:
    folder = Path(data_dir) / "records"
    return not (folder.exists() and list(folder.rglob("*.jsonl")))
