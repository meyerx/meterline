"""G26 The ``hidden`` marker (spec 14, v1.2; rule in 7.6.2).

The expected marker of every row is recomputed here from ``central/`` and the policy's ``k``; the rule is never read
from the file under test. Builds: the fixture and the tiny sim (seed 7, 6 months, example policy and prices).
"""

import csv
import hashlib
import io
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from gold_helpers import FIXTURE, KEY, fixture_inputs, one_value_changed, read_csv, read_dicts

FIXTURE_V11_HASHES = FIXTURE / "expected-v1.1-sha256.txt"
TINY_V11_HASHES = FIXTURE / "tiny-sim-v1.1-sha256.txt"

# finding 40 added cost_basis_label and invoice_usage to costs_month; they are withheld with the figure they describe
COSTS_COVERED = ("usage_cost_source", "cost_source_basis", "usage_cost_ref", "total_cost_ref", "overlap_cost_ref", "cost_basis_label",
                 "invoice_usage")
POOLS_COVERED = ("gross_credits", "pool_used_credits", "overage_credits", "overage_amount", "pool_used_share",
                 "budget_state", "budget_crossed_on")
SEATS_COVERED = ("active_seats", "idle_seats")
SUMMARY_COVERED = ("persons_stopped",)
COMPARISON_COVERED = ("comparison_band",)
REASON_WORDS = ("k", "differencing", "secondary")


@pytest.fixture(scope="module")
def tiny_build(example_files, tmp_path_factory):
    """(gold folder, silver/org folder, k) of the tiny sim; only the tiny preset is generated here."""
    import meterline_gold
    meterline_sim = pytest.importorskip("meterline_sim")

    # the v1.1 hashes of TINY_V11_HASHES were made with the example policy of v1.2; that policy is kept here unchanged
    # (the shipped example was resized later, change-list finding 43), so the build still has the same inputs
    _, example_prices = example_files
    # and with the price list of v1.2, whose Anthropic seat line is on (the shipped example has it commented out since the
    # v0.3 hardening, B-6): the example list with that one line switched on
    text = example_prices.read_text(encoding="utf-8")
    line = '# - {provider: anthropic, provider_account_id: null, valid_from: "2000-01-01", per_month: "27.00", included_credits: null}'
    assert line in text
    prices = tmp_path_factory.mktemp("g26_prices") / "prices.yaml"
    prices.write_text(text.replace(line, line[2:]), encoding="utf-8")
    policy = FIXTURE / "tiny-sim-policy-v1.2.yaml"
    sim = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("g26_sim"))
    out = tmp_path_factory.mktemp("g26_gold") / "gold"
    meterline_gold.build(silver=sim, org=Path(sim) / "org", policy=policy, prices=prices, key=KEY, out=out)
    return out, int(yaml.safe_load(Path(policy).read_text(encoding="utf-8"))["privacy"]["k"])


@pytest.fixture(scope="module")
def fixture_k():
    policy = yaml.safe_load(fixture_inputs()["policy"].read_text(encoding="utf-8"))
    return int(policy["privacy"]["k"])


# ---- the expected marker, recomputed from central/ and k -----------------------------------------------------


def _by_key(rows, cols):
    return {tuple(r[c] for c in cols): r for r in rows}


def expected_cost_hidden(out, k):
    """{(month, provider, provider_account_id): hidden} for exec/costs_month (7.6.1: users < k, then the repeat step)."""
    central = Path(out) / "central"
    users = {}
    for r in read_dicts(central / "person_day.csv"):
        if r["identity_status"] == "resolved":
            users.setdefault((r["month"], r["provider"], r["provider_account_id"]), set()).add(r["person_key"])
    keys = sorted((r["month"], r["provider"], r["provider_account_id"]) for r in read_dicts(central / "costs_month.csv"))
    hidden = {key: len(users.get(key, ())) < k for key in keys}
    for month in sorted({key[0] for key in keys}):
        month_keys = [key for key in keys if key[0] == month]
        while True:
            hidden_users = set()
            for key in month_keys:
                if hidden[key]:
                    hidden_users |= users.get(key, set())
            if len(hidden_users) == 0 or len(hidden_users) >= k:
                break
            published = [key for key in month_keys if not hidden[key]]
            if not published:
                break
            fewest = min(published, key=lambda key: (len(users.get(key, ())), key[1], key[2]))
            hidden[fewest] = True
    return hidden


