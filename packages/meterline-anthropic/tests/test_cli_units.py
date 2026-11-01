"""Unit tests for cli.py: resume, failed checks, key handling, parquet.
No done-check names here."""

import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from meterline_anthropic.cli import main

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 5, 7, 0, 0, tzinfo=timezone.utc)


def _run(transport, clock, data_dir, *argv, now=NOW):
    argv = _since_for_a_first_daily(data_dir, argv, now)
    return main(
        [*argv, "--data-dir", str(data_dir)],
        transport=transport, clock=clock, sleep=clock.sleep, now=lambda: now, jitter=lambda: 0,
    )


def _since_for_a_first_daily(data_dir, argv, now):
    """F-11: a first ``daily`` (no state file) needs ``--since``; these tests mean the first of the month,
    which is where daily used to start."""
    if argv[:1] == ("daily",) and "--since" not in argv and not (Path(data_dir) / "state.json").exists():
        return (*argv, "--since", now.replace(day=1).date().isoformat())
    return argv


def _state(data_dir):
    return json.loads((Path(data_dir) / "state.json").read_text(encoding="utf-8"))


def _record_dicts(data_dir):
    out = {}
    for path in sorted((Path(data_dir) / "records").glob("*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for volatile in ("pulled_at", "batch_id"):
                row.pop(volatile)
            out[(path.parent.name, json.dumps(row, sort_keys=True))] = path.name
    return set(out)


def test_backfill_resumes_after_a_failure(fake_api, fake_clock, tmp_path):
    flag = {"fail": True}
    seen = {"failed": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        starts = request.url.params.get("starting_at", "")
        if (
            flag["fail"]
            and request.url.path.endswith("/user_usage_report")
            and starts.startswith("2026-09-01")
        ):
            seen["failed"] += 1
            return httpx.Response(403, json={"error": {"type": "permission_error", "message": "forbidden"}})
        return fake_api.transport.handle_request(request)

    transport = httpx.MockTransport(handler)
    data_dir = tmp_path / "resume"
    args = ("backfill", "--since", "2026-08-01")

    assert _run(transport, fake_clock, data_dir, *args) == 3  # a 403 is a permanent refusal
    assert seen["failed"] == 1
    state = _state(data_dir)
    assert state["pending"]["command"] == "backfill"
    assert "usage:2026-08-01_2026-08-31" in state["pending"]["windows_done"]
    assert state["pending"]["raw_files"]
    assert not (data_dir / "records").exists()
    assert list((data_dir / "runs").glob("*.json")) == []

    done_before = len([e for e in fake_api.log if e.endpoint == "user_usage_report"])
    assert done_before >= 1
    flag["fail"] = False
    marker = len(fake_api.log)
    assert _run(transport, fake_clock, data_dir, *args, now=LATER) == 0

    again = [e for e in fake_api.log[marker:] if e.endpoint in ("user_usage_report", "user_cost_report", "usage_report", "cost_report")]
    assert all(not str(e.params["starting_at"]).startswith("2026-08-01") for e in again)
    assert _state(data_dir)["pending"] is None
    for kind in ("usage", "activity", "seats", "org_totals"):
        files = list((data_dir / "records" / kind).glob("*.jsonl"))
        assert [f.name for f in files] == ["20261005T070000Z.jsonl"]

    clean_dir = tmp_path / "clean"
    assert _run(fake_api.transport, fake_clock, clean_dir, *args, now=LATER) == 0
    assert _record_dicts(data_dir) == _record_dicts(clean_dir)


def test_failed_rule_six_check_publishes_nothing(fake_api, fake_clock, data_dir, capsys):
    def inflate(data):
        data["cost"]["2026-10-02"][0]["amount"] = "9" * 12 + ".000000"

    fake_api.mutate(inflate)
    assert _run(fake_api.transport, fake_clock, data_dir, "daily") == 2
    assert not (data_dir / "records").exists()
    manifest = json.loads((data_dir / "runs" / "20261005T060000Z.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert any("above the organization total" in f for f in manifest["failures"])
    assert manifest["published_counts"] == {}
    state = _state(data_dir)
    assert state["activity_loaded"] == []
    assert state["summaries_loaded"] == []
    assert state["pending"] is None
    assert "nothing published" in capsys.readouterr().err
    # the run manifest of a failed run is skipped by rebuild
    assert main(["rebuild", "--data-dir", str(data_dir)]) == 0
    assert list((data_dir / "records").rglob("*.jsonl")) == []


def test_missing_key_is_a_clear_error_and_rebuild_needs_none(
    fake_api, fake_clock, data_dir, monkeypatch, capsys
):
    assert _run(fake_api.transport, fake_clock, data_dir, "daily") == 0
    before = sorted(p.name for p in (data_dir / "records").rglob("*.jsonl"))
    capsys.readouterr()

    monkeypatch.delenv("ANTHROPIC_ANALYTICS_KEY")
    seen = len(fake_api.log)
    assert _run(fake_api.transport, fake_clock, data_dir, "daily", now=LATER) == 2
    assert "ANTHROPIC_ANALYTICS_KEY" in capsys.readouterr().err
    assert _run(fake_api.transport, fake_clock, data_dir, "backfill", "--since", "2026-09-01") == 2
    assert "ANTHROPIC_ANALYTICS_KEY" in capsys.readouterr().err
    assert len(fake_api.log) == seen

    assert _run(fake_api.transport, fake_clock, data_dir, "rebuild") == 0
    assert sorted(p.name for p in (data_dir / "records").rglob("*.jsonl")) == before


def test_key_never_reaches_disk_or_logs(fake_api, fake_clock, data_dir, fake_key_env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    assert _run(fake_api.transport, fake_clock, data_dir, "backfill", "--since", "2026-09-20") == 0
    assert _run(fake_api.transport, fake_clock, data_dir, "daily", now=LATER) == 0
    assert main(["rebuild", "--data-dir", str(data_dir)]) == 0

    needle = fake_key_env.encode("utf-8")
    scanned = 0
    for path in Path(data_dir).rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            data = gzip.decompress(data)
        assert needle not in data, path
        scanned += 1
    assert scanned > 5
    out = capsys.readouterr()
    assert fake_key_env not in caplog.text
    assert fake_key_env not in out.out
    assert fake_key_env not in out.err
    assert "@example.com" not in caplog.text


def test_parquet_next_to_jsonl(fake_api, fake_clock, data_dir):
    pytest.importorskip("pyarrow")
    assert _run(fake_api.transport, fake_clock, data_dir, "daily", "--parquet") == 0
    for jsonl in (data_dir / "records").rglob("*.jsonl"):
        assert jsonl.with_suffix(".parquet").is_file()
    # a rebuild keeps the parquet files that were there
    assert main(["rebuild", "--data-dir", str(data_dir)]) == 0
    for jsonl in (data_dir / "records").rglob("*.jsonl"):
        assert jsonl.with_suffix(".parquet").is_file()
