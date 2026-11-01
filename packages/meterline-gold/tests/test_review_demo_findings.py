"""Findings of the first end-to-end demo (2026-10-15): change-list findings 39 to 44.

Builds: the hand-built fixture (``fixture_out``) and the simulator's ``tiny`` and ``vertical`` presets (seed 7, 6
months) with the shipped example policy and prices. Everything is synthetic. Each section names the finding of
docs/dev/change-list-meterline-gold.md that records the change it checks.
"""

import csv
import json
import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from gold_helpers import KEY, read_dicts

PAGE = Path(__file__).resolve().parents[1] / "src" / "meterline_gold" / "dashboard" / "index.html"


@pytest.fixture(scope="module")
def tiny_gold(example_files, tmp_path_factory):
    """The tiny sim (seed 7, 6 months) built with the shipped example policy and prices; only this preset is made."""
    import meterline_gold
    meterline_sim = pytest.importorskip("meterline_sim")

    policy, prices = example_files
    sim = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("demo_sim"))
    out = tmp_path_factory.mktemp("demo_gold") / "gold"
    meterline_gold.build(silver=sim, org=Path(sim) / "org", policy=policy, prices=prices, key=KEY, out=out)
    return out


def _central(out, name):
    return read_dicts(Path(out) / "central" / (name + ".csv"))


# ---- finding 41: unallocated_share has one meaning per name ---------------------------------------------------------


