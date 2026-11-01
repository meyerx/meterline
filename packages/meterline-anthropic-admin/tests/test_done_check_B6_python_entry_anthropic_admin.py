"""B6: the Python entry point ``run`` gives what the command line gives, with the key passed in and the public fake."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from meterline_anthropic_admin import RunResult, run
from meterline_anthropic_admin.cli import main
from meterline_anthropic_admin.testing import FakeAPI

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
KEY_ENV = "ANTHROPIC_ADMIN_KEY"
RIGHT = "right"


def _now():
    return NOW


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted((root / "records").rglob("*")) if p.is_file()}


def _fake(key=RIGHT):
    return FakeAPI(api_key=key)


def _backfill(data_dir, fake, credentials, **kw):
    return run("backfill", since=date(2026, 9, 1), provider_account_id="my-org", data_dir=data_dir, credentials=credentials,
               transport=fake.transport, now=_now, **kw)


def test_B6_run_and_main_publish_the_same_records(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ENV, RIGHT)
    a, b = tmp_path / "a", tmp_path / "b"
    result = _backfill(a, _fake(), {KEY_ENV: RIGHT})
    code = main(["backfill", "--provider-account-id", "my-org", "--since", "2026-09-01", "--data-dir", str(b)], transport=_fake().transport, now=_now)
    assert isinstance(result, RunResult) and result.exit_code == 0 and code == 0
    files = _files(a)
    assert files and files == _files(b)
    manifest_path = next((a / "runs").glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.batch_id == manifest["batch_id"] and result.manifest_path == manifest_path
    assert result.records == manifest["published_counts"] and result.records
    assert result.windows == tuple(manifest["windows"]) and result.windows
    assert result.skipped == ()


def test_B6_credentials_are_used_and_the_environment_is_not_read(tmp_path, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    assert _backfill(tmp_path / "a", _fake(), {KEY_ENV: RIGHT}).exit_code == 0
    assert _backfill(tmp_path / "b", _fake(), lambda: {KEY_ENV: RIGHT}).exit_code == 0
    monkeypatch.setenv(KEY_ENV, "wrong")
    assert _backfill(tmp_path / "c", _fake(), {KEY_ENV: RIGHT}).exit_code == 0


def test_B6_negative_wrong_key_and_missing_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(KEY_ENV, raising=False)
    assert _backfill(tmp_path / "a", _fake(), {KEY_ENV: "wrong"}).exit_code == 3
    capsys.readouterr()
    assert _backfill(tmp_path / "b", _fake(), None).exit_code == 2
    via_run = capsys.readouterr().err
    code = main(["backfill", "--provider-account-id", "my-org", "--since", "2026-09-01", "--data-dir", str(tmp_path / "c")], transport=_fake().transport, now=_now)
    assert code == 2 and capsys.readouterr().err == via_run and KEY_ENV in via_run
    monkeypatch.setenv(KEY_ENV, RIGHT)
    assert _backfill(tmp_path / "d", _fake(), {}).exit_code == 2  # credentials given: the environment is not a fallback


def test_B6_daily_and_rebuild_return_a_result(tmp_path):
    fake = _fake()
    assert _backfill(tmp_path, fake, {KEY_ENV: RIGHT}).ok
    later = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    daily = run("daily", provider_account_id="my-org", data_dir=str(tmp_path), credentials={KEY_ENV: RIGHT}, transport=fake.transport, now=lambda: later)
    assert isinstance(daily, RunResult) and daily.exit_code == 0
    rebuilt = run("rebuild", data_dir=tmp_path)
    assert isinstance(rebuilt, RunResult) and rebuilt.exit_code == 0


def test_B6_an_unknown_option_raises_type_error(tmp_path):
    with pytest.raises(TypeError, match="no_such_option"):
        run("rebuild", data_dir=tmp_path, no_such_option=1)