def band_of(value, cuts):
    p50, p75, p90 = cuts
    if p50 is None:
        return None
    if value <= p50:
        return "up_to_p50"
    if p75 is None:
        return "above_p50"
    if value <= p75:
        return "p50_to_p75"
    if p90 is None:
        return "above_p75"
    if value <= p90:
        return "p75_to_p90"
    return "above_p90"


def _dec(text):
    return None if text == "" else Decimal(text)


def _check_columns(path, central_header, covered, problems):
    header, rows = read_csv(path)
    if header[-1] != "hidden":
        problems.append("a: %s: the last column is %r, not hidden" % (path, header[-1]))
        return None, None
    if central_header is not None and header[:-1] != central_header:
        problems.append("a: %s: columns before hidden differ from central" % path)
    for r in rows:
        if r[-1] not in ("true", "false"):
            problems.append("a: %s: hidden holds %r" % (path, r[-1]))
    return header, rows


def _compare_row(label, header, row, central_row, covered, hidden_expected, problems, skip=()):
    """The 'a' and 'b' parts for one row: marker, withheld values empty, other values equal to central's."""
    cells = dict(zip(header, row))
    marker = cells["hidden"]
    if hidden_expected and marker != "true":
        problems.append("a: %s: the rule fires but hidden is %r" % (label, marker))
    if not hidden_expected and marker != "false":
        problems.append("b: %s: the rule does not fire but hidden is %r" % (label, marker))
    for column in header[1:-1]:
        if column in skip:
            continue
        if column in covered and hidden_expected:
            if cells[column] != "":
                problems.append("a: %s: withheld %s is %r, not empty" % (label, column, cells[column]))
        elif cells[column] != central_row[column]:
            problems.append("b: %s: %s is %r, central has %r" % (label, column, cells[column], central_row[column]))


def _cost_pool_marker_problems(out, central, k, problems):
    # exec/costs_month and exec/pools_month
    cost_hidden = expected_cost_hidden(out, k)
    central_costs = _by_key(read_dicts(central / "costs_month.csv"), ("month", "provider", "provider_account_id"))
    c_header = read_csv(central / "costs_month.csv")[0][:]
    header, rows = _check_columns(out / "exec" / "costs_month.csv", c_header, COSTS_COVERED, problems)
    if rows is not None:
        for row in rows:
            cells = dict(zip(header, row))
            key = (cells["month"], cells["provider"], cells["provider_account_id"])
            _compare_row("exec costs %s" % (key,), header, row, central_costs[key], COSTS_COVERED,
                         cost_hidden[key], problems)
            if (cells["hidden"] == "true") != (cells["usage_cost_ref"] == ""):
                problems.append("b: exec costs %s: hidden is not 'usage_cost_ref is empty'" % (key,))
    p_header_central = read_csv(central / "pools_month.csv")[0]
    central_pools = _by_key(read_dicts(central / "pools_month.csv"), ("month", "provider", "provider_account_id"))
    header, rows = _check_columns(out / "exec" / "pools_month.csv", p_header_central, POOLS_COVERED, problems)
    if rows is not None:
        for row in rows:
            cells = dict(zip(header, row))
            key = (cells["month"], cells["provider"], cells["provider_account_id"])
            _compare_row("exec pools %s" % (key,), header, row, central_pools[key], POOLS_COVERED,
                         cost_hidden[key], problems)


def _seat_marker_problems(out, central, k, problems):
    # exec/seats_month
    central_seats = _by_key(read_dicts(central / "seats_month.csv"), ("month", "provider", "provider_account_id"))
    header, rows = _check_columns(out / "exec" / "seats_month.csv", read_csv(central / "seats_month.csv")[0],
                                  SEATS_COVERED, problems)
    if rows is not None:
        for row in rows:
            cells = dict(zip(header, row))
            key = (cells["month"], cells["provider"], cells["provider_account_id"])
            c = central_seats[key]
            billed, on_eval = int(c["seats_billed"]), int(c["seats_on_eval_day"])
            active, idle = int(c["active_seats"]), int(c["idle_seats"])
            active_out = not (active >= k and billed - active >= k)
            idle_out = not (idle >= k and on_eval - idle >= k)
            _compare_row("exec seats %s" % (key,), header, row, c, (), active_out or idle_out, problems,
                         skip=SEATS_COVERED)
            for column, withheld in (("active_seats", active_out), ("idle_seats", idle_out)):
                want = "" if withheld else c[column]
                if cells[column] != want:
                    problems.append("a: exec seats %s: %s is %r, wanted %r" % (key, column, cells[column], want))
            if (cells["hidden"] == "true") != (cells["active_seats"] == "" or cells["idle_seats"] == ""):
                problems.append("b: exec seats %s: hidden is not 'a covered count is empty'" % (key,))


