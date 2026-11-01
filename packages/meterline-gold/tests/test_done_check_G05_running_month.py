"""G05: a running month projects linearly (spec 6.4, Appendix A.7)."""

import pytest

from gold_helpers import KEY, filter_silver, fixture_inputs, person_key, read_dicts

P03 = person_key("p03")
P18 = person_key("p18")


def _running_month(tmp_path, last_day):
    import meterline_gold

    inp = fixture_inputs()
    silver = filter_silver(inp["silver"], tmp_path / ("silver-" + last_day), last_day)
    out = tmp_path / ("gold-" + last_day)
    meterline_gold.build(
        silver=silver, org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY, out=out,
    )
    return {r["person_key"]: r for r in read_dicts(out / "central" / "person_month.csv")}


def _check_projection(rows):
    assert {r["month_status"] for r in rows.values()} == {"running"}
    assert rows[P03]["projected_cost_ref"] == "1.84"
    assert rows[P18]["projected_cost_ref"] == "13.43"
    # the joiner (2026-05-18) has no employed day inside the window, so no row
    assert person_key("p21") not in rows


def test_done_check_G05_running_month_projection(tmp_path):
    _check_projection(_running_month(tmp_path, "2026-05-15"))
    # negative control (see `test_done_check_stronger_controls.py`): the same assertions on a build whose data runs to 2026-05-14 fail on the
    # projection (this cut-off projects 1.97 and 11.96), and on nothing else: the month is still running and the
    # joiner still has no row
    rows = _running_month(tmp_path, "2026-05-14")
    assert {r["month_status"] for r in rows.values()} == {"running"}
    assert person_key("p21") not in rows
    assert rows[P03]["projected_cost_ref"] == "1.97" and rows[P18]["projected_cost_ref"] == "11.96"
    with pytest.raises(AssertionError):
        _check_projection(rows)
