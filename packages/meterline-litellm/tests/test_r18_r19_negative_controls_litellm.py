"""Negative controls of rules R18 (auto) and R19 (complete_through): each planted defect the spec names fails the kit,
naming the rule. The connector is the smallest one; the defects sit in the core helpers every connector calls, or in
the connector's own complete_through.
"""
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import meterline.run as core
from meterline.conformance import ConformanceError, check
from test_conformance_litellm import CONNECTOR


def _reads_state_json(data_dir):
    return "daily" if (Path(data_dir) / "state.json").exists() else "backfill"  # the defect: state.json says a run started


def _any_published_manifest(data_dir):
    return "daily" if any(True for _ in core._published_manifests(data_dir)) else "backfill"  # the defect: a daily counts


def _last_day_served(data_dir):
    state = Path(data_dir) / "state.json"
    if not state.is_file():
        return None
    days = json.loads(state.read_text(encoding="utf-8"))["days_loaded"]
    return date.fromisoformat(max(days)) if days else None  # the defect: the last day loaded, final or not


@pytest.mark.parametrize("defect", [_reads_state_json, _any_published_manifest], ids=lambda f: f.__name__.strip("_"))
def test_a_planted_auto_defect_fails_r18(defect, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "auto_command", defect)
    with pytest.raises(ConformanceError, match=r"contract rule R18 .*not backfill") as caught:
        check("R18", CONNECTOR, tmp_path)
    assert "docs/reference/connector-contract.md#r18" in str(caught.value)


def test_complete_through_returning_the_last_day_served_fails_r19(tmp_path):
    with pytest.raises(ConformanceError, match=r"contract rule R19 .*the last day the fake marks final") as caught:
        check("R19", replace(CONNECTOR, complete_through=_last_day_served), tmp_path)
    assert "docs/reference/connector-contract.md#r19" in str(caught.value)


def test_a_connector_without_complete_through_fails_r19(tmp_path):
    with pytest.raises(ConformanceError, match=r"contract rule R19 .*must set Connector.complete_through"):
        check("R19", replace(CONNECTOR, complete_through=None), tmp_path)
