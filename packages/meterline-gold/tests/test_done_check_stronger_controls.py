"""Stronger controls: the checks that the review of phases 1 and 2 found weak (change list findings 12 and 17).

Nothing here replaces an earlier assertion. Each test either covers a case the earlier checks left open or gives a
control that fails for its own reason on a deliberately broken input, and says which one.
"""

import csv
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
pytest.importorskip("hypothesis")  # a base install lacks the dev extra: skip this module
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from gold_helpers import (
    KEY, all_providers_at_k_inputs, dataset_queries, folder_bytes, read_csv, read_dicts, run_cli_subprocess,
    split_statements, strip_sql,
)
from test_done_check_G11_G22_privacy import (
    MONTH, Scenario, attribute_scenarios, load_scenario, unit_scenarios, unit_tree,
)


def _build(inp, out, **kwargs):
    import meterline_gold

    return meterline_gold.build(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY, out=out, **kwargs,
    )


def _pkg():
    import meterline_gold

    return Path(meterline_gold.__file__).resolve().parent


# ---- G03 (exec): the cases the fixture itself cannot reach --------------------------------------------------

PROVIDERS = ("anthropic", "github_copilot", "litellm")
USAGE_COLUMNS = ("usage_cost_source", "cost_source_basis", "usage_cost_ref", "total_cost_ref")
POOL_COLUMNS = ("gross_credits", "pool_used_credits", "overage_credits", "overage_amount", "pool_used_share",
                "budget_state", "budget_crossed_on")


@pytest.fixture(scope="module")
def every_provider_at_k(tmp_path_factory):
    base = tmp_path_factory.mktemp("variant_all")
    inp = all_providers_at_k_inputs(base / "in")
    return _build(inp, base / "gold")


@pytest.fixture(scope="module")
def litellm_only_below_k(tmp_path_factory):
    base = tmp_path_factory.mktemp("variant_cascade")
    inp = all_providers_at_k_inputs(base / "in", litellm=False)
    return _build(inp, base / "gold")


def _users_per_provider(out):
    users = {}
    for r in read_dicts(out / "central" / "person_day.csv"):
        if r["person_key"]:
            users.setdefault(r["provider"], set()).add(r["person_key"])
    return {p: len(v) for p, v in users.items()}


def test_done_check_G03_variant_every_provider_has_k_users(every_provider_at_k):
    # the variant itself: 5 is the policy's k, and every provider is at or above it
    assert _users_per_provider(every_provider_at_k) == {"anthropic": 13, "github_copilot": 5, "litellm": 5}


def test_done_check_G03_exec_hides_nothing_when_every_provider_has_k_users(every_provider_at_k):
    out = every_provider_at_k
    costs = read_dicts(out / "exec" / "costs_month.csv")
    assert [r["provider"] for r in costs] == list(PROVIDERS)
    for r in costs:
        for column in USAGE_COLUMNS:
            assert r[column] != "", (r["provider"], column)
    # no row needs hiding, so exec's costs and pools are the central files with the last column hidden (v1.2, 7.6.2),
    # which is false on every row
    for name in ("costs_month", "pools_month"):
        exec_header, exec_rows = read_csv(out / "exec" / f"{name}.csv")
        central_header, central_rows = read_csv(out / "central" / f"{name}.csv")
        assert exec_header == central_header + ["hidden"], name
        assert [r[:-1] for r in exec_rows] == central_rows, name
        assert exec_rows and all(r[-1] == "false" for r in exec_rows), name
    pools = read_dicts(out / "exec" / "pools_month.csv")
    assert pools and all(r[c] != "" for r in pools for c in POOL_COLUMNS)


def test_done_check_G03_control_the_fixture_exec_does_hide_rows(fixture_out):
    # the control for the test above: with the fixture's 4 and 1 users the same assertion fails, because exec's
    # costs file is not the central one and holds blanks
    exec_costs = (fixture_out / "exec" / "costs_month.csv").read_bytes()
    central_costs = (fixture_out / "central" / "costs_month.csv").read_bytes()
    assert exec_costs != central_costs
    blanks = {r["provider"] for r in read_dicts(fixture_out / "exec" / "costs_month.csv") if r["usage_cost_ref"] == ""}
    assert blanks == {"github_copilot", "litellm"}


