"""F-16: the policy's extension points are exercised, and a second example shows an attribute tree.

``bands.metric: tokens``, the rule condition ``breaker_events_at_least`` and the grid attributes ``cost_center`` and
``population`` were allowed by the schema and never run by a test.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from gold_helpers import (KEY, copy_fixture_input, edit_policy, money_rule_violations, policy_money_amounts,
                          read_dicts)


def _build(inp, out):
    import meterline_gold

    return meterline_gold.build(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                                invoice=inp["invoice"], key=KEY, out=out)


# ---- bands.metric: tokens ----------------------------------------------------------------------------------

TOKEN_BANDS = [("none", "0"), ("light", "100000"), ("regular", "300000"), ("heavy", "1000000")]


def _band(value, thresholds):
    chosen = None
    for name, lower in thresholds:
        if Decimal(lower) <= value:
            chosen = name
    return chosen


def test_config_branch_bands_metric_tokens(tmp_path, fixture_out):
    inp = copy_fixture_input(tmp_path / "in")

    def tokens(policy):
        policy["bands"] = {"metric": "tokens",
                           "thresholds": [{"band": b, "from": f} for b, f in TOKEN_BANDS]}

    edit_policy(inp["policy"], tokens)
    out = _build(inp, tmp_path / "gold")
    rows = read_dicts(out / "central" / "person_month.csv")
    assert rows
    for r in rows:
        total = Decimal(r["tokens_input_total"] or 0) + Decimal(r["tokens_output"] or 0)
        assert r["intensity_band"] == _band(total, TOKEN_BANDS), r["person_key"]
    # the metric matters: against the cost bands of the fixture, at least one person falls in another band
    cost_band = {r["person_key"]: r["intensity_band"] for r in read_dicts(fixture_out / "central" / "person_month.csv")}
    assert any(cost_band[r["person_key"]] != r["intensity_band"] for r in rows)
    assert len({r["intensity_band"] for r in rows}) >= 3


def test_config_branch_bands_metric_tokens_thresholds_must_be_whole_numbers(tmp_path):
    from meterline_gold import InputError

    inp = copy_fixture_input(tmp_path / "in")
    edit_policy(inp["policy"], lambda p: p.update(bands={"metric": "tokens", "thresholds": [
        {"band": "none", "from": "0"}, {"band": "light", "from": "1000.5"}]}))
    with pytest.raises(InputError):
        _build(inp, tmp_path / "gold")


# ---- breaker_events_at_least -------------------------------------------------------------------------------


def test_config_branch_breaker_events_at_least(tmp_path, fixture_out):
    inp = copy_fixture_input(tmp_path / "in")

    def rules(policy):
        policy["named_exceptions"] += [
            {"id": "BR1", "audience": "central", "condition": "breaker_events_at_least", "threshold": "1"},
            {"id": "BR2", "audience": "manager", "condition": "breaker_events_at_least", "threshold": "1"},
            {"id": "BR3", "audience": "central", "condition": "breaker_events_at_least", "threshold": "2"},
        ]

    edit_policy(inp["policy"], rules)
    out = _build(inp, tmp_path / "gold")
    breakers = {}
    for r in read_dicts(out / "central" / "alert_events.csv"):
        if r["event_type"] == "breaker":
            month = r["day"][:7]
            breakers[(month, r["subject_key"])] = breakers.get((month, r["subject_key"]), 0) + 1
    assert breakers, "the fixture has no breaker event"
    named = read_dicts(out / "central" / "named_exceptions.csv")

    def got(rule):
        return {(r["month"], r["person_key"]): int(r["value"]) for r in named if r["rule_id"] == rule}

    assert got("BR1") == breakers
    assert got("BR2") == breakers
    assert got("BR3") == {k: n for k, n in breakers.items() if n >= 2}
    # BR2 is a manager rule: the managers' folders list exactly those persons, under their own manager key
    person_manager = {r["person_key"]: r["manager_key"] for r in read_dicts(out / "central" / "person_month.csv")}
    listed = {}
    for folder in sorted((out / "manager").iterdir()):
        for r in read_dicts(folder / "named_exceptions.csv"):
            if r["rule_id"] == "BR2":
                assert person_manager[r["person_key"]] == folder.name
                listed[(r["month"], r["person_key"])] = int(r["value"])
    assert listed == {k: n for k, n in breakers.items() if person_manager.get(k[1])}
    assert not any(r["rule_id"] == "BR1" for f in (out / "manager").iterdir()
                   for r in read_dicts(f / "named_exceptions.csv")), "a central rule reached a manager folder"


# ---- cost_center and population as grid attributes ------------------------------------------------------------


def _cells_of_a_chain(org_dir, attributes):
    """The cell ids that a tree over ``attributes`` gives for the people of an organisation folder."""
    people = read_dicts(Path(org_dir) / "people.csv")
    cells = {"org:"}
    for r in people:
        parts = []
        for name in attributes:
            parts.append("%s=%s" % (name, r[name] or "(none)"))
            cells.add("attr:" + "|".join(parts))
    return cells


@pytest.mark.parametrize("attributes", [["population", "cost_center"], ["cost_center", "population"],
                                        ["cost_center"], ["population"]])
def test_config_branch_grid_attributes_cost_center_and_population(tmp_path, attributes):
    inp = copy_fixture_input(tmp_path / "in")
    edit_policy(inp["policy"], lambda p: p["privacy"].update(tree={"kind": "attributes", "attributes": attributes}))
    out = _build(inp, tmp_path / "gold")
    rows = read_dicts(out / "central" / "groups_month.csv")
    assert {r["cell_id"] for r in rows} == _cells_of_a_chain(inp["org"], attributes)
    by_id = {r["cell_id"]: r for r in rows}
    for r in rows:
        if r["cell_id"] != "org:":
            assert r["cell_kind"] == "attr"
            assert by_id[r["parent_cell_id"]]["depth"] == str(int(r["depth"]) - 1)
    # the chain's deepest level is the length of the attribute list
    assert max(int(r["depth"]) for r in rows) == len(attributes)


def test_config_branch_grid_attributes_a_tree_has_no_manager_cells(tmp_path):
    """An attribute tree has no unit cells, so a manager folder holds named_exceptions only."""
    inp = copy_fixture_input(tmp_path / "in")
    edit_policy(inp["policy"], lambda p: p["privacy"].update(
        tree={"kind": "attributes", "attributes": ["population", "cost_center"]}))
    out = _build(inp, tmp_path / "gold")
    for folder in (out / "manager").iterdir():
        assert read_dicts(folder / "groups_month.csv") == []


# ---- the second example policy ---------------------------------------------------------------------------------


def _attributes_example():
    import meterline_gold

    return Path(meterline_gold.__file__).resolve().parent / "examples" / "policy.attributes.example.yaml"


def test_config_branch_second_example_policy_uses_an_attribute_tree_with_several_dimensions():
    from meterline_gold import params

    policy = params.load_policy(_attributes_example())
    assert policy.privacy.tree.kind == "attributes"
    assert len(policy.privacy.tree.attributes) >= 3
    assert any(a.startswith("attrs.") for a in policy.privacy.tree.attributes)
    assert policy.bands.metric == "tokens"
    assert {r.condition for r in policy.named_exceptions} >= {"breaker_events_at_least"}


def test_config_branch_second_example_policy_is_visibly_synthetic():
    import yaml

    obj = yaml.safe_load(_attributes_example().read_text(encoding="utf-8"))
    assert money_rule_violations(policy_money_amounts(obj)) == []
    assert obj["currency"] == "USD"
    assert "invented" in obj["label"].lower() or "made up" in obj["label"].lower()


def test_config_branch_second_example_policy_builds_on_the_tiny_sim(sim_runs, example_files, tmp_path):
    import meterline_gold

    _, prices = example_files
    run = sim_runs["tiny"]
    out = meterline_gold.build(silver=run.silver, org=run.org, policy=_attributes_example(), prices=prices, key=KEY,
                               out=tmp_path / "gold")
    cells = {r["cell_id"] for r in read_dicts(out / "central" / "groups_month.csv")}
    assert any(c.count("|") == 2 for c in cells), "no cell three attributes deep"
    assert {r["cell_kind"] for r in read_dicts(out / "central" / "groups_month.csv")} == {"org", "attr"}
    assert (out / "manifest.json").is_file()
