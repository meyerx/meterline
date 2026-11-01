"""F-28: the small gaps between the specs and gold, and between records.md and the connectors.

A01 over every dataset key is in test_review_f37_silver_and_people.py (``test_unique_keys_all_datasets``).
"""

from pathlib import Path

import pytest

from gold_helpers import KEY, REPO, copy_fixture_input, edit_policy, read_dicts

RECORDS_MD = REPO / "docs" / "records.md"


def _build(tmp_path, top_up):
    import meterline_gold

    inp = copy_fixture_input(tmp_path / "in")

    def edit(policy):
        for scenario in policy["scenarios"]:
            scenario["top_up_amount"] = top_up

    edit_policy(inp["policy"], edit)
    return meterline_gold.build(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                                invoice=inp["invoice"], key=KEY, out=tmp_path / "gold")


def test_top_up_amount_zero_gives_null_top_ups_everywhere(tmp_path):
    """Change-list finding 25: a top_up_amount of "0" gives null top_ups and top_up_total, also where the excess is 0."""
    out = _build(tmp_path, "0")
    persons = read_dicts(out / "central" / "scenario_persons.csv")
    units = read_dicts(out / "central" / "scenario_units.csv")
    assert persons and units
    assert any(r["excess"] not in ("", "0.00") for r in persons), "the fixture has no person beyond a cap"
    for r in persons + units:
        assert r["top_ups"] == "" and r["top_up_total"] == "", r
    for r in read_dicts(out / "central" / "scenario_summary.csv"):
        assert r["persons_top_up_total"] == "" and r["units_top_up_total"] == "", r


def test_top_up_amount_zero_control_a_real_top_up_still_counts(tmp_path):
    out = _build(tmp_path, "4.56")
    persons = read_dicts(out / "central" / "scenario_persons.csv")
    assert any(r["top_ups"] not in ("", "0") for r in persons)
    assert all(r["top_ups"] != "" for r in persons)
    assert any(r["persons_top_up_total"] != "" for r in read_dicts(out / "central" / "scenario_summary.csv"))


def test_records_md_has_a_litellm_section_like_the_copilot_one():
    text = RECORDS_MD.read_text(encoding="utf-8")
    assert "## LiteLLM sources" in text
    section = text.split("## LiteLLM sources", 1)[1].split("\n## ", 1)[0]
    for word in ("proxy_user", "unassigned", "end_user", "OrgTotal", "spend", "estimate", "prompt_tokens",
                 "cache_read_input_tokens", "completion_tokens", "api_requests"):
        assert word in section, word
    assert "its mapping is in [its README]" not in text


def test_records_md_names_the_two_import_routes_of_the_core_package():
    text = RECORDS_MD.read_text(encoding="utf-8")
    assert "meterline.records" in text and "meterline.writers" in text


def test_records_md_says_why_the_cache_lifetime_fields_are_core():
    text = RECORDS_MD.read_text(encoding="utf-8")
    assert "cache lifetime" in text.lower() and "any provider" in text