def test_done_check_G03_exec_hides_a_second_row_when_the_first_hidden_has_under_k_users(litellm_only_below_k):
    """The cascade of spec 7.6.1: LiteLLM has 1 user and is hidden; those hidden users number 1 to k - 1, so the
    published row with the fewest users (GitHub Copilot, 5) is hidden too; then 6 users are hidden and it stops.
    Anthropic, with 13, stays. Without the second step, central minus the published rows would isolate LiteLLM."""
    out = litellm_only_below_k
    assert _users_per_provider(out) == {"anthropic": 13, "github_copilot": 5, "litellm": 1}
    costs = {r["provider"]: r for r in read_dicts(out / "exec" / "costs_month.csv")}
    assert set(costs) == set(PROVIDERS)
    assert all(costs["anthropic"][c] != "" for c in USAGE_COLUMNS)
    for provider in ("github_copilot", "litellm"):
        assert all(costs[provider][c] == "" for c in USAGE_COLUMNS), provider
    pools = read_dicts(out / "exec" / "pools_month.csv")
    assert pools and all(r[c] == "" for r in pools for c in POOL_COLUMNS)
    # seat figures are not part of the cascade: the seat cost of the hidden provider is still shown
    assert costs["github_copilot"]["seat_cost"] != ""
    # central keeps everything
    central = {r["provider"]: r for r in read_dicts(out / "central" / "costs_month.csv")}
    assert all(central[p]["usage_cost_ref"] != "" for p in PROVIDERS)


def test_done_check_G26_repeat_step_marker_on_the_cascade_variant(litellm_only_below_k):
    """G26 on the cascade build: the marker agrees with the 7.6.1 rule, and a row is hidden by the repeat step alone
    (Copilot has 5 = k users, so the users < k test would leave it published)."""
    from test_done_check_G26_hidden_marker import marker_problems

    assert marker_problems(litellm_only_below_k, 5) == []
    users = _users_per_provider(litellm_only_below_k)
    rows = {r["provider"]: r for r in read_dicts(litellm_only_below_k / "exec" / "costs_month.csv")}
    by_repeat = [p for p, r in rows.items() if users[p] >= 5 and r["hidden"] == "true"]
    assert by_repeat == ["github_copilot"]
    assert rows["litellm"]["hidden"] == "true" and rows["anthropic"]["hidden"] == "false"


def test_done_check_G03_variant_reconciles_like_the_fixture(every_provider_at_k):
    # the added records are matched by organisation totals: nothing is unallocated beyond the fixture's own
    rows = {r["provider"]: r for r in read_dicts(every_provider_at_k / "central" / "reconciliation_month.csv")}
    assert rows["litellm"]["unallocated_cost_source"] == "0.00"
    assert rows["github_copilot"]["unallocated_credits"] == "0"
    assert rows["anthropic"]["unallocated_cost_source"] == "0.50"


# ---- G13: exec's three tables, and every dataset's ORDER BY covers its whole key ----------------------------------

EXEC_KEYS = {
    "costs_month": ("month", "provider", "provider_account_id"),
    "pools_month": ("month", "provider", "provider_account_id"),
    "seats_month": ("month", "provider", "provider_account_id"),
}


def _order_problems(path, key):
    header, rows = read_csv(path)
    idx = [header.index(c) for c in key]
    prev, problems = None, []
    for n, row in enumerate(rows):
        cur = tuple(row[i] for i in idx)
        if prev is not None and not prev < cur:
            problems.append((n, prev, cur))
        prev = cur
    return problems


