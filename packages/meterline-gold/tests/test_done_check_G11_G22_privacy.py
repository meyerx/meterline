"""Phase 2 done-checks G11 (privacy property test) and G22 (privacy worked example). Spec sections 7.4 and 14.

Interface used (spec 2 and 14, with the two details the spec leaves open recorded in the change list):
``run.run_privacy(con, k, *, _disable_block_rule=False)`` reads ``cells(month, cell_id, parent_cell_id,
persons_list, receivers_list)`` and returns ``{(month, cell_id): suppression_reason}`` for every suppressed cell.
When the connection also holds ``person_flags(month, person_key, active)`` it also decides the published
``active_persons`` cells (7.2) into the table ``cell_active_published(month, cell_id)``.
"""

import pytest
import itertools
import math
from collections import Counter
from fractions import Fraction
from pathlib import Path

import duckdb
pytest.importorskip("hypothesis")  # a base install lacks the dev extra: skip this module
from hypothesis import HealthCheck, event, example, given, settings
from hypothesis import strategies as st

from gold_helpers import FIXTURE, read_dicts

MONTH = "2026-05"


# ---- a scenario: persons, costs, flags and the cell tree -----------------------------------------


class Scenario:
    def __init__(self, k, cells, costs, active):
        self.k = k
        self.cells = cells  # list of (cell_id, parent_cell_id, persons, receivers)
        self.costs = costs  # person -> int
        self.active = active  # person -> 0/1

    @property
    def persons(self):
        return sorted(self.costs)


def load_scenario(scn, month=MONTH):
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE cells (month VARCHAR, cell_id VARCHAR, parent_cell_id VARCHAR, persons_list VARCHAR[], "
        "receivers_list VARCHAR[])"
    )
    for cell_id, parent, persons, receivers in scn.cells:
        con.execute(
            "INSERT INTO cells VALUES (?, ?, ?, ?, ?)", [month, cell_id, parent, sorted(persons), sorted(receivers)]
        )
    con.execute("CREATE TABLE person_flags (month VARCHAR, person_key VARCHAR, active BOOLEAN)")
    for person in scn.persons:
        con.execute("INSERT INTO person_flags VALUES (?, ?, ?)", [month, person, bool(scn.active[person])])
    return con


def unit_tree(units, k, costs, active):
    """units: list of (unit_id, parent_unit_id or None, direct members, head or None)."""
    parent_of = {u: p for u, p, _, _ in units}
    direct = {u: list(d) for u, _, d, _ in units}
    head = {u: h for u, _, _, h in units}
    members = {u: set(direct[u]) for u in direct}
    for u in direct:
        cur = parent_of[u]
        while cur is not None:
            members[cur] |= set(direct[u])
            cur = parent_of[cur]
    everyone = sorted(set().union(*members.values())) if members else []
    cells = [("org:", "", everyone, [])]
    for u, p, _, _ in units:
        if not members[u]:
            continue
        receivers, cur = [], u
        while cur is not None:
            if head[cur] is not None:
                receivers.append(head[cur])
            cur = parent_of[cur]
        parent = "org:" if p is None else "unit:" + p
        cells.append(("unit:" + u, parent, sorted(members[u]), receivers))
    return Scenario(k, cells, costs, active)


def _person(draw, costs, active):
    person = "p%02d" % len(costs)
    costs[person] = draw(st.integers(0, 40))
    active[person] = draw(st.integers(0, 1))
    return person


@st.composite
def unit_scenarios(draw):
    """A unit tree of depth <= 3, 1-4 children per unit, 0-6 direct members per unit (the head is the first),
    at most 25 persons, k in 2..6. Half of the examples are built to have a suppressed unit under a published
    one (a small child under a root with larger ones), a quarter are deep trees, a quarter are drawn uniformly."""
    costs, active, units = {}, {}, []
    branch = draw(st.integers(0, 3))
    if branch <= 1:
        k = draw(st.integers(2, 5))
        root = [_person(draw, costs, active) for _ in range(draw(st.integers(0, 2)))]
        units.append(("r", None, root, root[0] if root else None))
        sizes = [min(6, k + 1)] * 3 + [draw(st.integers(1, k - 1))]
        for i, size in enumerate(sizes):
            members = [_person(draw, costs, active) for _ in range(size)]
            units.append(("r.%d" % i, "r", members, members[0]))
        return unit_tree(units, k, costs, active)

    k = draw(st.integers(2, 6))
    budget = [25]
    deep = branch == 2  # every unit above depth 3 has children, with 1-3 persons at the leaves' parents

    def new_unit(unit_id, parent, depth):
        n = min(draw(st.integers(0, 3 if deep else 6)), budget[0])
        budget[0] -= n
        members = [_person(draw, costs, active) for _ in range(n)]
        units.append((unit_id, parent, members, members[0] if members else None))
        if depth < 3:
            for i in range(draw(st.integers(1 if deep else 0, 3 if deep else 4))):
                new_unit("%s.%d" % (unit_id, i), unit_id, depth + 1)

    new_unit("r", None, 1)
    return unit_tree(units, k, costs, active)


