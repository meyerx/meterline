"""B6: the result a connector's ``run`` returns, built from the run manifest the command wrote."""

import json

import pytest

from meterline import RunResult, result_of_run, run_manifests


def _manifest(data_dir, batch_id, **fields):
    runs = data_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    body = {"batch_id": batch_id, "status": "published", "windows": ["2026-09-01/2026-09-14"],
            "published_counts": {"usage": 3, "seats": 1}, "skipped_windows": []}
    body.update(fields)
    (runs / (batch_id + ".json")).write_text(json.dumps(body), encoding="utf-8")


def test_no_manifest_gives_an_empty_result_with_the_exit_code(tmp_path):
    before = run_manifests(tmp_path)
    assert before == frozenset()
    result = result_of_run(tmp_path, 2, before)
    assert result == RunResult(exit_code=2)
    assert result.ok is False and result.batch_id is None and result.manifest_path is None
    assert result.records == {} and result.windows == () and result.skipped == ()


def test_the_manifest_written_by_this_run_fills_the_result(tmp_path):
    _manifest(tmp_path, "20260901T060000Z")
    before = run_manifests(tmp_path)
    _manifest(tmp_path, "20260915T060000Z", skipped_windows=["2026-08-01/2026-08-31"])
    result = result_of_run(tmp_path, 0, before)
    assert result.ok is True
    assert result.batch_id == "20260915T060000Z"
    assert result.manifest_path == tmp_path / "runs" / "20260915T060000Z.json"
    assert result.records == {"seats": 1, "usage": 3}
    assert result.windows == ("2026-09-01/2026-09-14",)
    assert result.skipped == ("2026-08-01/2026-08-31",)


def test_an_older_manifest_is_not_taken_for_this_run(tmp_path):
    # negative control: a run that wrote nothing must not report the manifest of an earlier run
    _manifest(tmp_path, "20260901T060000Z")
    before = run_manifests(tmp_path)
    assert result_of_run(tmp_path, 1, before) == RunResult(exit_code=1)


def test_a_failed_run_reports_no_records(tmp_path):
    before = run_manifests(tmp_path)
    _manifest(tmp_path, "20260915T060000Z", status="failed", published_counts={})
    result = result_of_run(tmp_path, 2, before)
    assert result.batch_id == "20260915T060000Z" and result.records == {} and result.ok is False


def test_the_result_is_immutable():
    with pytest.raises(AttributeError):
        RunResult(exit_code=0).exit_code = 1
