"""Helpers shared by the Copilot tests: CLI runs and record reads."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

SLUG = "example-org"
FAKE_TOKEN = "synthetic-usage-token-not-real-0001"
FAKE_BILLING_TOKEN = "synthetic-billing-token-not-real-0002"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


NOW = at("2026-09-20T06:00:00")


def run(argv, api, clock, now=NOW, **kw):
    from meterline_copilot.cli import main

    return main(list(argv), transport=api.transport, clock=clock, sleep=clock.sleep, wall=clock,
                now=lambda: now, jitter=lambda: 0, **kw)


def _level_args(api):
    return ["--level", api.level, "--slug", api.slug]


def bare_daily(data_dir, api, clock, now=NOW, *extra):
    """``daily`` exactly as given: with no state file and no ``--since`` it stops (F-11)."""
    return run(["daily", "--data-dir", str(data_dir), *_level_args(api), *extra], api, clock, now)


def daily(data_dir, api, clock, now=NOW, *extra):
    """``daily``; a first run (no state file) starts at the first of the month, where it used to start."""
    if "--since" not in extra and not (Path(data_dir) / "state.json").exists():
        extra = (*extra, "--since", now.replace(day=1).date().isoformat())
    return bare_daily(data_dir, api, clock, now, *extra)


def backfill(data_dir, api, clock, since="2026-09-14", now=NOW, *extra):
    return run(["backfill", "--since", since, "--data-dir", str(data_dir), *_level_args(api), *extra], api, clock, now)


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
    d = rec.to_dict()
    for name in ("pulled_at", "data_refreshed_at", "batch_id"):
        d.pop(name, None)
    return d
