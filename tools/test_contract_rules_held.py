"""E3: each rule the connector contract states in prose, outside the kit rules R01 to R16, names the kit check or the test
that holds it, on a line that starts "Held by:". A kit check is a rule id (`R08`); a test is `path/to/test_file.py::test_name`
and must exist in the tree. A rule that nothing holds says so in plain words and is "reviewed in the pull request".

Run: python3 -m pytest -q tools/test_contract_rules_held.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import ROOT  # noqa: E402

from meterline.conformance import RULES  # noqa: E402

CONTRACT = os.path.join(ROOT, "docs", "reference", "connector-contract.md")
# the sections that state a rule in prose, in the order of the page
PROSE_RULES = (
    "The account label when a response has none",
    "Windows",
    "Raw page naming for a paged endpoint",
    "Shape files",
    "Checking a fake against the shape files",
    "Where other providers' mappings live",
)
REVIEWED = "reviewed in the pull request"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section(page, heading):
    match = re.search(r"^## %s\s*$(.*?)(?=^## |\Z)" % re.escape(heading), page, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def held_by_lines(text):
    """The paragraphs of a section that start "Held by:"."""
    return re.findall(r"^Held by:(.*?)(?=\n\s*\n|\Z)", text, re.M | re.S)


def holder_problems(text, root=ROOT, rule_ids=None):
    """What is wrong with the "Held by:" lines of a section: none, a rule id or test that does not exist."""
    ids = {rule.id for rule in RULES} if rule_ids is None else rule_ids
    lines = held_by_lines(text)
    if not lines:
        return ["no line starting 'Held by:'"]
    problems, valid = [], 0
    for line in lines:
        refs = re.findall(r"`([^`]+)`", line)
        for ref in refs:
            if re.fullmatch(r"R\d\d", ref):
                valid += ref in ids
                if ref not in ids:
                    problems.append("%s is not a kit rule" % ref)
            elif ".py" in ref:
                path, _, name = ref.partition("::")
                full = os.path.join(root, path)
                if not os.path.isfile(full):
                    problems.append("%s does not exist" % path)
                elif name and not re.search(r"^\s*def %s\(" % re.escape(name), read(full), re.M):
                    problems.append("%s has no test %s" % (path, name))
                else:
                    valid += 1
        if REVIEWED in line:
            valid += 1
    return problems or ([] if valid else ["'Held by:' names no kit rule and no test"])


def test_each_prose_rule_of_the_contract_names_what_holds_it():
    page = read(CONTRACT)
    for heading in PROSE_RULES:
        assert holder_problems(section(page, heading)) == [], heading


def test_the_prose_rules_come_in_the_order_the_test_lists_them():
    page = read(CONTRACT)
    headings = re.findall(r"^## (.+?)\s*$", page, re.M)
    positions = [headings.index(h) for h in PROSE_RULES]
    assert positions == sorted(positions)


def test_the_kit_rules_are_still_the_only_r_sections():
    page = read(CONTRACT)
    assert re.findall(r"^### (R\d\d)$", page, re.M) == [rule.id for rule in RULES]


# ---- negative controls: the check sees a planted defect ----


def test_a_section_without_a_held_by_line_is_reported():
    assert holder_problems("Some rule.\n\nNothing holds it.\n") == ["no line starting 'Held by:'"]


def test_a_held_by_line_that_names_nothing_is_reported():
    assert holder_problems("Held by: the reviewer, probably.\n") == ["'Held by:' names no kit rule and no test"]


def test_a_rule_id_that_is_not_in_the_kit_is_reported():
    assert holder_problems("Held by: `R98`.\n") == ["R98 is not a kit rule"]
    assert holder_problems("Held by: `R08`.\n") == []


def test_a_test_file_that_does_not_exist_is_reported(tmp_path):
    (tmp_path / "test_here.py").write_text("def test_there():\n    pass\n", encoding="utf-8")
    assert holder_problems("Held by: `test_gone.py::test_there`.\n", root=str(tmp_path)) == ["test_gone.py does not exist"]
    assert holder_problems("Held by: `test_here.py::test_missing`.\n", root=str(tmp_path)) == ["test_here.py has no test test_missing"]
    assert holder_problems("Held by: `test_here.py::test_there`.\n", root=str(tmp_path)) == []
    assert holder_problems("Held by: `test_here.py`.\n", root=str(tmp_path)) == []


def test_a_rule_that_nothing_holds_may_say_so():
    assert holder_problems("Held by: nothing; it is %s.\n" % REVIEWED) == []


def test_a_held_by_line_may_run_over_several_lines():
    text = "Held by: `R08` and\n`R06`.\n\nNext paragraph.\n"
    assert holder_problems(text) == []
