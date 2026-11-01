"""F-23: every project term is defined once, in a glossary, and an architecture picture shows the layers.

Run: python3 -m pytest -q tools/test_review_f23_terms.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLOSSARY = os.path.join(ROOT, "docs", "glossary.md")
ARCHITECTURE = os.path.join(ROOT, "docs", "architecture.md")

# the terms the review found used before they were defined, plus the overlapping words it asked to tell apart
TERMS = ("actor", "audience", "batch", "bronze", "cell", "check", "comparison group", "connector", "cost_basis",
         "cost_ref", "done-check", "event", "fixture", "gold", "grain", "ground truth", "hidden", "k", "level",
         "line", "person", "pool", "published", "raw", "record", "scope", "seat", "silver", "suppression", "unit",
         "user", "window")


def read(path):
    assert os.path.isfile(path), "%s does not exist" % os.path.relpath(path, ROOT)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def entries(text):
    """{term: body} of the glossary's `### term` entries."""
    found = {}
    for match in re.finditer(r"^### (.+?)\s*$(.*?)(?=^### |^## |\Z)", text, re.M | re.S):
        term = match.group(1).strip().strip("`")
        assert term not in found, "%s is defined twice" % term
        found[term] = match.group(2)
    return found


def test_glossary_defines_every_project_term_once():
    found = entries(read(GLOSSARY))
    missing = [t for t in TERMS if t not in found]
    assert not missing, missing
    for term, body in found.items():
        assert len(body.split()) >= 8, "%s has no real definition" % term


def test_glossary_tells_the_overlapping_words_apart():
    found = entries(read(GLOSSARY))
    check = found["check"]
    assert "done-check" in check and "validation" in check and "assertion" in check
    for word in ("actor", "user", "person"):
        others = {"actor", "user", "person"} - {word}
        assert all(o in found[word] for o in others), word
    assert "record" in found["line"] and "unit" in found["line"]
    assert "record" in found["event"]
    assert "suppress" in found["hidden"]


def test_architecture_has_one_diagram_from_raw_to_the_audiences():
    text = read(ARCHITECTURE)
    diagrams = re.findall(r"^```mermaid\n(.*?)^```", text, re.M | re.S)
    assert len(diagrams) == 1
    body = diagrams[0].lower()
    for node in ("api", "bronze", "silver", "gold", "central", "exec", "manager", "person"):
        assert node in body, node


def test_readme_links_the_glossary_and_the_architecture_before_it_uses_a_layer_name():
    text = read(os.path.join(ROOT, "README.md"))
    glossary, architecture = text.find("(docs/glossary.md"), text.find("(docs/architecture.md")
    assert glossary >= 0 and architecture >= 0
    assert "silver" in text.lower()
    for term in ("silver", "suppress", "done-check"):
        first = text.lower().find(term)
        if first < 0:
            continue
        assert min(glossary, architecture) < first, "%s is used before the glossary is linked" % term


def test_package_readmes_link_the_glossary():
    for pkg in ("meterline-gold", "meterline-sim"):
        assert "docs/glossary.md" in read(os.path.join(ROOT, "packages", pkg, "README.md")), pkg
