"""Phase 3 done-check G07: fixture scenarios, alerts and named exceptions (spec 14, with the v1.1 manifest item)."""

import json

from gold_helpers import (
    EXPECTED, KEY, build_cli, compare_with_control, copy_fixture_input, edit_policy, read_dicts,
)

CENTRAL_FILES = ("scenario_persons", "scenario_units", "scenario_summary", "alert_events", "named_exceptions")
SIX_KEYS = ["central-it", "commercial", "finance-legal", "operations", "people", "product"]


def test_done_check_G07_fixture_central_files_are_byte_identical(fixture_out):
    for name in CENTRAL_FILES:
        compare_with_control(fixture_out / "central" / (name + ".csv"), EXPECTED / "central" / (name + ".csv"),
                             "central " + name)


def test_done_check_G07_fixture_exec_scenario_summary_is_byte_identical(fixture_out):
    compare_with_control(fixture_out / "exec" / "scenario_summary.csv", EXPECTED / "exec" / "scenario_summary.csv",
                         "exec scenario_summary")


def test_done_check_G07_fixture_manager_named_exceptions_are_byte_identical(fixture_out):
    managers = sorted(p.name for p in (EXPECTED / "manager").iterdir() if p.is_dir())
    assert managers
    for key in managers:
        compare_with_control(
            fixture_out / "manager" / key / "named_exceptions.csv",
            EXPECTED / "manager" / key / "named_exceptions.csv", "manager named_exceptions " + key,
        )


def test_done_check_G07_fixture_manifest_has_no_unknown_envelope_keys(fixture_out):
    manifest = json.loads((fixture_out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["unknown_envelope_keys"] == {}


def test_done_check_G07_tiny_sim_lists_unknown_envelope_keys_and_exits_0(
    sim_runs, example_files, tmp_path, monkeypatch, capsys
):
    run = sim_runs["tiny"]
    inp = {"silver": run.silver, "org": run.org, "policy": example_files[0], "prices": example_files[1],
           "invoice": None}
    out = tmp_path / "gold"
    assert build_cli(inp, out, monkeypatch) == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["unknown_envelope_keys"] == {"unit_caps_plus_flat": SIX_KEYS}
    rows = read_dicts(out / "central" / "scenario_units.csv")
    assert all(r["scenario_id"] != "unit_caps_plus_flat" for r in rows)
    # one warning line per scenario with unknown keys, on stderr, naming the scenario and its keys
    err = [line for line in capsys.readouterr().err.splitlines() if "unit_caps_plus_flat" in line]
    assert len(err) == 1
    assert all(key in err[0] for key in SIX_KEYS)


def test_done_check_G07_unknown_envelope_key_gamma_is_listed_and_exits_0(tmp_path, monkeypatch):
    inp = copy_fixture_input(tmp_path / "input")

    def add_gamma(policy):
        for scenario in policy["scenarios"]:
            if scenario["id"] == "unit_caps_plus_flat":
                scenario["unit_envelopes"]["values"]["gamma"] = "45.67"

    edit_policy(inp["policy"], add_gamma)
    out = tmp_path / "gold"
    assert build_cli(inp, out, monkeypatch) == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["unknown_envelope_keys"] == {"unit_caps_plus_flat": ["gamma"]}
    # the known units are still subjects
    units = read_dicts(out / "central" / "scenario_units.csv")
    assert sorted(r["unit_id"] for r in units if r["scenario_id"] == "unit_caps_plus_flat") == ["u02", "u03"]
