"""F-35: each structural choice of the record format has a decision record with the alternatives weighed, the
consequences including the downsides, and a date; and the "never" rules of the README and records.md say why.

Run: python3 -m pytest -q tools/test_review_f35_decisions.py
"""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISIONS = os.path.join(ROOT, "docs", "decisions")
TOPICS = {"decimal": "Decimal", "token": "token buckets", "grain": "grain", "null": "null", "zeros": "zeros",
          "jsonl": "JSON lines", "record-kinds": "record kinds"}
REASON = re.compile(r"\bbecause\b|\bso (that )?\b|decisions/0\d{3}|: (it|they|a|an|the) ", re.I)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^## %s\s*$(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def records():
    return sorted(glob.glob(os.path.join(DECISIONS, "[0-9][0-9][0-9][0-9]-*.md")))


def test_one_record_per_structural_choice():
    names = [os.path.basename(p) for p in records()]
    assert len(names) >= 7, names
    for key in TOPICS:
        assert [n for n in names if key in n], key


def test_every_record_has_alternatives_consequences_with_downsides_and_a_date():
    for path in records():
        text = read(path)
        name = os.path.basename(path)
        assert re.search(r"^- Decided: 2026-\d\d-\d\d", text, re.M), name
        alternatives = section(text, "Alternatives considered")
        assert len(re.findall(r"^- \*\*", alternatives, re.M)) >= 2, name
        assert "Rejected" in alternatives and "chosen" in alternatives, name
        section(text, "Decision")
        assert "Downside" in section(text, "Consequences"), name
        assert section(text, "Sources").strip(), name


def test_records_md_conventions_give_a_reason_for_each_never_rule():
    conventions = section(read(os.path.join(ROOT, "docs", "records.md")), "Conventions")
    rows = [r for r in conventions.splitlines() if r.startswith("|") and re.search(r"\b[Nn]ever\b", r)]
    assert rows
    bare = [r[:60] for r in rows if not REASON.search(r)]
    assert not bare, bare
    for topic, number in (("Money", "0001"), ("grain", "0003"), ("Null", "0004")):
        row = [r for r in conventions.splitlines() if r.startswith("| %s" % topic) or r.startswith("| `%s`" % topic)]
        assert row and "decisions/%s" % number in row[0], topic


def test_readme_development_rules_give_their_reasons():
    body = section(read(os.path.join(ROOT, "README.md")), "Development")
    bullets = [b for b in body.splitlines() if b.startswith("- ") and re.search(r"\b[Nn]ever\b", b)]
    assert bullets
    bare = [b[:60] for b in bullets if not REASON.search(b)]
    assert not bare, bare
