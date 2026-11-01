"""F-12: no verification claim stands without its checker and artefact, the three evidence levels are defined once
and every connector's Status uses one of them word for word, and records.md says what validation does not check.

Run: python3 -m pytest -q tools/test_review_f12_evidence.py
"""
import glob
import os
import re

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import connectors  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEVELS = ("built against documented shapes", "verified by a recorded run", "verified live")
CONNECTORS = tuple(connectors())
# the connectors above the first level, with the level each reached (a live run on 2026-10-30)
AT_LEVEL = {"meterline-anthropic-admin": LEVELS[2]}


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def public_docs():
    paths = ["README.md"] + glob.glob(os.path.join(ROOT, "packages", "*", "README.md")) + \
        glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)
    return [os.path.relpath(os.path.join(ROOT, p), ROOT) for p in paths]


def test_no_unbacked_independent_check_claim():
    hits = [p for p in public_docs() if re.search(r"independently checked", read(p), re.I)]
    assert not hits, hits


def test_glossary_defines_the_three_evidence_levels_with_a_criterion_each():
    text = read("docs", "glossary.md")
    match = re.search(r"^### evidence levels\s*$(.*?)(?=^##)", text, re.M | re.S)
    assert match, "the glossary has no 'evidence levels' entry"
    body = match.group(1)
    for n, level in enumerate(LEVELS, 1):
        item = re.search(r"^%d\. \*\*%s\*\*: (.+)$" % (n, re.escape(level)), body, re.M)
        assert item and len(item.group(1).split()) >= 15, level


def _status(text):
    match = re.search(r"^\*\*Status:\*\*.*$", text, re.M)
    assert match, "no Status line"
    return match.group(0)


def test_every_connector_status_names_its_level_and_links_the_definition():
    for pkg in CONNECTORS:
        status = _status(read("packages", pkg, "README.md"))
        named = [lvl for lvl in LEVELS if lvl in status]
        assert named == [AT_LEVEL.get(pkg, LEVELS[0])], (pkg, named)
        assert "glossary.md#evidence-levels" in status, pkg


def test_root_status_names_the_level_and_links_the_definition():
    status = _status(read("README.md"))
    assert LEVELS[0] in status and "docs/glossary.md#evidence-levels" in status


def test_records_md_says_what_validation_does_not_check():
    text = read("docs", "records.md")
    match = re.search(r"^### What validation does not check\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "no 'What validation does not check' list"
    assert len(re.findall(r"^- ", match.group(1), re.M)) >= 4
