"""auto: ``meterline.run.auto_command`` reads the run manifests, never ``state.json``; ``run_auto`` and ``requested``.

Run: python3 -m pytest -q packages/meterline/tests/test_auto_command.py
"""
import json

from meterline.run import EXIT_CHECKS, auto_command, requested, requested_command, resolve_auto, run_auto


def _manifest(data, name, command, status):
    runs = data / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / (name + ".json")).write_text(json.dumps({"batch_id": name, "command": command, "status": status}), encoding="utf-8")


def test_an_empty_folder_backfills(tmp_path):
    assert auto_command(tmp_path / "missing") == "backfill"
    assert auto_command(tmp_path) == "backfill"


def test_a_published_backfill_turns_auto_to_daily(tmp_path):
    _manifest(tmp_path, "20260915T060000Z", "backfill", "published")
    assert auto_command(tmp_path) == "daily"


def test_a_backfill_that_stopped_half_way_is_backfilled_again(tmp_path):
    # what a backfill that stopped on a 503 leaves: a state file with a pending block, raw files, no manifest
    (tmp_path / "state.json").write_text(json.dumps({"days_loaded": [], "pending": {"command": "backfill"}}), encoding="utf-8")
    (tmp_path / "raw").mkdir()
    assert auto_command(tmp_path) == "backfill"


def test_a_failed_backfill_manifest_is_not_a_published_backfill(tmp_path):
    _manifest(tmp_path, "20260915T060000Z", "backfill", "failed")
    assert auto_command(tmp_path) == "backfill"


def test_a_published_daily_is_not_a_backfill(tmp_path):
    _manifest(tmp_path, "20260915T060000Z", "daily", "published")
    assert auto_command(tmp_path) == "backfill"


def test_an_unreadable_manifest_is_passed_over(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "20260914T060000Z.json").write_text("{cut sho", encoding="utf-8")
    assert auto_command(tmp_path) == "backfill"
    _manifest(tmp_path, "20260915T060000Z", "backfill", "published")
    assert auto_command(tmp_path) == "daily"


def test_resolve_auto_needs_since(tmp_path, capsys):
    assert resolve_auto(tmp_path, None) is None
    assert "auto needs --since" in capsys.readouterr().err
    assert resolve_auto(tmp_path, "2026-09-01") == "backfill"
    assert capsys.readouterr().err == ""


def test_resolve_auto_says_since_is_ignored_on_the_daily_path(tmp_path, capsys):
    _manifest(tmp_path, "20260915T060000Z", "backfill", "published")
    assert resolve_auto(tmp_path, "2026-09-01") == "daily"
    assert "--since is ignored" in capsys.readouterr().err


def test_run_auto_runs_the_chosen_command_marked_as_requested(tmp_path):
    seen = []

    def run_one(command, options):
        seen.append((command, dict(options), requested_command()))
        return 0

    assert run_auto(tmp_path, {"since": "2026-09-01", "proxy_name": "p"}, run_one) == 0
    _manifest(tmp_path, "20260915T060000Z", "backfill", "published")
    assert run_auto(tmp_path, {"since": "2026-09-01", "proxy_name": "p"}, run_one) == 0
    assert seen == [("backfill", {"since": "2026-09-01", "proxy_name": "p"}, "auto"), ("daily", {"proxy_name": "p"}, "auto")]
    assert requested_command() is None


def test_run_auto_without_since_runs_nothing_and_exits_2(tmp_path):
    seen = []
    assert run_auto(tmp_path, {"proxy_name": "p"}, lambda command, options: seen.append(command) or 0) == EXIT_CHECKS
    assert seen == []


def test_requested_is_none_outside_the_block_and_restored_after_an_error():
    assert requested_command() is None
    try:
        with requested("auto"):
            assert requested_command() == "auto"
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    assert requested_command() is None