@st.composite
def attribute_scenarios(draw):
    """An attribute chain of 1-2 attributes with 1-4 values each, at most 25 persons, k in 2..6. Three examples
    in four are built to have a value group under k beside larger ones; the rest are drawn uniformly."""
    costs, active, values = {}, {}, {}
    if draw(st.integers(0, 1)) == 0:
        k = draw(st.integers(2, 5))
        depth = draw(st.integers(1, 2))
        sizes = [k + 1, k + 1, k + 1, draw(st.integers(1, k - 1))]
        for v, size in enumerate(sizes):
            for _ in range(size):
                person = _person(draw, costs, active)
                values[person] = (v,) + tuple(draw(st.integers(0, 1)) for _ in range(depth - 1))
    else:
        k = draw(st.integers(2, 6))
        depth = draw(st.integers(1, 2))
        for _ in range(draw(st.integers(1, 25))):
            person = _person(draw, costs, active)
            values[person] = tuple(draw(st.integers(0, 3)) for _ in range(depth))
    cells = [("org:", "", sorted(costs), [])]
    for level in range(1, depth + 1):
        tuples = sorted({v[:level] for v in values.values()})
        for t in tuples:
            cell_id = "attr:" + "|".join("a%d=%d" % (j + 1, t[j]) for j in range(level))
            parent = "org:" if level == 1 else "attr:" + "|".join("a%d=%d" % (j + 1, t[j]) for j in range(level - 1))
            persons = sorted(p for p, v in values.items() if v[:level] == t)
            cells.append((cell_id, parent, persons, []))
    return Scenario(k, cells, costs, active)


def fixture_scenario():
    """The fixture's tree, persons and costs (Appendix A): org, u01 > u02, u03; k = 5."""
    people = read_dicts(FIXTURE / "input" / "org" / "people.csv")
    units = read_dicts(FIXTURE / "input" / "org" / "units.csv")
    unit_parent = {u["unit_id"]: (u["parent_id"] or None) for u in units}
    head = {u["unit_id"]: (u["head_person_id"] or None) for u in units}
    direct = {u: [] for u in unit_parent}
    for p in people:
        direct[p["unit_id"]].append(p["person_id"])
    pm = read_dicts(FIXTURE / "expected" / "central" / "person_month.csv")
    # person_month rows carry hashed keys; the expected person_comparison file gives the same order of persons
    ids = sorted(p["person_id"] for p in people)
    costs, active = {}, {}
    from decimal import Decimal

    from gold_helpers import person_key

    by_key = {r["person_key"]: r for r in pm}
    for pid in ids:
        r = by_key[person_key(pid)]
        costs[pid] = int(Decimal(r["cost_ref"]) * 1000)
        active[pid] = 1 if int(r["active_days"]) > 0 else 0
    return unit_tree([(u, unit_parent[u], direct[u], head[u]) for u in sorted(unit_parent)], 5, costs, active)


# ---- the attacker ---------------------------------------------------------------------------------


def _rref(rows, n):
    """Reduced row echelon form of a matrix of Fractions with n columns: (reduced rows, pivot columns)."""
    mat = [[Fraction(x) for x in r] for r in rows]
    pivots, row = [], 0
    for col in range(n):
        pivot = next((i for i in range(row, len(mat)) if mat[i][col] != 0), None)
        if pivot is None:
            continue
        mat[row], mat[pivot] = mat[pivot], mat[row]
        scale = mat[row][col]
        mat[row] = [x / scale for x in mat[row]]
        for i in range(len(mat)):
            if i != row and mat[i][col] != 0:
                factor = mat[i][col]
                mat[i] = [a - factor * b for a, b in zip(mat[i], mat[row])]
        pivots.append(col)
        row += 1
        if row == len(mat):
            break
    return mat[:row], pivots


def _columns_of_nullspace(rows, n):
    """For each of n unknowns, its coordinates in a basis of the nullspace of ``rows``."""
    reduced, pivots = _rref(rows, n)
    free = [c for c in range(n) if c not in pivots]
    columns = [[] for _ in range(n)]
    for f in free:
        vec = [Fraction(0)] * n
        vec[f] = Fraction(1)
        for r, p in zip(reduced, pivots):
            vec[p] = -r[f]
        for i in range(n):
            columns[i].append(vec[i])
    return [tuple(c) for c in columns]