def _summary_marker_problems(out, central, k, problems):
    # exec/scenario_summary
    central_summary = _by_key(read_dicts(central / "scenario_summary.csv"), ("scenario_id", "month"))
    header, rows = _check_columns(out / "exec" / "scenario_summary.csv", None, SUMMARY_COVERED, problems)
    if rows is not None:
        for row in rows:
            cells = dict(zip(header, row))
            key = (cells["scenario_id"], cells["month"])
            c = central_summary[key]
            persons, stopped = int(c["persons"]), int(c["persons_stopped"])
            withheld = not (stopped >= k and persons - stopped >= k)
            marker = cells["hidden"]
            if withheld and marker != "true":
                problems.append("a: exec summary %s: the rule fires but hidden is %r" % (key, marker))
            if not withheld and marker != "false":
                problems.append("b: exec summary %s: the rule does not fire but hidden is %r" % (key, marker))
            want = "" if withheld else c["persons_stopped"]
            if cells["persons_stopped"] != want:
                problems.append("a: exec summary %s: persons_stopped is %r, wanted %r"
                                % (key, cells["persons_stopped"], want))
            for column in ("persons", "units"):
                if cells[column] != c[column]:
                    problems.append("b: exec summary %s: %s differs from central" % (key, column))
            if (marker == "true") != (cells["persons_stopped"] == ""):
                problems.append("b: exec summary %s: hidden is not 'persons_stopped is empty'" % (key,))


def _comparison_marker_problems(out, central, problems):
    # person/<key>/comparison
    cuts = {(r["month"], r["compare_cell"]): (_dec(r["p50_cost_ref"]), _dec(r["p75_cost_ref"]), _dec(r["p90_cost_ref"]))
            for r in read_dicts(central / "comparison_groups.csv")}
    costs = {(r["person_key"], r["month"]): r["cost_ref"] for r in read_dicts(central / "person_month.csv")}
    for path in sorted((out / "person").glob("*/comparison.csv")):
        person = path.parent.name
        header, rows = read_csv(path)
        if header[-1] != "hidden":
            problems.append("a: %s: the last column is %r, not hidden" % (path, header[-1]))
            continue
        for row in rows:
            cells = dict(zip(header, row))
            if cells["hidden"] not in ("true", "false"):
                problems.append("a: %s: hidden holds %r" % (path, cells["hidden"]))
            cost = costs[(person, cells["month"])]
            band = band_of(Decimal(cost), cuts[(cells["month"], cells["compare_cell"])])
            if cells["own_cost_ref"] != cost:
                problems.append("b: %s: own_cost_ref differs from central" % path)
            if band is None and cells["hidden"] != "true":
                problems.append("a: %s %s: no cut is published but hidden is %r" % (path, cells["month"], cells["hidden"]))
            if band is not None and cells["hidden"] != "false":
                problems.append("b: %s %s: a band is published but hidden is %r" % (path, cells["month"], cells["hidden"]))
            if cells["comparison_band"] != (band or ""):
                problems.append("a: %s %s: band %r, wanted %r" % (path, cells["month"], cells["comparison_band"], band))
            if (cells["hidden"] == "true") != (cells["comparison_band"] == ""):
                problems.append("b: %s: hidden is not 'comparison_band is empty'" % path)


def marker_problems(out, k):
    """Every a, b and c problem of the five datasets of a gold folder; [] when G26 a-c hold."""
    out = Path(out)
    central = out / "central"
    problems = []

    _cost_pool_marker_problems(out, central, k, problems)
    _seat_marker_problems(out, central, k, problems)
    _summary_marker_problems(out, central, k, problems)
    _comparison_marker_problems(out, central, problems)

    problems.extend(reason_problems(out))
    return problems


def reason_problems(out):
    """G26 c: no reason outside central."""
    out = Path(out)
    problems = []
    for path in sorted(out.rglob("*.csv")):
        rel = path.relative_to(out).as_posix()
        if rel.startswith("central/"):
            continue
        header, rows = read_csv(path)
        if "suppression_reason" in header:
            problems.append("c: %s has a suppression_reason column" % rel)
    marked = [out / "exec" / name for name in ("costs_month.csv", "pools_month.csv", "seats_month.csv",
                                                "scenario_summary.csv")]
    marked += sorted((out / "person").glob("*/comparison.csv"))
    for path in marked:
        header, rows = read_csv(path)
        for row in rows:
            for column, value in zip(header, row):
                if value in REASON_WORDS:
                    problems.append("c: %s: %s holds %r" % (path.relative_to(out).as_posix(), column, value))
    return problems


