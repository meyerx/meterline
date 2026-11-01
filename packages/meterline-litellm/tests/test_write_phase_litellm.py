"""B1-d: a write-phase OSError (disk full, permission) must not escape ``run`` or leave a half-published batch.

The second ``write_jsonl`` of the run fails. The run exits 1 (transient, retry), the batch file the first write made is
removed, no manifest is written, the state file still says nothing is loaded, and the next run publishes the clean result.
"""

import errno
import json
from datetime import timedelta
from pathlib import Path

from litellm_helpers import NOW, backfill, basic_rows, users
from meterline_litellm import cli as cli_module

def _fail_second_write(monkeypatch):
    real = cli_module.write_jsonl
    calls = []

    def flaky(records, path):
        calls.append(Path(path))
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(records, path)

    monkeypatch.setattr(cli_module, "write_jsonl", flaky)
    return calls, real


def _files(root, pattern):
    return sorted(p.relative_to(root).as_posix() for p in Path(root).rglob(pattern)) if Path(root).exists() else []


def test_write_phase_oserror_litellm(make_proxy, fake_clock, data_dir, tmp_path, monkeypatch, capsys):
    api = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    calls, real = _fail_second_write(monkeypatch)
    code = backfill(data_dir, api, fake_clock)
    err = capsys.readouterr().err
    assert code == 1, err
    assert len(calls) == 2
    assert "Traceback" not in err and "writing" in err and "nothing was published" in err
    assert _files(data_dir / "records", "*.jsonl") == [] and _files(data_dir / "records", "*.tmp") == []
    assert _files(data_dir / "runs", "*.json") == [] and _files(data_dir / "runs", "*.tmp") == []
    state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    assert state["days_loaded"] == []
    monkeypatch.setattr(cli_module, "write_jsonl", real)
    assert backfill(data_dir, api, fake_clock, now=NOW + timedelta(hours=1)) == 0, capsys.readouterr().err
    clean = backfill(tmp_path / "clean", api, fake_clock, now=NOW + timedelta(hours=1))
    assert clean == 0
    assert _published(data_dir) == _published(tmp_path / "clean")
    assert _files(data_dir / "records", "*.jsonl")


def _published(root):
    found = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(root) / "runs").glob("*.json"))]
    return [(m["status"], m["published_counts"]) for m in found]