def _integer_columns(columns):
    """The same columns scaled by one common factor to integers (zero-sum tests are scale free)."""
    denominators = [x.denominator for col in columns for x in col] or [1]
    scale = 1
    for d in denominators:
        scale = scale * d // math.gcd(scale, d)
    return [tuple(int(x * scale) for x in col) for col in columns]


def _small_zero_sum(columns, max_size):
    """A nonempty set of at most ``max_size`` indices whose columns sum to zero, or None."""
    n = len(columns)
    if n == 0:
        return None
    d = len(columns[0])
    if d == 0:  # no nullspace: every vector is in the row space
        return (0,)
    columns = _integer_columns(columns)
    zero = tuple(0 for _ in range(d))

    def add(a, b):
        return tuple(x + y for x, y in zip(a, b))

    def neg(a):
        return tuple(-x for x in a)

    half_a = min(2, max_size)
    half_b = max_size - half_a
    small = {}
    for size in range(0, half_a + 1):
        for combo in itertools.combinations(range(n), size):
            total = zero
            for i in combo:
                total = add(total, columns[i])
            small.setdefault(total, []).append(frozenset(combo))
    for size in range(0, half_b + 1):
        for combo in itertools.combinations(range(n), size):
            total = zero
            for i in combo:
                total = add(total, columns[i])
            for other in small.get(neg(total), []):
                union = other | frozenset(combo)
                if union and not (other & frozenset(combo)):
                    return tuple(sorted(union))
    return None


def uncovered_parts(scn, state):
    """Suppressed cell -> persons outside its nearest unsuppressed descendants (7.4 b2)."""
    members = {c: set(p) for c, _, p, _ in scn.cells}
    parent = {c: pa for c, pa, _, _ in scn.cells}
    out = {}
    for cell_id in members:
        if (MONTH, cell_id) not in state:
            continue
        covered = set()
        for d, ms in members.items():
            cur = parent[d]
            while cur:
                if cur == cell_id:
                    if (MONTH, d) not in state:
                        covered |= ms
                    break
                cur = parent[cur]
        out[cell_id] = members[cell_id] - covered
    return out


def attack_problems(scn, state, active_published, month=MONTH):
    """What an exact linear attacker recovers (G11 assertions a and b): a list of descriptions."""
    members = {c: set(p) for c, _, p, _ in scn.cells}
    receivers = {c: set(r) for c, _, _, r in scn.cells}
    published = [c for c in members if (month, c) not in state]
    problems = []
    persons = scn.persons
    parts = uncovered_parts(scn, state)

    attackers = {frozenset(): set(published)}
    for c in published:
        if receivers[c]:
            key = frozenset(receivers[c])
            seen = {u for u in published if key & receivers[u]}
            attackers[key] = seen
    for known, visible in attackers.items():
        cost_cells = sorted(visible)
        active_cells = sorted(c for c in visible if (month, c) in active_published)
        # active_persons is published for a subset of the published cells, so its row space lies inside the
        # one of cost_ref and the second system is only built when the subset is a proper one
        measures = [("cost_ref", cost_cells)]
        if active_cells != cost_cells:
            measures.append(("active_persons", active_cells))
        for measure, rows_for in measures:
            unknown = [p for p in persons if p not in known]
            index = {p: i for i, p in enumerate(unknown)}
            rows = [[1 if p in members[c] else 0 for p in unknown] for c in rows_for]
            rows = [r for r in rows if any(r)]
            columns = _columns_of_nullspace(rows, len(unknown))
            # a. a suppressed cell's uncovered part must not be in the row space
            for cell_id, part in sorted(parts.items()):
                vec = sorted(index[p] for p in part if p in index)
                if not vec:
                    continue
                d = len(columns[0])
                if all(sum((columns[i][j] for i in vec), Fraction(0)) == 0 for j in range(d)):
                    problems.append(("a", measure, sorted(known), cell_id))
            # b. no small 0/1 vector in the row space
            hit = _small_zero_sum(columns, scn.k - 1)
            if hit is not None:
                problems.append(("b", measure, sorted(known), [unknown[i] for i in hit]))
    return problems


def solve(scn, disable_block_rule=False):
    from meterline_gold import run

    con = load_scenario(scn)
    state = run.run_privacy(con, scn.k, _disable_block_rule=disable_block_rule)
    active = {
        (m, c) for m, c in con.execute("SELECT month, cell_id FROM cell_active_published").fetchall()
    }
    return state, active


# ---- G11 -----------------------------------------------------------------------------------------


