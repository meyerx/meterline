"""A-2 of the v0.3 hardening spec: a proxy's calls that a provider's own connector also reports are marked, not added twice.

The hand-built fixture has an Anthropic source and a LiteLLM source (three proxy records, all of model-y-standard). A copy of
its silver routes some of the proxy's records to ``anthropic`` (``model_provider``); the build must carry that column to
``person_day``, put the routed part of the proxy's cost on its ``costs_month`` row as ``overlap_cost_ref``, and write one
alert. Every figure is read back from the files the build wrote."""
import json
from decimal import Decimal

import pytest

from gold_helpers import KEY, copy_fixture_input, read_dicts

USAGE = "records/usage/20260610T062000Z.jsonl"  # the three LiteLLM records of the fixture


def _route(inp, routed, indexes):
    path = inp["silver"] / USAGE
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["provider"] == "litellm"
        if i in indexes:
            record["model_provider"] = routed
        out.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _build(tmp_path, routed, indexes):
    import meterline_gold

    inp = copy_fixture_input(tmp_path / "in")
    _route(inp, routed, indexes)
    out = tmp_path / "out"
    meterline_gold.build(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                         invoice=inp["invoice"], key=KEY, out=out)
    return out


def _costs(out, audience="central"):
    return {r["provider"]: r for r in read_dicts(out / audience / "costs_month.csv")}


@pytest.fixture(scope="module")
def routed(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("routed"), "anthropic", {0, 1})


def test_person_day_carries_model_provider_as_its_last_column(routed):
    rows = read_dicts(routed / "central" / "person_day.csv")
    header = list(rows[0])
    assert header[-1] == "model_provider" and header[-2] == "source_records"
    by_provider = {}
    for r in rows:
        by_provider.setdefault((r["provider"], r["model_provider"]), 0)
        by_provider[(r["provider"], r["model_provider"])] += 1
    assert by_provider[("litellm", "anthropic")] == 2 and by_provider[("litellm", "")] == 1
    assert read_dicts(routed / "person" / sorted(p.name for p in (routed / "person").iterdir())[0] / "person_day.csv") is not None


def test_the_overlap_is_the_routed_part_of_the_proxy_cost_to_the_cent(routed):
    person_day = read_dicts(routed / "central" / "person_day.csv")
    part = sum(Decimal(r["cost_ref"]) for r in person_day if r["provider"] == "litellm" and r["model_provider"] == "anthropic")
    whole = sum(Decimal(r["cost_ref"]) for r in person_day if r["provider"] == "litellm")
    assert Decimal("0") < part < whole
    costs = _costs(routed)
    assert Decimal(costs["litellm"]["overlap_cost_ref"]) == part
    assert Decimal(costs["litellm"]["usage_cost_ref"]) == whole  # the total itself is unchanged: the column marks, it does not subtract
    assert costs["anthropic"]["overlap_cost_ref"] == "0" or Decimal(costs["anthropic"]["overlap_cost_ref"]) == 0
    assert Decimal(costs["github_copilot"]["overlap_cost_ref"]) == 0


def test_the_column_is_appended_after_the_existing_ones(routed):
    header = list(read_dicts(routed / "central" / "costs_month.csv")[0])
    assert header[header.index("invoice_usage") + 1] == "overlap_cost_ref"


def test_one_alert_names_the_proxy_the_provider_the_month_and_the_amount(routed):
    events = [e for e in read_dicts(routed / "central" / "alert_events.csv") if e["event_type"] == "proxy_overlap"]
    assert len(events) == 1
    event = events[0]
    assert (event["subject_kind"], event["subject_key"]) == ("proxy", "litellm/anthropic")
    assert event["day"].startswith("2026-05")
    assert Decimal(event["value"]) == Decimal(_costs(routed)["litellm"]["overlap_cost_ref"])


def test_exec_form_carries_the_overlap_and_withholds_it_with_the_row(routed):
    exec_costs = _costs(routed, "exec")
    central = _costs(routed)
    for provider, row in exec_costs.items():
        if row["hidden"] == "true":
            assert row["overlap_cost_ref"] == "", provider
        else:
            assert Decimal(row["overlap_cost_ref"]) == Decimal(central[provider]["overlap_cost_ref"]), provider
    assert any(r["hidden"] == "true" for r in exec_costs.values()), "the fixture is meant to hold one hidden row"


def test_a_proxy_routing_to_a_provider_with_no_source_has_no_overlap(tmp_path):
    out = _build(tmp_path, "model-host-without-a-connector", {0, 1, 2})
    assert all(Decimal(r["overlap_cost_ref"]) == 0 for r in _costs(out).values())
    assert not [e for e in read_dicts(out / "central" / "alert_events.csv") if e["event_type"] == "proxy_overlap"]


def test_a_record_routed_to_its_own_provider_is_no_overlap(tmp_path):
    out = _build(tmp_path, "litellm", {0, 1, 2})
    assert all(Decimal(r["overlap_cost_ref"]) == 0 for r in _costs(out).values())


def test_no_routing_at_all_writes_no_alert(tmp_path):
    out = _build(tmp_path, None, set())
    assert not [e for e in read_dicts(out / "central" / "alert_events.csv") if e["event_type"] == "proxy_overlap"]
