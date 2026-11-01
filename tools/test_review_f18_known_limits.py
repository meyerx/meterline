"""F-18: every package README has a Known limits section linked from its Status, the root's out-of-scope list
says which package it is about, and the limits the review found left open are written down.

Run: python3 -m pytest -q tools/test_review_f18_known_limits.py
"""
import glob
import os
import re

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import connectors, packages  # noqa: E402
from wiring import BY_ID  # noqa: E402  the places a package must be named in, shared with tools/check_new_connector.py

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_READMES = sorted(glob.glob(os.path.join(ROOT, "packages", "*", "README.md")))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^## %s\s*$(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def readme(pkg):
    return read(os.path.join(ROOT, "packages", pkg, "README.md"))


def test_every_package_has_known_limits_linked_from_its_status():
    assert len(PACKAGE_READMES) == len(packages()), "a package has no README"
    for path in PACKAGE_READMES:
        text = read(path)
        rel = os.path.relpath(path, ROOT)
        assert re.search(r"^## Known limits\s*$", text, re.M), rel
        status = re.search(r"^\*\*Status:\*\*.*$", text, re.M)
        assert status, "%s has no Status line" % rel
        assert "(#known-limits)" in status.group(0), "%s: Status does not link Known limits" % rel


def test_root_out_of_scope_names_the_package_each_item_concerns():
    body = section(read(os.path.join(ROOT, "README.md")), "Known limits")
    # gold delivers what the old list called out of scope: the list must say so, not contradict it
    for delivered in ("identity", "reference cost", "alert"):
        line = [x for x in body.splitlines() if delivered in x.lower()]
        assert line and all("meterline-gold" in x or "gold" in x for x in line), delivered
    assert "connectors" in body


def test_left_open_findings_are_in_a_limits_section():
    core = section(readme("meterline"), "Known limits")
    assert "decimal128" in core and '"false"' in core and "deletion marker" in core
    for pkg in connectors():
        assert "`pending`" in section(readme(pkg), "Known limits"), pkg
        assert BY_ID["known-limits-pending"].missing(ROOT, pkg) == [], pkg
    assert "vanishes" in section(readme("meterline-litellm"), "Known limits")
    gold = section(readme("meterline-gold"), "Known limits")
    assert ".tmp" in gold and "hashing key" in gold and "two builds" in gold.lower()


def test_no_known_limit_contradicts_what_ships():
    # the seat summaries are now cut into ranges of at most 366 days (fetch_summaries_windows)
    assert "does not split a longer range" not in readme("meterline-anthropic")
    assert "fetch_summaries_windows" in read(os.path.join(ROOT, "packages", "meterline-anthropic", "src",
                                                          "meterline_anthropic", "fetch.py"))
