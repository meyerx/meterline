"""Done-check B6: the Python entry point ``run`` and the public fake ``meterline_anthropic.testing.FakeAPI``.

``run`` does what the command line does, with the key handed in (not read from the environment) and the
outcome returned as a ``RunResult``. Everything runs on the small synthetic sample the fake serves with no argument.
"""

import json
from datetime import date, datetime, timezone

import pytest

from meterline import RunResult
from meterline_anthropic import run
from meterline_anthropic.cli import main
from meterline_anthropic.testing import FakeAPI

KEY_ENV = "ANTHROPIC_ANALYTICS_KEY"
NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)
SINCE = date(2026, 9, 1)


def _files(data_dir):
    root = data_dir / "records"
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _manifest(result):
    return json.loads(result.manifest_path.read_text(encoding="utf-8"))


def _backfill(tmp_path, **kw):
    kw.setdefault("credentials", {KEY_ENV: "right"})
    fake = kw.pop("fake", None) or FakeAPI(api_key="right")
    return run("backfill", since=SINCE, data_dir=tmp_path, transport=fake.transport, now=lambda: NOW, **kw)


def test_run_and_main_publish_the_same_records(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "right")
    via_run, via_main = tmp_path / "a", tmp_path / "b"
    result = _backfill(via_run)
    code = main(
        ["backfill", "--since", "2026-09-01", "--data-dir", str(via_main)],
        transport=FakeAPI(api_key="right").transport, now=lambda: NOW,
    )
    assert isinstance(result, RunResult) and result.exit_code == code == 0 and result.ok
    assert _files(via_run) and _files(via_run) == _files(via_main)
    manifest = _manifest(result)
    assert result.batch_id == manifest["batch_id"]
    assert result.manifest_path == via_run / "runs" / (manifest["batch_id"] + ".json")
    assert result.records == manifest["published_counts"] and sum(result.records.values()) > 0
    assert result.windows == tuple(manifest["windows"])
    assert result.skipped == tuple(manifest.get("skipped_windows") or ())


def test_run_uses_the_credentials_given_and_never_reads_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    assert _backfill(tmp_path / "mapping").exit_code == 0
    assert _backfill(tmp_path / "callable", credentials=lambda: {KEY_ENV: "right"}).exit_code == 0
    monkeypatch.setenv(KEY_ENV, "wrong")
    assert _backfill(tmp_path / "ignored-env").exit_code == 0


def test_run_negative_controls(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(KEY_ENV, raising=False)
    wrong = _backfill(tmp_path / "wrong", credentials={KEY_ENV: "not-the-key"})
    assert wrong.exit_code == 3 and wrong.batch_id is None and wrong.records == {}
    capsys.readouterr()
    for credentials in (None, {}):
        assert _backfill(tmp_path / "none", credentials=credentials).exit_code == 2
        via_run = capsys.readouterr().err
        assert main(["backfill", "--since", "2026-09-01", "--data-dir", str(tmp_path / "cli")]) == 2
        assert KEY_ENV in via_run and via_run == capsys.readouterr().err


def test_run_daily_and_rebuild(tmp_path):
    assert _backfill(tmp_path).exit_code == 0
    fake = FakeAPI(api_key="right")
    daily = run("daily", data_dir=tmp_path, credentials={KEY_ENV: "right"}, transport=fake.transport,
                now=lambda: LATER)
    assert daily.exit_code == 0 and daily.batch_id and daily.manifest_path.is_file()
    before = _files(tmp_path)
    rebuilt = run("rebuild", data_dir=str(tmp_path))
    assert rebuilt.exit_code == 0 and isinstance(rebuilt, RunResult)
    assert _files(tmp_path) == before


def test_run_refuses_what_the_command_line_refuses(tmp_path, capsys):
    result = _backfill(tmp_path, rate_per_minute=0)
    assert result.exit_code == 2
    assert "--rate-per-minute must be at least 1" in capsys.readouterr().err


def test_run_unknown_option_is_a_type_error(tmp_path):
    with pytest.raises(TypeError, match="rate_per_second"):
        _backfill(tmp_path, rate_per_second=5)
