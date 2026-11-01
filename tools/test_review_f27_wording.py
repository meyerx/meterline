"""F-27: a short style sheet fixes the wording conventions, and the wording findings against the accepted (frozen)
specs are recorded in their change lists, with the reading the code implements.

Run: python3 -m pytest -q tools/test_review_f27_wording.py
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import in_working_repo  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    path = os.path.join(ROOT, *parts)
    assert os.path.isfile(path), "%s does not exist" % os.path.relpath(path, ROOT)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^##+ %s\b.*?$(.*?)(?=^##+ |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '%s'" % heading
    return match.group(1)


def test_style_sheet_covers_the_six_conventions():
    text = read("docs", "dev", "style.md")
    modal = section(text, "Modal verbs")
    for word in ("**must**", "**must not**", "**may**", "**can**"):
        assert word in modal, word
    assert "restrictive" in section(text, "That and which")
    assert "and/or" in section(text, "And, or")
    assert "one per line" in section(text, "Rules with several conditions")
    windows = section(text, "Time windows")
    assert "calendar" in windows and "rolling" in windows and "UTC" in windows
    defined = section(text, "Words with a defined meaning")
    for word in ("published", "hidden", "suppressed"):
        assert word in defined, word


def test_style_sheet_lists_the_frozen_spec_findings_with_the_reading_the_code_implements():
    body = section(read("docs", "dev", "style.md"), "Wording findings in the accepted specs")
    for where in ("gold v1.2", "v2 v1.0", "sim v1.0", "4.3", "7.3", "7.4", "8.2", "6.3"):
        assert where in body, where


def _wording_entry(text):
    match = re.search(r"^##+ [^\n]*[Ww]ording[^\n]*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "no wording entry"
    return match.group(1)


def test_gold_change_list_numbers_the_multi_condition_rules_one_per_line():
    body = _wording_entry(read("docs", "dev", "change-list-meterline-gold.md"))
    for rule in ("7.4 b2", "8.2", "8.1"):
        assert rule in body, rule
    assert len(re.findall(r"^\s*[123]\. ", body, re.M)) >= 6, "the three-case rules are not numbered one per line"
    assert "published" in body and "must be used with" in body


def test_v2_change_list_says_calendar_or_rolling_for_each_history_window():
    body = _wording_entry(read("docs", "dev", "change-list-meterline-v2.md"))
    assert "rolling" in body and "calendar months" in body and "REPORT_DAYS_BACK" in body


def test_sim_change_list_settles_the_s03_scope_sentence():
    if not os.path.exists(os.path.join(ROOT, "docs", "dev", "change-list-meterline-sim.md")) and not in_working_repo():
        pytest.skip("the sim change list is not part of the public repository: no public document links it")
    body = _wording_entry(read("docs", "dev", "change-list-meterline-sim.md"))
    assert "S03" in body and "org_totals" in body and "any model" in body


def test_copilot_readme_says_calendar_or_rolling_for_its_history_windows():
    text = read("packages", "meterline-copilot", "README.md")
    commands = section(text, "Commands")
    assert "rolling" in commands and "calendar months" in commands
