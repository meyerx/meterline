"""A: the connector contract page, the connectors named in the front-door prose, and no test that hard-codes the package list.

Run: python3 -m pytest -q tools/test_connector_contract_docs.py
"""
import ast
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import ROOT, connectors, packages  # noqa: E402
from wiring import BY_ID, mentioned  # noqa: E402  the places a package must be named in, shared with tools/check_new_connector.py

from meterline.conformance import RULES  # noqa: E402
from meterline.run import FAILURE_KINDS  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_there_are_connectors_to_check():
    assert connectors() and set(connectors()) < set(packages())


def test_the_name_check_tells_a_connector_from_its_longer_sibling():
    # negative control: the helper does not take meterline-anthropic-admin for meterline-anthropic
    assert mentioned("see meterline-anthropic-admin", "meterline-anthropic-admin")
    assert not mentioned("see meterline-anthropic-admin", "meterline-anthropic")


def test_readme_agents_llms_and_threat_model_name_every_connector():
    # one predicate per file, the one tools/check_new_connector.py prints the missing line for
    for item in ("readme-packages-table", "agents-md", "llms-txt", "threat-model"):
        missing = {c: BY_ID[item].missing(ROOT, c) for c in connectors() if BY_ID[item].missing(ROOT, c)}
        assert not missing, "%s: %s" % (BY_ID[item].file, missing)


def test_every_kit_rule_has_a_section_and_a_table_row_in_the_contract():
    page = read("docs", "reference", "connector-contract.md")
    for rule in RULES:
        assert re.search(r"^### %s$" % rule.id, page, re.M), "no section for %s" % rule.id
        assert "[%s](#%s)" % (rule.id, rule.id.lower()) in page, "no table row for %s" % rule.id
    sections = re.findall(r"^### (R\d\d)$", page, re.M)
    assert sections == [rule.id for rule in RULES], "the contract and the kit list different rules"


def test_the_contract_exit_code_table_matches_the_core_constants():
    page = read("docs", "reference", "connector-contract.md")
    rows = re.findall(r"^\| `(\d)` \| `(\w+)` \|", page, re.M)
    assert rows == [(str(n), kind) for n, kind in enumerate(FAILURE_KINDS)]


def test_the_docs_index_and_the_how_to_link_the_contract():
    assert "reference/connector-contract.md" in read("docs", "README.md")
    assert "../reference/connector-contract.md" in read("docs", "how-to", "add-a-provider.md")


def _package_name_collections(path):
    """Every list, tuple or set literal in ``path`` that holds three or more package names."""
    names = set(packages())
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            found = [e.value for e in node.elts if isinstance(e, ast.Constant) and e.value in names]
            if len(found) >= 3:
                yield node.lineno, found


def test_no_tools_test_hard_codes_the_package_list():
    here = os.path.abspath(__file__)
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py")) + glob.glob(os.path.join(ROOT, "packages", "*", "tests", "*.py"))):
        if os.path.abspath(path) == here:
            continue
        offenders += ["%s:%d" % (os.path.relpath(path, ROOT), line) for line, _ in _package_name_collections(path)]
    assert offenders == [], "read the package list from tools/package_list.py: %s" % offenders


def test_the_hard_coded_list_check_flags_one(tmp_path):
    # negative control: a planted list of package names is reported
    planted = tmp_path / "test_planted.py"
    planted.write_text('PACKAGES = ["%s"]\n' % '", "'.join(packages()[:3]), encoding="utf-8")
    assert list(_package_name_collections(str(planted)))
