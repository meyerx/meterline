"""Done-check B6: the Python entry point ``run`` does what the command line does, on the public fake API."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from meterline import RunResult
from meterline_litellm import run
from meterline_litellm.cli import main
from meterline_litellm.testing import FakeAPI

FAKE_KEY = "sk-synthetic-not-a-real-key"  # the key tests/conftest.py puts in the environment
BASE_URL = "https://proxy.example.com"

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
CREDS = {"LITELLM_API_KEY": FAKE_KEY, "LITELLM_BASE_URL": BASE_URL}


def _hooks(clock):
    return {"clock": clock, "sleep": clock.sleep, "jitter": lambda: 0}


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted((root / "records").rglob("*")) if p.is_file()}


def _backfill(data_dir, fake_clock, **kw):
    return run("backfill", since=date(2026, 9, 1), proxy_name="my-proxy", data_dir=data_dir, now=lambda: NOW,
               **_hooks(fake_clock), **kw)


def test_run_and_main_publish_the_same_records(tmp_path, fake_clock):
    a, b = tmp_path / "a", tmp_path / "b"
    result = _backfill(a, fake_clock, credentials=CREDS, transport=FakeAPI(clock=fake_clock).transport)
    code = main(["backfill", "--proxy-name", "my-proxy", "--since", "2026-09-01", "--data-dir", str(b)],
                transport=FakeAPI(clock=fake_clock).transport, now=lambda: NOW, **_hooks(fake_clock))
    assert isinstance(result, RunResult)
    assert result.exit_code == code == 0 and result.ok
    files = _files(a)
    assert files and files == _files(b)
    import json

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.batch_id == manifest["batch_id"]
    assert result.records == manifest["published_counts"] and sum(result.records.values()) > 0
    assert result.windows == tuple(manifest["windows"]) and result.windows
    assert result.skipped == tuple(manifest.get("skipped_windows") or manifest.get("skipped") or ())


def test_credentials_are_used_and_the_environment_is_not_read(tmp_path, fake_clock, monkeypatch):
    monkeypatch.delenv("LITELLM_API_KEY")
    monkeypatch.delenv("LITELLM_BASE_URL")
    fake = FakeAPI(api_key="right", clock=fake_clock)
    creds = {"LITELLM_API_KEY": "right", "LITELLM_BASE_URL": BASE_URL}
    assert _backfill(tmp_path / "1", fake_clock, credentials=creds, transport=fake.transport).exit_code == 0
    calls = []

    def provider():
        calls.append(1)
        return creds

    assert _backfill(tmp_path / "2", fake_clock, credentials=provider, transport=fake.transport).exit_code == 0
    assert len(calls) == 1
    monkeypatch.setenv("LITELLM_API_KEY", "wrong")
    monkeypatch.setenv("LITELLM_BASE_URL", "https://wrong.example.com")
    assert _backfill(tmp_path / "3", fake_clock, credentials=creds, transport=fake.transport).exit_code == 0


def test_wrong_key_and_missing_credentials_fail_like_the_command_line(tmp_path, fake_clock, monkeypatch, capsys):
    fake = FakeAPI(clock=fake_clock)
    bad = {"LITELLM_API_KEY": "wrong", "LITELLM_BASE_URL": BASE_URL}
    assert _backfill(tmp_path / "1", fake_clock, credentials=bad, transport=fake.transport).exit_code == 3
    capsys.readouterr()
    monkeypatch.delenv("LITELLM_API_KEY")
    monkeypatch.delenv("LITELLM_BASE_URL")
    result = _backfill(tmp_path / "2", fake_clock, transport=fake.transport)
    err_run = capsys.readouterr().err
    assert result.exit_code == 2
    code = main(["backfill", "--proxy-name", "my-proxy", "--since", "2026-09-01", "--data-dir", str(tmp_path / "3")],
                transport=fake.transport, now=lambda: NOW, **_hooks(fake_clock))
    assert code == 2 and err_run == capsys.readouterr().err != ""
    # credentials that is given replaces the environment: a name left out is unset
    monkeypatch.setenv("LITELLM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LITELLM_BASE_URL", BASE_URL)
    assert _backfill(tmp_path / "4", fake_clock, credentials={}, transport=fake.transport).exit_code == 2


def test_daily_and_rebuild_return_results(tmp_path, fake_clock):
    fake = FakeAPI(clock=fake_clock)
    assert _backfill(tmp_path, fake_clock, credentials=CREDS, transport=fake.transport).ok
    later = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    daily = run("daily", proxy_name="my-proxy", data_dir=str(tmp_path), credentials=CREDS, transport=fake.transport,
                now=lambda: later, **_hooks(fake_clock))
    assert isinstance(daily, RunResult) and daily.exit_code == 0
    rebuilt = run("rebuild", data_dir=tmp_path)
    assert isinstance(rebuilt, RunResult) and rebuilt.exit_code == 0


def test_unknown_option_raises_type_error(tmp_path):
    with pytest.raises(TypeError, match="no_such_option"):
        run("rebuild", data_dir=tmp_path, no_such_option=1)
