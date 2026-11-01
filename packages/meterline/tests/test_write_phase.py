"""B1-d: ``WritePhase`` makes the write phase of a run all or nothing. The four connectors use it; their own tests
(``test_write_phase_oserror_<connector>``) fail the second record write of a real run."""

import errno

import pytest

from meterline.run import EXIT_TRANSIENT, WritePhase


def _batch(root, batch_id):
    files = [root / "records" / "usage" / (batch_id + ".jsonl"), root / "records" / "org_totals" / (batch_id + ".jsonl.tmp"),
             root / "records" / "usage" / (batch_id + ".parquet"), root / "runs" / (batch_id + ".json")]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return files


def test_oserror_removes_this_batch_only_and_is_swallowed(tmp_path, capsys):
    mine = _batch(tmp_path, "20261001T060000Z")
    other = _batch(tmp_path, "20260930T060000Z")
    state = tmp_path / "state.json"
    state.write_text('{"days_loaded": []}', encoding="utf-8")
    with WritePhase(tmp_path, "20261001T060000Z") as phase:
        raise OSError(errno.ENOSPC, "No space left on device")
    assert phase.failed
    assert not any(p.exists() for p in mine)
    assert all(p.exists() for p in other)
    assert state.read_text(encoding="utf-8") == '{"days_loaded": []}'
    err = capsys.readouterr().err
    assert "No space left on device" in err and "nothing was published" in err and "Traceback" not in err
    assert EXIT_TRANSIENT == 1


def test_other_exceptions_pass_through_and_files_stay(tmp_path):
    mine = _batch(tmp_path, "20261001T060000Z")
    with pytest.raises(ValueError), WritePhase(tmp_path, "20261001T060000Z") as phase:
        raise ValueError("a bug is not a write failure")
    assert not phase.failed
    assert all(p.exists() for p in mine)


def test_no_error_leaves_everything_and_does_not_fail(tmp_path):
    mine = _batch(tmp_path, "20261001T060000Z")
    with WritePhase(tmp_path, "20261001T060000Z") as phase:
        pass
    assert not phase.failed and all(p.exists() for p in mine)