def test_f41_reconciliation_month_calls_its_share_unreconciled(fixture_out, tiny_gold):
    """6.8 measures the org total minus the actor cost; 6.9 measures unresolved plus non-person cost. Two measures,
    two names: the reconciliation column is ``unreconciled_share`` and ``unallocated_share`` stays the 6.9 metric."""
    for out in (fixture_out, tiny_gold):
        with open(Path(out) / "central" / "reconciliation_month.csv", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        assert "unreconciled_share" in header
        assert "unallocated_share" not in header
        assert header.index("unreconciled_share") == header.index("unallocated_cost_source") + 1
        metrics = {r["metric"] for r in _central(out, "data_quality")}
        assert "unallocated_share" in metrics
        assert "unreconciled_share" not in metrics


def test_f41_the_renamed_column_keeps_its_values(fixture_out):
    """The fixture's anthropic row: 0.50 of 62.99 is 0.0079 (spec A.6); the 6.9 metric of the same row is 0.0043."""
    row = next(r for r in _central(fixture_out, "reconciliation_month") if r["provider"] == "anthropic")
    assert row["unreconciled_share"] == "0.0079"
    metric = next(r for r in _central(fixture_out, "data_quality")
                  if r["provider"] == "anthropic" and r["metric"] == "unallocated_share")
    assert metric["value"] == "0.0043"


# ---- finding 40: costs_month says what its figure is, next to the figure --------------------------------------------

# the words next to a figure for each cost basis (and for a mix of bases); a row with no usage figure has none
BASIS_LABEL = {
    "billed": "billed amount, as invoiced",
    "consumption": "usage value, not the invoice",
    "estimate": "estimate, not the invoice",
    "mixed": "mix of billed, usage-value and estimated amounts",
}
COSTS_KEY = ("month", "provider", "provider_account_id")


def _key(row):
    return tuple(row[c] for c in COSTS_KEY)


def _check_costs_rows(costs, reconciliation, problems, where):
    recon = {_key(r): r for r in reconciliation}
    for row in costs:
        if "cost_basis_label" not in row or "invoice_usage" not in row:
            problems.append("%s: costs_month has no cost_basis_label or invoice_usage column" % where)
            return
        basis = row["cost_source_basis"]
        want = BASIS_LABEL.get(basis, "")
        if row.get("hidden") != "true" and row["cost_basis_label"] != want:
            problems.append("%s %s: label %r for basis %r, wanted %r" % (where, _key(row), row["cost_basis_label"], basis, want))
        invoiced = recon[_key(row)]["invoice_usage"] if _key(row) in recon else ""
        if row.get("hidden") != "true" and row["invoice_usage"] != invoiced:
            problems.append("%s %s: invoice_usage %r, reconciliation_month has %r" % (where, _key(row), row["invoice_usage"], invoiced))


def test_f40_central_costs_month_labels_its_basis_and_shows_the_invoice(fixture_out):
    """The fixture has consumption (anthropic, github_copilot) and estimate (litellm) rows, and an invoice for the
    first two: a consumption figure sits next to its words and next to the invoiced usage amount."""
    problems = []
    costs = _central(fixture_out, "costs_month")
    _check_costs_rows(costs, _central(fixture_out, "reconciliation_month"), problems, "central")
    assert problems == []
    by_provider = {r["provider"]: r for r in costs}
    assert by_provider["github_copilot"]["cost_basis_label"] == "usage value, not the invoice"
    assert by_provider["github_copilot"]["invoice_usage"] == "1.15"
    assert by_provider["github_copilot"]["usage_cost_ref"] == "4.80"
    assert by_provider["litellm"]["cost_basis_label"] == "estimate, not the invoice"
    assert by_provider["litellm"]["invoice_usage"] == ""


def test_f40_exec_costs_month_labels_its_basis_and_hides_the_label_with_the_figure(fixture_out):
    exec_costs = read_dicts(Path(fixture_out) / "exec" / "costs_month.csv")
    problems = []
    _check_costs_rows(exec_costs, _central(fixture_out, "reconciliation_month"), problems, "exec")
    assert problems == []
    assert any(r["hidden"] == "true" for r in exec_costs) and any(r["hidden"] == "false" for r in exec_costs)
    for row in exec_costs:
        if row["hidden"] == "true":
            # the label and the invoice follow the figure: nothing next to a withheld figure says what it was
            assert row["cost_basis_label"] == "" and row["invoice_usage"] == "", row


def test_f40_a_billed_figure_is_labelled_as_the_invoice(fixture_inputs_billed, tmp_path):
    """The other basis: with the anthropic usage records on the ``billed`` basis the label says so."""
    import meterline_gold

    inp = fixture_inputs_billed
    out = tmp_path / "gold"
    meterline_gold.build(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                         invoice=inp["invoice"], key=KEY, out=out)
    costs = _central(out, "costs_month")
    problems = []
    _check_costs_rows(costs, _central(out, "reconciliation_month"), problems, "billed")
    assert problems == []
    anthropic = next(r for r in costs if r["provider"] == "anthropic")
    assert anthropic["cost_source_basis"] == "billed"
    assert anthropic["cost_basis_label"] == "billed amount, as invoiced"
    assert anthropic["invoice_usage"] == "63.50"


@pytest.fixture()
def fixture_inputs_billed(tmp_path):
    """The fixture inputs with every anthropic usage record moved from the consumption to the billed basis."""
    import shutil

    from gold_helpers import fixture_inputs

    inp = fixture_inputs()
    silver = tmp_path / "silver"
    shutil.copytree(inp["silver"], silver)
    changed = 0
    for path in sorted((silver / "records" / "usage").glob("*.jsonl")):
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if '"provider": "anthropic"' in line and '"cost_basis": "consumption"' in line:
                line = line.replace('"cost_basis": "consumption"', '"cost_basis": "billed"')
                changed += 1
            lines.append(line)
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    assert changed, "no anthropic consumption record found to move to billed"
    return dict(inp, silver=silver)

# ---- finding 39: outside central a hidden cell carries the marker, never the reason ---------------------------------

REASON_WORDS = ("k", "differencing", "secondary")


def _groups_files(out):
    out = Path(out)
    files = [out / "exec" / "groups_month.csv"] + sorted((out / "manager").glob("*/groups_month.csv"))
    return [(p.relative_to(out).as_posix(), read_dicts(p), _header(p)) for p in files]


def _header(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


@pytest.mark.parametrize("which", ["fixture", "tiny"])
def test_f39_groups_month_outside_central_has_the_marker_and_no_reason(which, fixture_out, tiny_gold):
    out = fixture_out if which == "fixture" else tiny_gold
    central = {(r["month"], r["cell_id"]): r for r in _central(out, "groups_month")}
    files = _groups_files(out)
    assert files, "no groups_month outside central"
    seen = set()
    for rel, rows, header in files:
        assert header[-1] == "hidden", (rel, header)
        assert "suppression_reason" not in header and "suppressed" not in header, (rel, header)
        for row in rows:
            assert row["hidden"] in ("true", "false"), (rel, row["hidden"])
            seen.add(row["hidden"])
            twin = central[(row["month"], row["cell_id"])]
            # the same state as central (A07), shown without the case
            assert (row["hidden"] == "true") == (twin["suppressed"] == "true"), (rel, row["cell_id"])
            if row["hidden"] == "true":
                for column in ("persons", "active_persons", "active_days", "cost_ref", "p50_cost_ref", "p75_cost_ref",
                               "p90_cost_ref", "p99_cost_ref", "top_decile_share", "is_final"):
                    assert row[column] == "", (rel, row["cell_id"], column)
            for column, value in row.items():
                assert value not in REASON_WORDS, (rel, column, value)
    if which == "fixture":
        assert seen == {"true", "false"}


def test_f39_central_groups_month_keeps_the_reason(fixture_out):
    rows = _central(fixture_out, "groups_month")
    header = _header(Path(fixture_out) / "central" / "groups_month.csv")
    assert "suppressed" in header and "suppression_reason" in header and "hidden" not in header
    reasons = {r["suppression_reason"] for r in rows if r["suppressed"] == "true"}
    assert reasons and reasons <= set(REASON_WORDS)


# ---- finding 42: the dashboard opens on a cost chart, has a month selector, keeps its first columns in view -----------


def _script():
    return re.search(r"<script>(.*?)</script>", PAGE.read_text(encoding="utf-8"), re.S).group(1)


def _style():
    return re.search(r"<style>(.*?)</style>", PAGE.read_text(encoding="utf-8"), re.S).group(1)


def _chart_defaults_js():
    """The pure functions that pick the first chart (between the two marker comments of the script)."""
    match = re.search(r"// chart defaults: begin\n(.*?)// chart defaults: end", _script(), re.S)
    assert match, "the script has no 'chart defaults' block"
    return match.group(1)


def test_f42_the_chart_never_defaults_to_schema_version_and_prefers_cost():
    js = _chart_defaults_js()
    assert re.search(r'CHART_SKIP\s*=\s*\[[^\]]*"schema_version"', js)
    assert "function defaultChartColumn(" in js and "function defaultLabelColumn(" in js
    assert re.search(r"defaultChartColumn\(", _script().replace(js, "")), "showFile does not call defaultChartColumn"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed; the source checks above still run")
def test_f42_the_default_chart_column_and_label_by_file():
    """Run the two functions on the headers of the files an audience opens on."""
    cases = {
        "exec groups_month": (["schema_version", "month", "cell_id", "cell_kind", "cell_key", "depth", "parent_cell_id",
                               "persons", "cost_ref", "p50_cost_ref", "hidden"], ("cost_ref", "cell_key")),
        "exec costs_month": (["schema_version", "month", "provider", "provider_account_id", "usage_cost_ref", "seat_cost",
                              "total_cost_ref"], ("total_cost_ref", "provider")),
        "central seats_month": (["schema_version", "month", "provider", "provider_account_id", "seats_billed", "seat_cost"],
                                ("seat_cost", "provider")),
        "scenario_summary": (["schema_version", "scenario_id", "month", "persons", "persons_stopped"],
                             ("persons", "scenario_id")),
    }
    for label, (header, (want_col, want_label)) in cases.items():
        numeric = [h for h in header if h not in ("schema_version", "depth", "month", "cell_id", "cell_kind", "cell_key",
                                                  "provider", "provider_account_id", "parent_cell_id", "hidden", "scenario_id")]
        code = _chart_defaults_js() + "\nconsole.log(JSON.stringify([defaultChartColumn(%s), defaultLabelColumn(%s)]));" % (
            json.dumps(numeric), json.dumps(header))
        done = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == [want_col, want_label], label


def test_f42_a_month_selector_filters_the_shown_rows_and_opens_on_the_latest_month():
    script = _script()
    assert re.search(r'\bmon\.setAttribute\("aria-label", "Month', script), "the month select has no accessible name"
    assert 'header.indexOf("month")' in script
    assert re.search(r"latest|months\[months\.length - 1\]", script), "no default month"


def test_f42_the_first_columns_stay_in_view_when_the_table_scrolls_sideways():
    style, script = _style(), _script()
    rule = re.search(r"\.frozen-1, \.frozen-2\s*{([^}]*)}", style)
    assert rule and "position: sticky" in rule.group(1) and "background: var(--bg)" in rule.group(1)
    assert re.search(r"\.frozen-2\s*{[^}]*left: 12ch", style), "the second frozen column starts after the first"
    assert "function freezeColumns(" in script and re.search(r"freezeColumns\(wrap\.firstChild\)", script)
    assert not re.search(r"\.style\b", script), "the page's CSP forbids a style set by script"
    assert re.search(r"@media \(max-width[^)]*\)\s*{[^}]*\.frozen-1[^}]*position: static", style, re.S), \
        "frozen columns must not fill a narrow screen"


# ---- findings 43 and 44: the example policy is sized for the vertical preset (about 400 people) ----------------------


@pytest.fixture(scope="module")
def vertical_gold(example_files, tmp_path_factory):
    """The vertical sim (seed 7, 6 months) built with the shipped example policy and prices."""
    import meterline_gold
    meterline_sim = pytest.importorskip("meterline_sim")

    policy, prices = example_files
    sim = meterline_sim.generate(preset="vertical", seed=7, months=6, out=tmp_path_factory.mktemp("demo_vsim"))
    out = tmp_path_factory.mktemp("demo_vgold") / "gold"
    meterline_gold.build(silver=sim, org=Path(sim) / "org", policy=policy, prices=prices, key=KEY, out=out)
    return out


def _nearest_rank(values, percent):
    ordered = sorted(values)
    return ordered[-(-percent * len(ordered) // 100) - 1]


def test_f44_fewer_than_5_percent_of_person_rows_have_no_comparison_band(vertical_gold):
    """Before: 476 of 2,442 rows (19.5 %), because the grouping by function and level left most groups below k."""
    total = hidden = 0
    for path in sorted((Path(vertical_gold) / "person").glob("*/comparison.csv")):
        for row in read_dicts(path):
            total += 1
            hidden += row["hidden"] == "true"
    assert total > 2000
    assert hidden / total < 0.05, "%d of %d rows have no band (%.1f %%)" % (hidden, total, 100 * hidden / total)


def test_f43_the_default_cap_sits_near_the_99th_percentile_of_monthly_cost(vertical_gold, example_files):
    policy = yaml.safe_load(Path(example_files[0]).read_text(encoding="utf-8"))
    costs = [int(Decimal(r["cost_ref"]) * 100) for r in _central(vertical_gold, "person_month")]
    cap = int(Decimal(next(s for s in policy["scenarios"] if s["id"] == "default_cap")["person_caps"]["default"]) * 100)
    low, high = _nearest_rank(costs, 97), max(costs)
    assert low <= cap <= high, "default_cap %s is outside p97 (%s) to the dearest person (%s)" % (cap / 100, low / 100, high / 100)


def test_f43_no_person_cap_scenario_stops_more_than_3_percent_of_people_a_month(vertical_gold):
    by_scenario = {}
    for row in _central(vertical_gold, "scenario_summary"):
        by_scenario.setdefault(row["scenario_id"], []).append(int(row["persons_stopped"]) / int(row["persons"]))
    assert by_scenario
    for scenario, shares in by_scenario.items():
        mean = sum(shares) / len(shares)
        assert mean <= 0.03, "%s stops %.1f %% of people a month" % (scenario, 100 * mean)


def test_f43_unit_envelopes_stop_at_most_one_division_a_month(vertical_gold):
    rows = [r for r in _central(vertical_gold, "scenario_summary") if r["scenario_id"] == "unit_caps_plus_flat"]
    assert rows and all(int(r["units"]) == 6 for r in rows)
    assert sum(int(r["units_stopped"]) for r in rows) / len(rows) <= 1.0


def test_f43_alerts_stay_under_100_a_month(vertical_gold):
    """Before: 1,708 events in 6 months (285 a month), mostly scenario alerts on caps far below the heavy users."""
    months = {r["month"] for r in _central(vertical_gold, "person_month")}
    events = _central(vertical_gold, "alert_events")
    assert len(events) / len(months) < 100, "%d alert events in %d months" % (len(events), len(months))
