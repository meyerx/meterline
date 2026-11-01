"""MF-8: a number written before "connectors" or "packages" in a public document agrees with the tree.

The numbers are read from the tree, never typed here: a package is a folder under packages/ with a pyproject.toml,
and a connector is a package whose src defines a FakeAPI class in testing.py. The documents scanned are the files
tools/export_public.py would export (its INCLUDE and EXCLUDE lists, and its rule for which docs/dev files stay),
read from the working tree, plus the two living maintainer files docs/dev/README.md and docs/dev/RELEASING.md.
CHANGELOG.md, docs/decisions/ and the dated records under docs/dev/ (specs, change lists, history, plans, analysis)
are left out: they say what was true when they were written.

Run: python3 -m pytest -q tools/test_counts_match_the_tree.py
"""
import glob
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_public as ep  # noqa: E402
from package_list import connectors, packages  # noqa: E402
from test_docs_structure import tracked_files  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
# a number, then the noun, with nothing between; "-n 4 packages" is the pytest-xdist worker flag, not a count
COUNT = re.compile(r"(?<!-n )\b(%s|\d+)\s+(connectors|packages)\b" % "|".join(NUMBER_WORDS), re.IGNORECASE)
DOCUMENT_SUFFIXES = (".md", ".txt", ".yml")
LIVING_DEV_FILES = ("docs/dev/README.md", "docs/dev/RELEASING.md")
LEFT_OUT = ("CHANGELOG.md", "docs/decisions/")
DATED_DEV_RECORD = re.compile(r"^docs/dev/(history/|plans/|analysis/|[^/]*-SPEC-[^/]*$|change-list-[^/]*$)")


def connectors_in_tree(root=ROOT):
    """Packages whose src defines a class FakeAPI in testing.py."""
    found = []
    for package in packages(root):
        for testing in glob.glob(os.path.join(root, "packages", package, "src", "*", "testing.py")):
            with open(testing, encoding="utf-8") as fh:
                if re.search(r"^class FakeAPI\b", fh.read(), re.MULTILINE):
                    found.append(package)
    return sorted(found)


def expected(root=ROOT):
    return {"connectors": len(connectors_in_tree(root)), "packages": len(packages(root))}


def number_of(token):
    return NUMBER_WORDS.index(token.lower()) if token.lower() in NUMBER_WORDS else int(token)


def exported_documents(tmp_path, root=ROOT):
    """Relative paths of the exportable documents, copied from the working tree (``root``) into tmp_path.

    The exporter's own allowlist and its docs/dev rule select them, so a new public folder is scanned without a change here."""
    for rel in tracked_files(root):
        if not any(rel == p or rel.startswith(p + "/") for p in ep.INCLUDE) or rel in ep.EXCLUDE:
            continue
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(root, rel), dest)
    ep.prune_dev(str(tmp_path))
    kept = [rel for rel in ep._tree_files(str(tmp_path)) if rel.endswith(DOCUMENT_SUFFIXES)]
    kept += [rel for rel in LIVING_DEV_FILES if rel not in kept and os.path.exists(os.path.join(root, rel))]
    skipped = [rel for rel in kept if rel in LEFT_OUT or rel.startswith(LEFT_OUT[1]) or DATED_DEV_RECORD.match(rel)]
    return sorted(set(kept) - set(skipped))


def disagreements(text, want):
    """(line number, phrase) for each number before connectors or packages that differs from the tree."""
    bad = []
    for number, line in enumerate(text.split("\n"), 1):
        for match in COUNT.finditer(line):
            if number_of(match.group(1)) != want[match.group(2).lower()]:
                bad.append((number, match.group(0)))
    return bad


def counts_that_disagree(tmp_path, root=ROOT, documents=None):
    """'file:line: phrase (the tree has ...)' for each number of connectors or packages written in an exportable document of ``root``
    that differs from the tree. tools/wiring.py runs this for the `counts` item of tools/check_new_connector.py, so the command
    and this test read one scan."""
    want = expected(root)
    wrong = []
    for rel in documents if documents is not None else exported_documents(tmp_path, root):
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            wrong += ["%s:%d: %r (the tree has %d connectors, %d packages)" % (rel, n, phrase, want["connectors"], want["packages"])
                      for n, phrase in disagreements(fh.read(), want)]
    return wrong


def test_counts_match_the_tree(tmp_path):
    want = expected()
    assert want["connectors"] >= 1 and want["packages"] > want["connectors"], want
    documents = exported_documents(tmp_path)
    assert "README.md" in documents and "SECURITY.md" in documents, documents
    wrong = counts_that_disagree(tmp_path, documents=documents)
    assert not wrong, "\n" + "\n".join(wrong)


def test_the_scan_flags_a_wrong_count_and_passes_a_right_one():
    # negative control: the same predicate on planted text, built from the counts read from the tree
    want = expected()
    wrong_c, wrong_p = NUMBER_WORDS[want["connectors"] - 1], str(want["packages"] - 1)
    right_c, right_p = NUMBER_WORDS[want["connectors"]], NUMBER_WORDS[want["packages"]]
    planted = "all %s connectors; the %s packages" % (wrong_c, wrong_p)
    assert [phrase for _, phrase in disagreements(planted, want)] == ["%s connectors" % wrong_c, "%s packages" % wrong_p]
    assert disagreements("%s connectors, %s packages, %d packages; run -n 4 packages tools" % (right_c, right_p, want["packages"]), want) == []


def test_the_two_ways_to_find_the_connectors_agree():
    # FakeAPI in testing.py (used here) and the httpx dependency (tools/package_list.py) name the same packages
    assert connectors_in_tree() == connectors()
