"""Done-check B6: the Python entry point ``run`` and the public fake ``meterline_copilot.testing.FakeAPI``.

``run`` does what the command line does and returns a ``RunResult``; credentials can be given to it, in which case
the environment is not read. The fake serves a small synthetic data set when it is given none.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from copilot_helpers import FAKE_BILLING_TOKEN, FAKE_TOKEN

from meterline_copilot import RunResult, run
from meterline_copilot.cli import main
from meterline_copilot.testing import FakeAPI

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
CREDENTIALS = {"COPILOT_TOKEN": FAKE_TOKEN, "COPILOT_BILLING_TOKEN": FAKE_BILLING_TOKEN}
HOOKS = {"clock": lambda: 0.0, "sleep": lambda s: None, "wall": lambda: 0.0, "jitter": lambda: 0}


def _fake(**kw):
    return FakeAPI(token=FAKE_TOKEN, billing_token=FAKE_BILLING_TOKEN, **kw)


def _backfill(data_dir, fake, credentials=CREDENTIALS, **kw):
    return run("backfill", since=date(2026, 9, 1), level="organization", slug="my-org", data_dir=data_dir,
               credentials=credentials, transport=fake.transport, now=lambda: NOW, **HOOKS, **kw)


def _files(data_dir):
    root = Path(data_dir) / "records"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_run_and_main_publish_the_same_records(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    result = _backfill(a, _fake())
    code = main(["backfill", "--level", "organization", "--slug", "my-org", "--since", "2026-09-01",
                 "--data-dir", str(b)], transport=_fake().transport, now=lambda: NOW, **HOOKS)
    assert isinstance(result, RunResult)
    assert result.exit_code == code == 0
    files = _files(a)
    assert files and files == _files(b)
    manifest_path = a / "runs" / (result.batch_id + ".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.manifest_path == manifest_path
    assert result.records == manifest["published_counts"] and sum(result.records.values()) > 0
    assert result.windows == tuple(manifest["windows"])
    assert result.skipped == tuple(manifest.get("skipped_windows") or manifest.get("skipped") or ())


def test_credentials_are_used_and_the_environment_is_not_read(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_TOKEN")
    monkeypatch.delenv("COPILOT_BILLING_TOKEN")
    fake = FakeAPI(token="right")
    assert _backfill(tmp_path / "d1", fake, {"COPILOT_TOKEN": "right"}).exit_code == 0
    assert _backfill(tmp_path / "d2", FakeAPI(token="right"), lambda: {"COPILOT_TOKEN": "right"}).exit_code == 0
    monkeypatch.setenv("COPILOT_TOKEN", "wrong")
    assert _backfill(tmp_path / "d3", FakeAPI(token="right"), {"COPILOT_TOKEN": "right"}).exit_code == 0


def test_a_callable_is_called_once_per_run(tmp_path):
    calls = []

    def credentials():
        calls.append(1)
        return {"COPILOT_TOKEN": "right"}

    assert _backfill(tmp_path / "d", FakeAPI(token="right"), credentials).exit_code == 0
    assert len(calls) == 1


def test_negative_controls(tmp_path, monkeypatch, capsys):
    result = _backfill(tmp_path / "d1", FakeAPI(token="right"), {"COPILOT_TOKEN": "wrong"})
    assert result.exit_code == 3 and result.failure_kind == "permanent" and not result.ok
    capsys.readouterr()
    monkeypatch.delenv("COPILOT_TOKEN")
    monkeypatch.delenv("COPILOT_BILLING_TOKEN")
    assert _backfill(tmp_path / "d2", FakeAPI(), None).exit_code == 2
    via_run = capsys.readouterr().err
    assert _backfill(tmp_path / "d3", FakeAPI(), {}).exit_code == 2
    assert capsys.readouterr().err == via_run
    code = main(["backfill", "--level", "organization", "--slug", "my-org", "--since", "2026-09-01",
                 "--data-dir", str(tmp_path / "d4")], transport=FakeAPI().transport, now=lambda: NOW, **HOOKS)
    assert code == 2 and capsys.readouterr().err == via_run
    assert "COPILOT_TOKEN must be set" in via_run


def test_daily_and_rebuild(tmp_path):
    data = tmp_path / "d"
    assert _backfill(data, _fake()).exit_code == 0
    later = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    daily = run("daily", level="organization", slug="my-org", data_dir=str(data), credentials=CREDENTIALS,
                transport=_fake().transport, now=lambda: later, **HOOKS)
    assert isinstance(daily, RunResult) and daily.exit_code == 0
    rebuilt = run("rebuild", data_dir=data)
    assert isinstance(rebuilt, RunResult) and rebuilt.exit_code == 0


def test_an_unknown_option_raises(tmp_path):
    with pytest.raises(TypeError, match="no_such_option"):
        run("rebuild", data_dir=tmp_path, no_such_option=1)
