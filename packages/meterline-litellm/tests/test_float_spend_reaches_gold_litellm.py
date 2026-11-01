"""MF-2: a real LiteLLM spend (a binary float, 19 decimals) goes from the proxy through the connector into silver and
through the gold build. Before the shared scale the connector wrote every digit, the gold build refused anything past
twelve decimals (exit 2), and ``--parquet`` died on an uncaught ArrowInvalid.

The connector may not import gold, and a test of a package declares only what that package imports, so the gold build is
driven as the program a user runs: a subprocess on the silver this connector wrote, with the synthetic gold fixture
(``docs/gold-fixture``) for the organisation, policy and prices. The test skips when gold is not installed.
"""

import csv
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from meterline_litellm.cli import main
from meterline_litellm.testing import FakeAPI, row

FIXTURE = Path(__file__).resolve().parents[3] / "docs" / "gold-fixture" / "input"
SPEND = Decimal("0.0027199999999999998")  # as the proxy renders a float: 19 decimals
HELD = "0.00272"  # the twelve-decimal value the record keeps (ROUND_HALF_EVEN)
GOLD_KEY = "meterline-gold-fixture-key-not-a-secret"
GOLD_CLI = "import sys; from meterline_gold import cli; sys.exit(cli.main(sys.argv[1:]))"


def _silver_from_proxy(tmp_path, parquet):
    rows = [row("2026-05-13", "lu-06", "model-y-standard", prompt_tokens=100, completion_tokens=10, api_requests=1,
                successful_requests=1, spend=SPEND)]
    proxy = FakeAPI(rows, users=[{"user_id": "lu-06", "user_email": "rasovemila@example.com"}])
    silver = tmp_path / "silver"
    argv = ["backfill", "--since", "2026-05-13", "--data-dir", str(silver), "--proxy-name", "fixture-proxy"]
    if parquet:
        argv.append("--parquet")
    now = datetime(2026, 6, 10, 6, 0, tzinfo=timezone.utc)
    assert main(argv, transport=proxy.transport, sleep=lambda _s: None, now=lambda: now, jitter=lambda: 0) == 0
    return silver


@pytest.mark.skipif(importlib.util.find_spec("meterline_gold") is None, reason="meterline-gold is not installed")
@pytest.mark.parametrize("parquet", [False, True])
def test_float_spend_reaches_gold_litellm(tmp_path, monkeypatch, parquet):
    if parquet:
        pytest.importorskip("pyarrow")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-synthetic-not-a-real-key")
    monkeypatch.setenv("LITELLM_BASE_URL", "https://proxy.example.com")
    silver = _silver_from_proxy(tmp_path, parquet)
    if parquet:
        assert sorted((silver / "records" / "usage").glob("*.parquet")), "--parquet wrote no file"
    out = tmp_path / "gold"
    build = [sys.executable, "-c", GOLD_CLI, "build", "--silver", str(silver), "--org-files", str(FIXTURE / "org"),
             "--policy", str(FIXTURE / "policy.yaml"), "--prices", str(FIXTURE / "prices.yaml"), "--out", str(out)]
    done = subprocess.run(build, env={**os.environ, "METERLINE_GOLD_KEY": GOLD_KEY}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    with open(out / "central" / "person_day.csv", newline="", encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r["provider"] == "litellm"]
    assert [r["cost_source"] for r in rows] == [HELD]