# ---- d: published values unchanged ---------------------------------------------------------------------------


# columns renamed after v1.2 (docs/dev/change-list-meterline-gold.md); the v1.1 view reads them under their v1.1 name
RENAMED_SINCE_V11 = {"unreconciled_share": "unallocated_share"}  # finding 41, reconciliation_month
# MF-13 (GOLD_SCHEMA_VERSION 2.0) renamed two columns; the v1.1 hashes were recorded under the old names, so the view maps them back
RENAMED_SINCE_V11.update({"provider_account_id": "org_id", "cell_kind": "group_id"})
WRITTEN_SCHEMA_VERSION = "2.1"  # what the build writes now; the v1.1 view reads it as the 1.0 of the recorded bytes
ADDED_SINCE_V11 = ("cost_basis_label", "invoice_usage")  # finding 40, costs_month
# columns appended in GOLD_SCHEMA_VERSION 2.1 (v0.3 hardening A-2, A-3): the v1.1 bytes did not have them. is_final is new in
# seats_month and costs_month only (pools_month had it); model_provider is new in person_day; month_status in the three org files
ADDED_IN_2_1 = {"person_day": ("model_provider",), "seats_month": ("month_status", "is_final"), "pools_month": ("month_status",),
                "costs_month": ("overlap_cost_ref", "month_status", "is_final")}


def _dataset_of(header):
    """Which of the changed datasets a header is, told by a column only that dataset has; None for every other file."""
    for dataset, marker in (("person_day", "source_records"), ("seats_month", "seat_source"), ("pools_month", "pool_credits"),
                            ("costs_month", "total_cost_ref")):
        if marker in header:
            return dataset
    return None


def v11_view(data):
    """The v1.1 bytes of a v1.2 CSV: hidden removed, schema_version read as 1.0 (it must be 2.0 now), the two columns
    renamed by MF-13 under their v1.1 names."""
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))
    header = [RENAMED_SINCE_V11.get(name, name) for name in rows[0]]
    rows[0] = header
    is_costs = "total_cost_ref" in header  # invoice_usage is also a v1.1 column of reconciliation_month
    new_in_2_1 = ADDED_IN_2_1.get(_dataset_of(header), ())
    drop = {i for i, name in enumerate(header)
            if (is_costs and name in ADDED_SINCE_V11) or name in new_in_2_1 or (name == "hidden" and i == len(header) - 1)}
    sv = header.index("schema_version")
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    for i, row in enumerate(rows):
        row = list(row)
        if i > 0:
            if row[sv] != WRITTEN_SCHEMA_VERSION:
                raise AssertionError("schema_version is %r, not %s" % (row[sv], WRITTEN_SCHEMA_VERSION))
            row[sv] = "1.0"
        writer.writerow([cell for i, cell in enumerate(row) if i not in drop])
    return buf.getvalue().encode("utf-8")


