"""B: exit codes 0-3, ``failure_kind`` and ``http_status`` in the core package."""

import json

import pytest

from meterline import RunResult, note_failure, result_of_run, run_manifests
from meterline.http import failure_kind_of_status
from meterline.run import EXIT_CHECKS, EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT, FAILURE_KINDS, exit_code_of_status


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_a_refusal_is_permanent(status):
    assert failure_kind_of_status(status) == "permanent"
    assert exit_code_of_status(status) == EXIT_PERMANENT == 3


@pytest.mark.parametrize("status", [0, 200, 408, 429, 500, 502, 503, 504])
def test_a_transport_failure_or_a_retryable_status_is_transient(status):
    assert failure_kind_of_status(status) == "transient"
    assert exit_code_of_status(status) == EXIT_TRANSIENT == 1


def test_the_exit_codes_and_their_kinds():
    assert (EXIT_OK, EXIT_TRANSIENT, EXIT_CHECKS, EXIT_PERMANENT) == (0, 1, 2, 3)
    assert FAILURE_KINDS == ("none", "transient", "input", "permanent")
    kinds = [RunResult(exit_code=code).failure_kind for code in range(4)]
    assert kinds == list(FAILURE_KINDS)


def test_a_run_without_a_manifest_takes_its_kind_from_the_exit_code(tmp_path):
    before = run_manifests(tmp_path)
    note_failure(401)
    result = result_of_run(tmp_path, 3, before)
    assert (result.failure_kind, result.http_status, result.ok) == ("permanent", 401, False)


def test_a_noted_status_is_used_once(tmp_path):
    # negative control: the status of one failed run must not leak into the next run's result
    note_failure(503)
    assert result_of_run(tmp_path, 1, run_manifests(tmp_path)).http_status == 503
    assert result_of_run(tmp_path, 1, run_manifests(tmp_path)).http_status is None


def test_the_manifest_status_wins_and_a_successful_run_has_none(tmp_path):
    before = run_manifests(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    body = {"batch_id": "20260915T060000Z", "status": "published", "failure_kind": "none", "published_counts": {"usage": 1}}
    (runs / "20260915T060000Z.json").write_text(json.dumps(body), encoding="utf-8")
    result = result_of_run(tmp_path, 0, before)
    assert (result.failure_kind, result.http_status) == ("none", None)


def test_the_fake_clock_moves_when_it_sleeps_and_records_what_it_slept():
    from meterline.http import FakeClock

    clock = FakeClock()
    start = clock()
    clock.sleep(5)
    clock.sleep(0)
    clock.sleep(-3)  # a negative wait is no wait
    assert clock() - start == 5 and clock.sleeps == [5, 0, -3]
    clock.advance(10)
    assert clock() - start == 15
