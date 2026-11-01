"""The threat model (F-46) and the privacy statement (F-39) exist and keep the parts a reviewer needs.

These are structure checks: they cannot judge the content, but they fail when a part goes missing.

Run: python3 -m pytest -q tools/test_security_privacy_docs.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THREAT_MODEL = os.path.join(ROOT, "docs", "THREAT-MODEL.md")

RESPONSES = {"mitigate", "eliminate", "transfer", "accept"}


def read(path):
    assert os.path.isfile(path), "%s does not exist" % os.path.relpath(path, ROOT)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    """The body of the `## heading` (or `### heading`) section, up to the next heading of either level."""
    match = re.search(r"^###? %s\b.*?$(.*?)(?=^###? |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def table_rows(body):
    """Data rows of the first Markdown table in body, as lists of stripped cells."""
    lines = [l for l in body.splitlines() if l.startswith("|")]
    assert len(lines) >= 3, "no table"
    return [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines[2:]]


# ---- threat model (F-46) ----

def test_threat_model_is_dated_tied_to_a_commit_and_names_its_update_triggers():
    text = read(THREAT_MODEL)
    head = text[:1500]
    assert re.search(r"\b20\d\d-\d\d-\d\d\b", head), "no date in the header"
    assert re.search(r"\bcommit `?[0-9a-f]{7,40}\b", head), "no commit in the header"
    assert "## Update triggers" in text


def test_threat_model_draws_the_data_flow_with_element_ids_and_trust_boundaries():
    text = read(THREAT_MODEL)
    diagram = re.search(r"```mermaid\n(.*?)```", text, re.S)
    assert diagram, "no Mermaid data-flow diagram"
    ids = set(re.findall(r"\b([A-Z]{1,2}\d{1,2})\b", diagram.group(1)))
    assert {"E1", "P1", "D1"} <= ids, "element ids missing from the diagram: %s" % sorted(ids)
    boundaries = section(text, "Trust boundaries")
    for phrase in ("provider API", "raw", "bronze", "silver", "gold", "central", "manager", "person", "exec",
                   "dashboard"):
        assert phrase in boundaries, "trust boundaries do not cover %r" % phrase


def test_every_stride_row_names_an_element_of_the_diagram_a_response_and_an_owner():
    text = read(THREAT_MODEL)
    diagram = re.search(r"```mermaid\n(.*?)```", text, re.S).group(1)
    ids = set(re.findall(r"\b([A-Z]{1,2}\d{1,2})\b", diagram))
    rows = table_rows(section(text, "STRIDE"))
    assert len(rows) >= 12
    letters = set()
    for row in rows:
        assert len(row) >= 6, row
        threat_id, element, category, _threat, response, owner = row[:6]
        assert re.fullmatch(r"T\d+", threat_id), row
        assert element.split()[0] in ids, "element %r is not in the diagram" % element
        assert category[:1] in "STRIDE", row
        letters.add(category[:1])
        assert response.lower().split()[0] in RESPONSES, row
        assert owner, row
    assert letters == set("STRIDE"), "STRIDE categories not all covered: %s" % sorted(letters)


def test_threat_model_has_a_bug_bar_an_attack_tree_and_an_assumptions_register():
    text = read(THREAT_MODEL)
    assert len(table_rows(section(text, "Bug bar"))) >= 4
    tree = section(text, "Attack tree")
    assert "exfiltrate the admin key or per-person emails" in tree
    assumptions = section(text, "Assumptions")
    header = [l for l in assumptions.splitlines() if l.startswith("|")][0].lower()
    for column in ("impact if wrong", "who can tell", "follow-up"):
        assert column in header, column
    assert "never been run" in assumptions or "never run" in assumptions, "the never-run-live shapes are missing"


# ---- privacy statement (F-39) ----

PRIVACY = os.path.join(ROOT, "docs", "PRIVACY.md")


def test_privacy_doc_has_retention_and_a_per_layer_erasure_procedure():
    text = read(PRIVACY)
    retention = section(text, "Retention")
    erasure = section(text, "Erasure")
    for layer in ("raw/", "records/", "runs/", "state.json", "central/", "delivery/", "manager/", "person/", "exec/",
                  "organisation files"):
        assert layer.lower() in retention.lower(), "retention does not cover %s" % layer
    for layer in ("raw/", "records/", "runs/", "state.json", "rebuild", "identities.csv", "METERLINE_GOLD_KEY"):
        assert layer in erasure, "erasure does not cover %s" % layer


def test_privacy_doc_names_roles_records_of_processing_dpia_monitoring_and_transfers():
    text = read(PRIVACY)
    for phrase in ("controller", "processor", "GDPR art. 30", "nLPD art. 12", "DPIA", "employee-monitoring",
                   "employee representatives", "transfer", "why `actor_email` is kept"):
        assert phrase.lower() in text.lower(), phrase
    never = section(text, "Fields that must never be added")
    assert len([l for l in never.splitlines() if l.startswith("- ")]) >= 5


def test_readme_links_the_privacy_and_threat_model_docs():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    assert "(docs/PRIVACY.md)" in text
    assert "(docs/THREAT-MODEL.md)" in text


# ---- static analysis of the package sources (F-33) ----

def _ruff():
    import shutil
    import sys
    local = os.path.join(os.path.dirname(sys.executable), "ruff")
    return local if os.path.exists(local) else shutil.which("ruff")


def test_security_lint_of_package_sources_is_clean_or_justified():
    """ruff's bandit rules (S) find nothing in packages/*/src that is not suppressed on its line with a reason."""
    import glob
    import subprocess
    ruff = _ruff()
    if not ruff:
        import pytest
        pytest.skip("ruff is not installed (the CI lint job installs it)")
    sources = sorted(glob.glob(os.path.join(ROOT, "packages", "*", "src")))
    done = subprocess.run([ruff, "check", "--isolated", "--select", "S", "--output-format", "concise", *sources],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-2000:]


def test_every_security_suppression_states_its_reason():
    import glob
    bad = []
    for path in sorted(glob.glob(os.path.join(ROOT, "packages", "*", "src", "**", "*.py"), recursive=True)):
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if re.search(r"#\s*nosec|noqa:\s*S\d", line) and not re.search(r"(nosec|noqa)[^#]*? - \S", line):
                    bad.append("%s:%d" % (os.path.relpath(path, ROOT), n))
    assert not bad, bad