def recorded_hashes(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        out[rel] = digest
    return out


def hash_problems(out, hash_file):
    out = Path(out)
    recorded = recorded_hashes(hash_file)
    built = {p.relative_to(out).as_posix(): p for p in sorted(out.rglob("*.csv"))}
    problems = []
    if sorted(built) != sorted(recorded):
        problems.append("d: paths differ: %s" % sorted(set(built) ^ set(recorded)))
    for rel, digest in recorded.items():
        if rel not in built:
            continue
        if rel == "exec/groups_month.csv" or (rel.startswith("manager/") and rel.endswith("/groups_month.csv")):
            # finding 39: outside central a cell says hidden and no longer why, so the v1.1 bytes cannot be rebuilt
            # from the file; it is checked against central's rows in test_review_demo_findings.py and by G06
            continue
        try:
            got = hashlib.sha256(v11_view(built[rel].read_bytes())).hexdigest()
        except AssertionError as exc:
            problems.append("d: %s: %s" % (rel, exc))
            continue
        if got != digest:
            problems.append("d: %s: sha256 differs from the v1.1 build" % rel)
    return problems


# ---- the checks ----------------------------------------------------------------------------------------------


def test_done_check_G26_a_b_c_fixture(fixture_out, fixture_k):
    assert marker_problems(fixture_out, fixture_k) == []


def test_done_check_G26_a_b_c_tiny_sim(tiny_build):
    out, k = tiny_build
    assert marker_problems(out, k) == []


def test_done_check_G26_d_fixture_published_values_unchanged(fixture_out):
    assert hash_problems(fixture_out, FIXTURE_V11_HASHES) == []


def test_done_check_G26_d_tiny_sim_published_values_unchanged(tiny_build):
    out, _ = tiny_build
    assert hash_problems(out, TINY_V11_HASHES) == []


def _counts(out):
    """dataset -> [rows with hidden true, rows with hidden false] of one gold folder."""
    out = Path(out)
    files = {
        "exec/costs_month": [out / "exec" / "costs_month.csv"],
        "exec/pools_month": [out / "exec" / "pools_month.csv"],
        "exec/seats_month": [out / "exec" / "seats_month.csv"],
        "exec/scenario_summary": [out / "exec" / "scenario_summary.csv"],
        "person/comparison": sorted((out / "person").glob("*/comparison.csv")),
    }
    counts = {}
    for name, paths in files.items():
        t = f = 0
        for path in paths:
            header, rows = read_csv(path)
            if header[-1] != "hidden":
                continue
            t += sum(1 for r in rows if r[-1] == "true")
            f += sum(1 for r in rows if r[-1] == "false")
        counts[name] = [t, f]
    return counts


def test_done_check_G26_coverage(fixture_out, tiny_build):
    total = {}
    for out in (fixture_out, tiny_build[0]):
        for name, (t, f) in _counts(out).items():
            acc = total.setdefault(name, [0, 0])
            acc[0] += t
            acc[1] += f
    assert sorted(total) == ["exec/costs_month", "exec/pools_month", "exec/scenario_summary", "exec/seats_month",
                             "person/comparison"]
    for name, (t, f) in total.items():
        assert t >= 1, "%s: no row with hidden = true in either build" % name
        assert f >= 1, "%s: no row with hidden = false in either build" % name


# ---- negative controls ---------------------------------------------------------------------------------------


def _rewrite(path, edit):
    header, rows = read_csv(path)
    edit(header, rows)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture()
def fixture_copy(fixture_out, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(fixture_out, copy)
    return copy


def test_done_check_G26_control_a_hidden_row_set_to_false(fixture_copy, fixture_k):
    def edit(header, rows):
        i = next(n for n, r in enumerate(rows) if r[-1] == "true")
        rows[i][-1] = "false"

    _rewrite(fixture_copy / "exec" / "costs_month.csv", edit)
    assert any(p.startswith("a:") for p in marker_problems(fixture_copy, fixture_k))


def test_done_check_G26_control_b_published_band_row_set_to_true(fixture_copy, fixture_k):
    def edit(header, rows):
        i = next(n for n, r in enumerate(rows) if r[header.index("comparison_band")] != "")
        rows[i][-1] = "true"

    path = next(p for p in sorted((fixture_copy / "person").glob("*/comparison.csv"))
                if any(r[-2] != "" for r in read_csv(p)[1]))
    _rewrite(path, edit)
    assert any(p.startswith("b:") for p in marker_problems(fixture_copy, fixture_k))


def test_done_check_G26_control_c_reason_written_in_hidden(fixture_copy, fixture_k):
    def edit(header, rows):
        rows[0][-1] = "k"

    _rewrite(fixture_copy / "exec" / "seats_month.csv", edit)
    assert any(p.startswith("c:") for p in marker_problems(fixture_copy, fixture_k))
    assert any(p.startswith("c:") for p in reason_problems(fixture_copy))


def test_done_check_G26_control_d_published_value_changed(fixture_copy):
    path = fixture_copy / "central" / "person_month.csv"
    path.write_bytes(one_value_changed(path.read_bytes()))
    assert any("central/person_month.csv" in p for p in hash_problems(fixture_copy, FIXTURE_V11_HASHES))


def test_done_check_G26_control_d_hidden_column_dropped_before_hashing(fixture_copy):
    # a published digit in a column before hidden: v11_view must drop hidden and leave the rest, so this is flagged
    path = fixture_copy / "exec" / "costs_month.csv"
    data = path.read_bytes()
    changed = one_value_changed(data)
    header = data.split(b"\n", 1)[0].decode("utf-8").split(",")
    assert header[-1] == "hidden"
    assert changed != data
    path.write_bytes(changed)
    problems = hash_problems(fixture_copy, FIXTURE_V11_HASHES)
    assert any(p.startswith("d: exec/costs_month.csv: sha256 differs") for p in problems), problems
