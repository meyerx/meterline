"""B3-c: two caveats on the cost figures, and the Admin README on the blank reconciliation gap.

The caveats rest on facts of the example price list and of the gold SQL; the first test reads the price list so that the
text cannot outlive the line it warns about. The rest are text checks. A reviewer judges whether a sentence is true.

Run: python3 -m pytest -q tools/test_b3c_caveats.py
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^#{2,3} %s\b.*?$(.*?)(?=^#{2,3} |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '%s'" % heading
    return match.group(1)


EXAMPLE_03 = read("examples", "03-daily-job", "README.md")
COOKBOOK = section(read("docs", "how-to", "finops-kpis.md"), "Billed, consumption and reference cost")
ADMIN = read("packages", "meterline-anthropic-admin", "README.md")


def test_the_example_price_list_keeps_the_provider_wide_anthropic_seat_line_commented_out():
    yaml = pytest.importorskip("yaml")
    base = os.path.join(ROOT, "packages", "meterline-gold", "src", "meterline_gold", "examples", "prices.example.yaml")
    with open(base, encoding="utf-8") as fh:
        text = fh.read()
    assert not [s for s in yaml.safe_load(text)["seats"] if s["provider"] == "anthropic"]  # nothing active
    commented = [line for line in text.splitlines() if line.lstrip().startswith("# - {provider: anthropic")]
    assert len(commented) == 1 and "provider_account_id: null" in commented[0] and 'per_month: "27.00"' in commented[0]
    assert "Commented out on purpose" in text


@pytest.mark.parametrize("name,text", [("example 03 README", EXAMPLE_03), ("cost cookbook", COOKBOOK)])
def test_the_seat_price_caveat_is_where_the_cost_figures_are_explained(name, text):
    assert "27.00" in text and "`provider_account_id`" in text, name
    assert "Console" in text and "seat_cost" in text, name
    assert "remove" in text.lower(), name


@pytest.mark.parametrize("name,text", [("example 03 README", EXAMPLE_03), ("cost cookbook", COOKBOOK)])
def test_the_lower_bound_caveat_is_where_the_cost_figures_are_explained(name, text):
    assert "lower bound" in text and "`usage_cost_ref`" in text, name
    assert "`rows_unpriced`" in text and "`rows_unknown_model`" in text, name
    assert "`total_cost_ref`" in text, name


def test_the_admin_readme_says_why_the_reconciliation_gap_is_blank():
    assert "`ref_vs_source_gap`" in ADMIN
    paragraph = next(p for p in ADMIN.split("\n") if "`ref_vs_source_gap`" in p)
    assert "`actor_cost_source`" in paragraph and "`reconciliation_month`" in paragraph
