"""MF-12: the add-a-provider guide can be followed without reading another connector's code.

It installs the core package with its conformance extra, names the shared core modules and the run() entry point,
says what a fake API must do, points to the existing provider mappings, never says to copy a connector, and ends on
the wiring command.

Run: python3 -m pytest -q tools/test_mf12_add_a_provider.py
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE = os.path.join(ROOT, "docs", "how-to", "add-a-provider.md")


def guide():
    with open(GUIDE, encoding="utf-8") as fh:
        return fh.read()


def steps(text):
    """The numbered steps of the section Steps, in order."""
    body = text.split("\n## Steps\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\d+\. (.+)$", body, re.M)


def test_step_3_installs_the_core_package_with_its_conformance_extra_first():
    step = steps(guide())[2]
    assert 'pip install -e "packages/meterline[conformance,dev]"' in step
    assert step.index("packages/meterline[conformance") < step.index("packages/meterline-<provider>[dev]")


def test_the_guide_names_the_shared_core_modules_and_the_run_entry_point():
    text = guide()
    for module in ("meterline.http", "meterline.run", "meterline.state", "meterline.published", "meterline.conformance"):
        assert "`%s`" % module in text, module
    assert "`run(" in text and "#the-run-signature-and-the-credentials-rule" in text


def test_the_guide_says_what_a_fake_api_must_do():
    step = [s for s in steps(guide()) if s.startswith("**Write the fake API")][0]
    for word in ("page", "fail", "429", "log", "clock", "compare_shape", "docs/api-shapes", "duplicate"):
        assert word in step, word


def test_the_guide_points_to_the_existing_mappings_and_the_rules_a_new_provider_needs():
    text = guide()
    for anchor in ("#where-other-providers-mappings-live", "#the-account-label-when-a-response-has-none", "#windows",
                   "#raw-page-naming-for-a-paged-endpoint", "#shape-files"):
        assert "../reference/connector-contract.md" + anchor in text, anchor


def test_no_sentence_says_to_copy_or_open_an_existing_connector():
    text = guide().lower()
    assert not re.search(r"copy (the layout|an existing connector|another connector)", text)
    assert "beside this guide" not in text


def test_the_last_step_is_the_wiring_command_then_the_tools_tests():
    last = steps(guide())[-1]
    assert "tools/check_new_connector.py NAME" in last and "until it prints nothing missing, then `pytest tools`" in last