def test_done_check_G13_exec_costs_pools_seats_sorted_by_key(fixture_out, sim_builds, every_provider_at_k, tmp_path):
    checked = 0
    for out in (fixture_out, every_provider_at_k, sim_builds["tiny"][0]):
        for name, key in EXEC_KEYS.items():
            path = out / "exec" / (name + ".csv")
            assert _order_problems(path, key) == [], path
            checked += 1
    assert checked == 9
    # control: the same file with its rows reversed is flagged (the file has more than one row)
    header, rows = read_csv(every_provider_at_k / "exec" / "costs_month.csv")
    assert len(rows) > 1
    bad = tmp_path / "costs_month.csv"
    with open(bad, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(reversed(rows))
    assert _order_problems(bad, EXEC_KEYS["costs_month"]) != []


def _order_by_columns(statement):
    """Column names of the statement's final ORDER BY, in order: table prefix, ASC/DESC and NULLS FIRST/LAST
    dropped. None when the statement does not end with one."""
    body = strip_sql(statement).strip().rstrip(";").strip()
    match = re.search(r"\border\s+by\b([^()]*)$", body, re.I | re.S)
    if match is None:
        return None
    columns = []
    for part in match.group(1).split(","):
        token = part.strip().split()[0]
        columns.append(token.split(".")[-1].lower())
    return columns


def _key_gaps(statement, key):
    """The key columns that the ORDER BY does not cover, taking them as an in-order subsequence."""
    columns = _order_by_columns(statement)
    if columns is None:
        return list(key)
    missing, position = [], 0
    for column in key:
        try:
            position = columns.index(column, position) + 1
        except ValueError:
            missing.append(column)
    return missing


def test_done_check_G20_order_by_covers_the_whole_key_of_every_dataset():
    from meterline_gold import write

    found = dataset_queries(_pkg() / "sql")
    checked = set()
    for name, statements in found.items():
        base = name.split("/")[-1]
        if base not in write.DATASET_KEYS:
            continue  # 'manager/keys' lists folders and has no key
        for stmt in statements:
            assert _key_gaps(stmt, write.DATASET_KEYS[base]) == [], (name, _order_by_columns(stmt))
            checked.add(base)
    assert checked == set(write.DATASET_KEYS)
    # controls: each way of ordering badly is flagged, for its own reason
    key = ("month", "provider", "provider_account_id")
    assert _key_gaps("-- dataset: x\nSELECT 1 FROM t ORDER BY month, provider", key) == ["provider_account_id"]
    assert _key_gaps("-- dataset: x\nSELECT 1 FROM t ORDER BY provider, month, provider_account_id", key) == ["provider"]
    assert _key_gaps("-- dataset: x\nSELECT 1 FROM t ORDER BY cost_ref", key) == list(key)
    assert _key_gaps("-- dataset: x\nSELECT 1 FROM t", key) == list(key)
    assert _key_gaps("-- dataset: x\nSELECT 1 FROM t ORDER BY t.month NULLS FIRST, provider DESC, provider_account_id", key) == []
    assert len(split_statements("SELECT 1; SELECT 2;")) == 2


# ---- G08: row order, and the thread count on a build that has something to reorder --------------------------


def test_done_check_G08_control_a_reordered_row_changes_the_folder_bytes(fixture_out, tmp_path):
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(fixture_out, copy)
    path = copy / "central" / "person_day.csv"
    header, rows = read_csv(path)
    assert len(rows) > 2
    rows[0], rows[1] = rows[1], rows[0]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    assert sorted(folder_bytes(copy)) == sorted(folder_bytes(fixture_out))
    assert folder_bytes(copy) != folder_bytes(fixture_out)


@pytest.mark.parametrize("threads", [1, 8])
def test_done_check_G08_thread_count_does_not_change_the_bytes_of_the_tiny_sim(sim_runs, example_files, tmp_path, threads):
    import meterline_gold

    run = sim_runs["tiny"]
    policy, prices = example_files
    reference = tmp_path / "reference"
    meterline_gold.build(silver=run.silver, org=run.org, policy=policy, prices=prices, key=KEY, out=reference, threads=2)
    other = tmp_path / "other"
    meterline_gold.build(silver=run.silver, org=run.org, policy=policy, prices=prices, key=KEY, out=other, threads=threads)
    first, second = folder_bytes(reference), folder_bytes(other)
    # a build that has per-person and per-manager folders: many files, so there is order to get wrong
    assert any(n.startswith("person/") for n in first) and any(n.startswith("manager/") for n in first)
    assert len(first) > 50
    assert first == second


def test_done_check_G08_vertical_sim_same_bytes_with_one_thread(sim_runs, sim_builds, example_files, tmp_path):
    # the vertical preset has 450 persons and about 40,000 person-days: enough rows for a thread-dependent
    # order to show; the session build used DuckDB's default thread count
    import meterline_gold

    policy, prices = example_files
    run = sim_runs["vertical"]
    other = tmp_path / "one_thread"
    meterline_gold.build(silver=run.silver, org=run.org, policy=policy, prices=prices, key=KEY, out=other, threads=1)
    assert folder_bytes(other) == folder_bytes(sim_builds["vertical"][0])


# ---- G19: a failure while writing (the A09 path) exits 1 and leaves nothing --------------------------------


def test_done_check_G19_failure_during_writing_exits_1_and_leaves_nothing(tmp_path, monkeypatch, capsys):
    from meterline_gold import cli, write

    from gold_helpers import fixture_inputs

    inp = fixture_inputs()
    out = tmp_path / "gold"
    seen = {}
    real = write.a09_violations

    def planted(out_dir, org_dir, silver_dir):
        # called from inside write_gold, after the files exist in the temporary folder
        seen["files"] = sorted(p.name for p in Path(out_dir).rglob("*.csv"))
        seen["tmp_is_out_dir"] = str(out_dir) == str(out) + ".tmp"
        assert real(out_dir, org_dir, silver_dir) == []
        return [("central/person_day.csv", 2, "actor_key", "planted")]

    monkeypatch.setattr(write, "a09_violations", planted)
    monkeypatch.setenv("METERLINE_GOLD_KEY", KEY.decode())
    args = ["build", "--silver", str(inp["silver"]), "--org-files", str(inp["org"]), "--policy", str(inp["policy"]),
            "--prices", str(inp["prices"]), "--invoice", str(inp["invoice"]), "--out", str(out)]
    assert cli.main(args) == 1
    assert seen["tmp_is_out_dir"] and "person_day.csv" in seen["files"]  # files were written before the failure
    assert not out.exists()
    assert not Path(str(out) + ".tmp").exists()
    assert "A09" in capsys.readouterr().err
    # control: without the planted violation the same call succeeds and writes the folder
    monkeypatch.setattr(write, "a09_violations", real)
    assert cli.main(args) == 0
    assert (out / "manifest.json").is_file()


# ---- finding 17: the active_persons remainder rule of spec 7.2, on generated data -----------------------------


def _reference_active_published(scn, state):
    """The rule of 7.2 restated outside the SQL, bottom-up: a cell publishes ``active_persons`` when it is
    unsuppressed, holds at least k active and k inactive persons, and, once the persons of its published
    descendants are removed, what remains is empty or holds at least k active and at least k inactive persons,
    both with and without the cell's receivers."""
    k = scn.k
    members = {c: set(persons) for c, _, persons, _ in scn.cells}
    receivers = {c: set(r) for c, _, _, r in scn.cells}
    parent = {c: p for c, p, _, _ in scn.cells}

    def depth(cell):
        d, cur = 0, parent[cell]
        while cur:
            d, cur = d + 1, parent[cur]
        return d

    def descendants(cell):
        return [c for c in members if c != cell and _is_below(c, cell)]

    def _is_below(cell, ancestor):
        cur = parent[cell]
        while cur:
            if cur == ancestor:
                return True
            cur = parent[cur]
        return False

    published = set()
    for cell in sorted(members, key=lambda c: -depth(c)):
        if (MONTH, cell) in state:
            continue
        persons = members[cell]
        if sum(scn.active[p] for p in persons) < k or sum(1 - scn.active[p] for p in persons) < k:
            continue
        covered = set()
        for below in descendants(cell):
            if below in published:
                covered |= members[below]
        remainder = persons - covered
        if remainder:
            def count(group):
                return sum(scn.active[p] for p in group), sum(1 - scn.active[p] for p in group)

            a_with, i_with = count(remainder)
            a_without, i_without = count(remainder - receivers[cell])
            if min(a_with, i_with, a_without, i_without) < k:
                continue
        published.add(cell)
    return {(MONTH, c) for c in published}


def _remainder_problems(scn, state, published):
    """Every published ``active_persons`` cell must pass the remainder test on the published set, and the published
    set must be exactly what the bottom-up reference gives."""
    problems = []
    members = {c: set(persons) for c, _, persons, _ in scn.cells}
    receivers = {c: set(r) for c, _, _, r in scn.cells}
    parent = {c: p for c, p, _, _ in scn.cells}
    published_cells = {c for m, c in published}

    def below(cell, ancestor):
        cur = parent[cell]
        while cur:
            if cur == ancestor:
                return True
            cur = parent[cur]
        return False

    for cell in sorted(published_cells):
        if (MONTH, cell) in state:
            problems.append(("a suppressed cell publishes active_persons", cell))
            continue
        covered = set()
        for other in published_cells:
            if other != cell and below(other, cell):
                covered |= members[other]
        remainder = members[cell] - covered
        for label, group in (("cell", members[cell]), ("remainder", remainder), ("remainder minus receivers", remainder - receivers[cell])):
            if label != "cell" and not remainder:
                continue
            active = sum(scn.active[p] for p in group)
            if active < scn.k or len(group) - active < scn.k:
                problems.append(("%s of %s holds %d active and %d inactive, k = %d"
                                 % (label, cell, active, len(group) - active, scn.k), cell))
    if published != _reference_active_published(scn, state):
        problems.append(("published set differs from the bottom-up reference",
                         sorted(published ^ _reference_active_published(scn, state))))
    return problems


def _solve(scn):
    from meterline_gold import run

    con = load_scenario(scn)
    state = run.run_privacy(con, scn.k)
    published = {(m, c) for m, c in con.execute("SELECT month, cell_id FROM cell_active_published").fetchall()}
    return state, published


def test_done_check_G11_active_persons_remainder_rule_on_generated_data():
    counters = Counter()

    @settings(
        max_examples=300, deadline=None, derandomize=True,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
    )
    @given(st.one_of(unit_scenarios(), attribute_scenarios()))
    def prop(scn):
        state, published = _solve(scn)
        # a candidate: unsuppressed, k active and k inactive; the remainder rule may still withhold it
        members = {c: set(p) for c, _, p, _ in scn.cells}
        candidates = {
            (MONTH, c) for c, persons in members.items()
            if (MONTH, c) not in state
            and sum(scn.active[p] for p in persons) >= scn.k
            and sum(1 - scn.active[p] for p in persons) >= scn.k
        }
        withheld = candidates - published
        # does the "without the cell's receivers" clause decide anything here? Compare with a reference that
        # ignores receivers
        no_receivers = Scenario(scn.k, [(c, p, m, []) for c, p, m, _ in scn.cells], scn.costs, scn.active)
        if _reference_active_published(no_receivers, state) != _reference_active_published(scn, state):
            counters["receivers_decide"] += 1
        counters["examples"] += 1
        counters["with_a_published_cell"] += 1 if published else 0
        counters["with_a_withheld_candidate"] += 1 if withheld else 0
        event("withheld by the remainder rule" if withheld else "nothing withheld")
        problems = _remainder_problems(scn, state, published)
        assert problems == [], (scn.k, scn.cells, scn.active, problems)

    prop()
    # the generated flags must make the rule non-trivial: the rule must publish in many examples and withhold a
    # candidate in some, or the test passes without testing anything
    assert counters["examples"] >= 100
    assert counters["with_a_published_cell"] * 10 >= counters["examples"], dict(counters)
    assert counters["with_a_withheld_candidate"] >= 5, dict(counters)
    assert counters["receivers_decide"] >= 1, dict(counters)


def _remainder_scenario(inactive_in_remainder, k=2):
    """org holds two published-looking children of k active and k inactive each, plus a remainder of direct members
    with ``inactive_in_remainder`` inactive and k active persons. The org is itself unsuppressed."""
    costs, active, units = {}, {}, []

    def make(prefix, n_active, n_inactive):
        persons = []
        for i in range(n_active + n_inactive):
            person = "%s%d" % (prefix, i)
            costs[person] = 1
            active[person] = 1 if i < n_active else 0
            persons.append(person)
        return persons

    units.append(("r", None, make("d", k, inactive_in_remainder), None))
    units.append(("r.a", "r", make("a", k, k), None))
    units.append(("r.b", "r", make("b", k, k), None))
    return unit_tree(units, k, costs, active)


def test_done_check_G11_control_the_remainder_checker_flags_a_wrongly_published_cell():
    # remainder = the root's 2 direct members: 2 active, 0 inactive. The children publish; the root must not.
    scn = _remainder_scenario(inactive_in_remainder=0)
    state, published = _solve(scn)
    assert ("2026-05", "unit:r.a") in published and ("2026-05", "unit:r.b") in published
    assert (MONTH, "unit:r") not in published and (MONTH, "org:") not in published
    assert _remainder_problems(scn, state, published) == []
    # the broken input: the same state with the root added to the published set
    broken = set(published) | {(MONTH, "unit:r")}
    problems = _remainder_problems(scn, state, broken)
    assert any(text.startswith("remainder of unit:r holds 2 active and 0 inactive") for text, _ in problems), problems
    assert any(text.startswith("published set differs") for text, _ in problems), problems
    # and the opposite break: a cell the rule would publish is missing
    healthy = _remainder_scenario(inactive_in_remainder=2)
    state, published = _solve(healthy)
    assert (MONTH, "unit:r") in published
    assert _remainder_problems(healthy, state, published) == []
    missing = set(published) - {(MONTH, "unit:r")}
    assert any(text.startswith("published set differs") for text, _ in _remainder_problems(healthy, state, missing))
