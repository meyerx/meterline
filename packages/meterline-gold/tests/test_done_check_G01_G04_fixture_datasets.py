"""G01-G04: the fixture build against the hand-computed expected files (spec 14, Appendix A)."""

import pytest

from gold_helpers import EXPECTED, compare_with_control


def test_done_check_G01_person_day(fixture_out):
    compare_with_control(
        fixture_out / "central" / "person_day.csv", EXPECTED / "central" / "person_day.csv", "G01 person_day"
    )


@pytest.mark.parametrize("name", ["person_month", "person_day_activity"])
def test_done_check_G02_person_month_and_activity(fixture_out, name):
    compare_with_control(
        fixture_out / "central" / (name + ".csv"), EXPECTED / "central" / (name + ".csv"), "G02 " + name
    )


@pytest.mark.parametrize("audience", ["central", "exec"])
@pytest.mark.parametrize("name", ["seats_month", "pools_month", "costs_month"])
def test_done_check_G03_seats_pools_costs(fixture_out, audience, name):
    compare_with_control(
        fixture_out / audience / (name + ".csv"),
        EXPECTED / audience / (name + ".csv"),
        "G03 %s/%s" % (audience, name),
    )


@pytest.mark.parametrize("name", ["reconciliation_month", "data_quality"])
def test_done_check_G04_reconciliation_and_data_quality(fixture_out, name):
    compare_with_control(
        fixture_out / "central" / (name + ".csv"), EXPECTED / "central" / (name + ".csv"), "G04 " + name
    )