def test_done_check_G11_privacy_property():
    counters = Counter()

    @settings(
        max_examples=300, deadline=None, derandomize=True,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
    )
    @example(fixture_scenario())
    @given(st.one_of(unit_scenarios(), attribute_scenarios()))
    def prop(scn):
        state, active = solve(scn)
        published = [c for c, _, _, _ in scn.cells if (MONTH, c) not in state]
        parent = {c: p for c, p, _, _ in scn.cells}

        def has_published_ancestor(cell):
            cur = parent[cell]
            while cur:
                if (MONTH, cur) not in state:
                    return True
                cur = parent[cur]
            return False

        covered = len(published) >= 3 and any(
            (MONTH, c) in state and has_published_ancestor(c) for c, _, _, _ in scn.cells
        )
        counters["examples"] += 1
        counters["covered"] += 1 if covered else 0
        event("covered" if covered else "not covered")
        problems = attack_problems(scn, state, active)
        assert problems == [], (scn.k, scn.cells, problems)

    prop()
    # c. coverage: at least half of the examples have a suppressed cell under a published ancestor and
    # at least 3 published cells
    assert counters["examples"] >= 100
    assert counters["covered"] * 2 >= counters["examples"], dict(counters)


def _control_scenario():
    """One unit R with head h and two child units of 6 and 7 persons (each with its own head)."""
    costs, active = {}, {}

    def make(prefix, n, base):
        out = []
        for i in range(n):
            person = "%s%d" % (prefix, i)
            costs[person] = base + i * 3
            active[person] = i % 2
            out.append(person)
        return out

    h = make("h", 1, 7)
    a = make("a", 6, 11)
    b = make("b", 7, 5)
    units = [("R", None, h, h[0]), ("A", "R", a, a[0]), ("B", "R", b, b[0])]
    return unit_tree(units, 5, costs, active)


def test_done_check_G11_negative_control_block_rule_disabled_leaks_the_head():
    scn = _control_scenario()
    state_off, active_off = solve(scn, disable_block_rule=True)
    assert state_off == {}  # all cells published
    leaks = attack_problems(scn, state_off, active_off)
    assert leaks != []
    assert any(kind == "b" and who == ["h0"] for kind, _, known, who in leaks if not known)
    # with the rule on, the same cells leak nothing
    state_on, active_on = solve(scn)
    assert state_on != {}
    assert attack_problems(scn, state_on, active_on) == []


# ---- G22 -----------------------------------------------------------------------------------------


def _worked_example(joiner):
    h = ["h"]
    a = ["a%d" % i for i in range(6)]
    b = ["b%d" % i for i in range(2)]
    c = ["c%d" % i for i in range(7 + (1 if joiner else 0))]
    everyone = h + a + b + c
    cells = [
        ("org:", "", everyone, []),
        ("unit:R", "org:", everyone, h),
        ("unit:A", "unit:R", a, h),
        ("unit:B", "unit:R", b, h),
        ("unit:C", "unit:R", c, h),
    ]
    return cells


def test_done_check_G22_worked_example_states_and_reasons():
    from meterline_gold import run

    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE cells (month VARCHAR, cell_id VARCHAR, parent_cell_id VARCHAR, persons_list VARCHAR[], "
        "receivers_list VARCHAR[])"
    )
    for month, joiner in (("2026-04", False), ("2026-05", True)):
        for cell_id, parent, persons, receivers in _worked_example(joiner):
            con.execute("INSERT INTO cells VALUES (?, ?, ?, ?, ?)", [month, cell_id, parent, persons, receivers])
    state = run.run_privacy(con, 5)
    expected = {
        ("2026-04", "unit:A"): "secondary", ("2026-04", "unit:B"): "k",
        ("2026-05", "unit:A"): "secondary", ("2026-05", "unit:B"): "k",
    }
    assert state == expected
    # negative control: another expectation does not match
    wrong = dict(expected)
    wrong[("2026-05", "unit:A")] = "k"
    assert state != wrong
    # the published cells are exactly org, R and C in both months
    rows = con.execute("SELECT month, cell_id, suppressed FROM cell_state ORDER BY month, cell_id").fetchall()
    published = sorted((m, c) for m, c, s in rows if not s)
    assert published == [(m, c) for m in ("2026-04", "2026-05") for c in ("org:", "unit:C", "unit:R")]


def test_done_check_G11_head_cannot_subtract_into_a_suppressed_child():
    """Found by the phase 2 review: a root whose direct members are all receivers has an unknown block that is
    exactly one suppressed child's uncovered part, and the head recovers that child's total from root minus own."""
    costs, active = {}, {}

    def make(prefix, n):
        out = []
        for i in range(n):
            person = "%s%d" % (prefix, i)
            costs[person] = 3 + 4 * i
            active[person] = i % 2
            out.append(person)
        return out

    h, c, d = make("h", 1), make("c", 5), make("d", 6)
    units = [("R", None, h, h[0]), ("C", "R", c, c[0]), ("D", None, d, d[0])]
    scn = unit_tree(units, 5, costs, active)
    state, active_pub = solve(scn)
    assert attack_problems(scn, state, active_pub) == []
