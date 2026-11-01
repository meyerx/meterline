"""Negative controls of the conformance kit: a connector with a planted defect must fail the kit, naming the rule."""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from meterline.conformance import ConformanceError, check
from test_conformance_litellm import CONNECTOR

REAL_RUN = CONNECTOR.run


def _with_float_in_money(command, **kw):
    """The defect: a money amount written as a JSON float instead of a decimal string."""
    result = REAL_RUN(command, **kw)
    for path in sorted(Path(kw["data_dir"]).glob("records/usage/*.jsonl"))[:1]:
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["cost"] = float(record["cost"]) + 0.1  # a string like "1.95433725" becomes a float
        lines[0] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def _state_written_before_publish(command, **kw):
    """The defect: the days are marked loaded in state.json although the run failed before it published."""
    result = REAL_RUN(command, **kw)
    state_path = Path(kw["data_dir"]) / "state.json"
    if result.exit_code != 0 and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["days_loaded"] = ["2026-09-01", "2026-09-02"]
        state_path.write_text(json.dumps(state), encoding="utf-8")
    return result


def test_a_float_in_money_fails_the_kit_naming_the_rule(tmp_path):
    broken = replace(CONNECTOR, run=_with_float_in_money)
    with pytest.raises(ConformanceError, match=r"contract rule R02 .*float") as caught:
        check("R02", broken, tmp_path)
    assert "docs/reference/connector-contract.md#r02" in str(caught.value)


def test_state_written_before_publish_fails_the_kit_naming_the_rule(tmp_path):
    broken = replace(CONNECTOR, run=_state_written_before_publish)
    with pytest.raises(ConformanceError, match=r"contract rule R10 .*state is written after the records"):
        check("R10", broken, tmp_path)


def test_the_unbroken_connector_passes_the_same_two_rules(tmp_path):
    # the control of the controls: the defect is what fails the rule, not the harness
    check("R02", CONNECTOR, tmp_path / "a")
    check("R10", CONNECTOR, tmp_path / "b")
    assert re.fullmatch(r"R\d\d", "R02")
