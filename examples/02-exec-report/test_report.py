"""The exec report is written readable by its owner only (0600), whatever the caller's umask. Synthetic figures only."""
import importlib.util
import os
import re
import stat
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("exec_report", HERE / "report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def make_gold(root):
    exec_dir = root / "exec"
    exec_dir.mkdir(parents=True)
    (exec_dir / "costs_month.csv").write_text(
        "month,provider,usage_cost_ref,seat_cost,total_cost_ref\n2026-09,anthropic,12.50,,12.50\n", encoding="utf-8")
    (exec_dir / "seats_month.csv").write_text(
        "month,provider,seats_billed,active_seats,idle_seats\n2026-09,github_copilot,10,8,2\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("umask", [0o022, 0o000], ids=["umask022", "umask000"])
@pytest.mark.parametrize("existing", [False, True])
def test_output_private_example_02(tmp_path, umask, existing):
    gold = make_gold(tmp_path / "gold")
    out = tmp_path / "report.html"
    if existing:  # a page left by an earlier run, or made by the operator, with loose permissions
        out.write_text("old", encoding="utf-8")
        os.chmod(out, 0o644)
    old = os.umask(umask)
    try:
        assert report.main([str(gold), "--out", str(out)]) == 0
        assert os.umask(umask) == umask, "the umask was left changed after the run"
    finally:
        os.umask(old)
    assert "Exec report" in out.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def page_for(tmp_path, costs, seats):
    gold = tmp_path / "gold"
    (gold / "exec").mkdir(parents=True)
    (gold / "exec" / "costs_month.csv").write_text(costs, encoding="utf-8")
    (gold / "exec" / "seats_month.csv").write_text(seats, encoding="utf-8")
    return report.build(gold)


COSTS = "month,provider,usage_cost_ref,seat_cost,total_cost_ref,hidden\n"
SEATS = "month,provider,seats_billed,active_seats,idle_seats,hidden\n"


def test_an_empty_cell_is_shown_as_withheld_only_on_a_row_whose_hidden_flag_is_set(tmp_path):
    page = page_for(tmp_path, COSTS + "2026-09,a,,40.00,,true\n2026-09,b,12.50,,12.50,false\n", SEATS + "2026-09,a,9,,,true\n")
    rows = re.findall(r"<tr>(.*?)</tr>", page)
    hidden_costs = next(r for r in rows if "<td>a</td>" in r and "40.00" in r)
    shown_costs = next(r for r in rows if "<td>b</td>" in r and "12.50" in r)
    assert hidden_costs.count("withheld") == 2, hidden_costs
    assert "withheld" not in shown_costs and "<td>none</td>" in shown_costs, shown_costs


def test_a_row_without_a_hidden_column_keeps_saying_withheld_because_it_cannot_tell(tmp_path):
    # a gold folder of schema 1.0 has no hidden column: an empty cell may be a withheld value, so it stays "withheld"
    page = page_for(tmp_path, "month,provider,usage_cost_ref,seat_cost,total_cost_ref\n2026-09,a,12.50,,12.50\n",
                    "month,provider,seats_billed,active_seats,idle_seats\n2026-09,a,9,,\n")
    assert "<td>withheld</td>" in page and "<td>none</td>" not in page
