"""F-32: the README says who the tool is for, the problem it solves and why not a provider dashboard or a script.

Structure checks: they cannot judge the writing, but they fail when a part goes missing or the purpose sentence
drifts between the README and the package READMEs.

Run: python3 -m pytest -q tools/test_review_f32_front_door.py
"""
import os
import re

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import packages  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = tuple(packages())


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^## %s\s*$(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def purpose_sentence():
    """The first sentence of the README's first paragraph."""
    first = read("README.md").split("\n\n")[1].strip()
    return re.split(r"(?<=\.)\s", first, maxsplit=1)[0]


def test_readme_opens_with_who_and_the_problem_before_the_mechanism():
    sentence = purpose_sentence()
    assert len(sentence.split()) <= 45, sentence
    # a person, then their problem, then what the tool does
    assert re.search(r"\bpeople who\b", sentence), sentence
    assert re.search(r"\b(compare|report|see)\b", sentence), sentence


def test_who_it_is_for_comes_before_the_packages_and_says_for_and_not_for():
    text = read("README.md")
    assert text.index("\n## Who it is for") < text.index("\n## Packages")
    body = section(text, "Who it is for")
    assert "**For:**" in body and "**Not for" in body


def test_who_it_is_for_answers_why_not_a_provider_dashboard_or_a_script():
    body = section(read("README.md"), "Who it is for").lower()
    assert "provider's own dashboard" in body or "provider dashboards" in body
    assert "script" in body


def test_who_it_is_for_states_the_evidence_of_need_maintenance_and_assumptions():
    body = section(read("README.md"), "Who it is for")
    assert re.search(r"\bEvidence of need\b", body)
    assert "untested" in body, "no evidence of outside need exists in the repository: the README must say so"
    assert re.search(r"\bMaintenance\b", body) and "best-effort" in body
    assert re.search(r"\bAssumptions\b", body)


def test_status_starts_with_a_state_not_with_counts():
    text = read("README.md")
    status = re.search(r"^\*\*Status:\*\* (.*)$", text, re.M)
    assert status, "no Status line"
    first = re.split(r"(?<=\.)\s", status.group(1), maxsplit=1)[0]
    assert not re.search(r"\d", first), first
    assert len(first.split()) <= 25, first


def test_every_package_readme_carries_the_same_purpose_sentence():
    sentence = purpose_sentence()
    for pkg in PACKAGES:
        head = "\n".join(read("packages", pkg, "README.md").splitlines()[:12])
        assert sentence in head, pkg
