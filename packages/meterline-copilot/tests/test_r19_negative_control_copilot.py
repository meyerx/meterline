"""Negative control of rule R19 for meterline-copilot: a complete_through that applies the usage rule of a day and forgets that the
billing totals of the day stay open until the month is final fails the kit, naming the rule."""
from dataclasses import replace

import pytest

from meterline.conformance import ConformanceError, check
from meterline.run import final_through
from meterline_copilot.cli import _read_state
from meterline_copilot.normalise import is_final_day
from test_conformance_copilot import CONNECTOR


def _usage_rule_only(data_dir):
    state = _read_state(data_dir)
    if state is None:
        return None
    from datetime import date
    return final_through(data_dir, (date.fromisoformat(d) for d in state["days_loaded"]), is_final_day)


def test_forgetting_the_month_of_the_billing_totals_fails_r19(tmp_path):
    with pytest.raises(ConformanceError, match=r"contract rule R19 .*record of the backfill that is not final ends on"):
        check("R19", replace(CONNECTOR, complete_through=_usage_rule_only, is_final=is_final_day), tmp_path)


def test_the_real_connector_passes_r19(tmp_path):
    check("R19", CONNECTOR, tmp_path)
