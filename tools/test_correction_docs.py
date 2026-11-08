"""B-3 of the v0.3 hardening spec: erasure and the correction of a delivered month.

The privacy page says that rows are removed by the manual erasure procedure and not by the tool; it has a section for a wrong
attribution or a stricter policy found after a month was delivered; and the accepted-risk sentences read as a decision the
adopter takes and records before go-live, with the re-delivery comparison among the residual risks.

Run: python3 -m pytest -q tools
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


PRIVACY = read("docs", "PRIVACY.md")
THREAT = read("docs", "THREAT-MODEL.md")
HEADING = "Correcting a delivered month or applying a stricter policy"


def correction_section():
    match = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(HEADING), PRIVACY, re.M | re.S)
    assert match, "no section '## %s'" % HEADING
    return match.group(1)


def test_the_section_sits_next_to_erasure_and_names_the_cases():
    headings = re.findall(r"^## (.+)$", PRIVACY, re.M)
    assert abs(headings.index(HEADING) - headings.index("Erasure")) == 1
    text = correction_section()
    for needle in ("wrong attribution", "`k`", "exec depth", "`policy_changed`"):
        assert needle in text, needle


def test_the_section_gives_the_procedure_and_what_to_record():
    text = correction_section()
    for needle in ("stop", "frozen/<month>/", "`frozen.json`", "together", "re-freezes", "latest build", "record", "tell",
                   "compare"):
        assert needle in text.lower() or needle in text, needle
    assert re.search(r"recipients? who kept", text)


def test_the_delivery_rule_says_rows_leave_by_the_manual_procedure_not_by_the_tool():
    rule = [line for line in PRIVACY.splitlines() if line.startswith("A month is refreshed on every published run")]
    assert len(rule) == 1
    assert "the manual erasure procedure removes an erased person's rows" in rule[0]
    assert "an erased person's rows are removed," not in rule[0]


def test_the_accepted_risk_is_a_decision_taken_and_recorded_before_go_live():
    for name, text in (("PRIVACY.md", PRIVACY), ("THREAT-MODEL.md", THREAT)):
        rule = [line for line in text.splitlines() if line.startswith(("A month is refreshed", "**Delivery rule"))]
        assert rule, name
        joined = " ".join(rule)
        assert "decides before go-live" in joined and "records the decision" in joined, name
        assert "The adopter accepts this risk" not in joined, name
        assert "compare it with the version delivered again after a correction" in joined, name
    t6 = next(line for line in THREAT.splitlines() if line.startswith("| T6 |"))
    assert "decides" in t6 and "go-live" in t6 and "re-deliver" in t6
