"""F-25: the connector READMEs follow one outline, each says what to do when a live response differs, the
LiteLLM and Copilot field mappings are complete, and every response-shape folder states its provenance.

Run: python3 -m pytest -q tools/test_review_f25_provider_docs.py
"""
import glob
import os
import re

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import connectors  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONNECTORS = tuple(connectors())
OUTLINE = ("Before you run it", "Commands", "How it works", "Files under the data directory", "Record mapping",
           "Response shapes", "Known limits", "Public interface", "Tests")
SHAPES = os.path.join(ROOT, "docs", "api-shapes")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    match = re.search(r"^## %s\b.*?$(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def test_connector_readmes_follow_one_outline():
    for pkg in CONNECTORS:
        headings = re.findall(r"^## (.+?)\s*$", read("packages", pkg, "README.md"), re.M)
        found = [h for h in headings if h in OUTLINE]
        assert found == list(OUTLINE), (pkg, found)


def test_every_connector_says_what_to_do_when_a_live_response_differs():
    for pkg in CONNECTORS:
        body = section(read("packages", pkg, "README.md"), "Response shapes")
        assert "When a live response differs" in body, pkg
        steps = re.findall(r"^\d+\. ", body, re.M)
        assert len(steps) >= 3, pkg
        assert "synthetic" in body and "never commit a real response" in body.lower(), pkg


def test_header_versioned_apis_say_what_to_do_on_a_new_version():
    for pkg, version in (("meterline-anthropic", "2023-06-01"), ("meterline-copilot", "2026-03-10")):
        body = section(read("packages", pkg, "README.md"), "Response shapes")
        assert version in body and "new API version" in body, pkg


def test_litellm_record_mapping_names_every_key_and_constant_field():
    body = section(read("packages", "meterline-litellm", "README.md"), "Record mapping")
    for field in ("actor_id", "actor_deleted", "cost_only", "grain", "scope", "product", "period_start", "provider_account_id",
                  "data_refreshed_at", "cost_list"):
        assert "`%s`" % field in body, field


def test_records_md_copilot_section_names_the_constant_fields():
    body = section(read("docs", "records.md"), "GitHub Copilot sources")
    for field in ("actor_kind", "model_provider", "actor_deleted", "cost_only", "edits_accepted", "commits"):
        assert "`%s`" % field in body, field


def test_api_shapes_has_a_readme_with_provenance_for_every_file():
    text = read("docs", "api-shapes", "README.md")
    assert "synthetic" in text.lower()
    names = [os.path.relpath(p, SHAPES) for p in glob.glob(os.path.join(SHAPES, "*.json"))]
    assert names
    for name in names:
        assert "`%s`" % name in text, name
    for folder in ("copilot/", "litellm/", "fit/"):
        assert "`%s`" % folder in text, folder
    assert re.search(r"[Rr]ead on 2026-\d\d-\d\d", text)


def test_every_shape_folder_readme_marks_its_values_synthetic_and_names_its_source():
    for readme in [os.path.join(SHAPES, "README.md")] + glob.glob(os.path.join(SHAPES, "*", "README.md")):
        text = open(readme, encoding="utf-8").read()
        rel = os.path.relpath(readme, ROOT)
        assert re.search(r"\bsynthetic\b", text, re.I), rel
        assert re.search(r"^Provenance:", text, re.M), rel
