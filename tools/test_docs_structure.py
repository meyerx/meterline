"""B1: the docs are organised by goal, and nobody follows a dead link.

A relative link in a Markdown file must point at a file that exists; maintainer material (specs, change lists,
analyses, plans) lives under docs/dev/; docs/README.md is the index by goal.

Run: python3 -m pytest -q tools/test_docs_structure.py
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import in_working_repo  # noqa: E402
import wiring  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files kept out of the public export; their links are not checked because the targets they name are private too.
# (names assembled so that this file passes the export scan it sits beside)
PRIVATE_SOURCES = ("BACKLOG" + "-std.md", "CLAUDE" + ".md")
# Accepted specs are frozen: moving one is allowed, editing its body (links included) is not.
FROZEN = re.compile(r"-SPEC-v[0-9.]+-[0-9-]+\.md$")

FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.M | re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REF_LINK = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*<?(\S+?)>?(?:\s+.*)?$", re.M)


def tracked_files(root):
    """Tracked files in a git checkout, every file in an exported tree."""
    if os.path.isdir(os.path.join(root, ".git")) or os.path.isfile(os.path.join(root, ".git")):
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
        return sorted(p for p in out.split("\0") if p and os.path.exists(os.path.join(root, p)))
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "node_modules")]
        found += [os.path.relpath(os.path.join(base, f), root) for f in files]
    return sorted(found)


def relative_targets(text):
    text = INLINE_CODE.sub("", FENCE.sub("", text))
    for target in INLINE_LINK.findall(text) + REF_LINK.findall(text):
        if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "//")):
            continue  # a URL scheme, an in-page anchor
        target = target.split("#", 1)[0].split("?", 1)[0]
        if target:
            yield target


def broken_links(root, skip_frozen=True):
    problems = []
    for rel in tracked_files(root):
        if not rel.endswith(".md") or rel in PRIVATE_SOURCES:
            continue
        if skip_frozen and FROZEN.search(rel):
            continue
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
        for target in relative_targets(text):
            path = os.path.normpath(os.path.join(root, os.path.dirname(rel), target))
            if not os.path.exists(path):
                problems.append("%s -> %s" % (rel, target))
    return problems


def test_no_broken_relative_link_in_a_markdown_file():
    assert broken_links(ROOT) == []


def test_link_check_flags_a_dead_link_and_ignores_code_and_urls(tmp_path):
    # negative control: one dead link, plus a fenced one, an inline-code one, a URL and an anchor that must not count
    (tmp_path / "ok.md").write_text("# ok\n", encoding="utf-8")
    (tmp_path / "a.md").write_text(
        "[fine](ok.md#top) [dead](gone.md) [url](https://example.com/x) [here](#top)\n"
        "`[code](nope.md)`\n\n```\n[fenced](nope2.md)\n```\n\n[ref]: missing.md\n", encoding="utf-8")
    assert broken_links(str(tmp_path)) == ["a.md -> gone.md", "a.md -> missing.md"]


MAINTAINER = re.compile(r"(^|/)(?:[^/]*-SPEC-[^/]*|change-list-[^/]*)$")


def test_maintainer_material_sits_under_docs_dev():
    stray = []
    for rel in tracked_files(ROOT):
        if rel.startswith("docs/dev/") or not rel.startswith("docs/"):
            continue
        parts = rel.split("/")
        if MAINTAINER.search(rel) or "analysis" in parts[:-1] or "plans" in parts[:-1] \
                or parts[-1] in ("style.md", "RELEASING.md"):
            stray.append(rel)
    assert stray == []


def test_docs_dev_names_the_spec_in_force_per_package():
    if not os.path.exists(os.path.join(ROOT, "docs", "dev", "README.md")) and not in_working_repo():
        pytest.skip("the maintainer index is not part of the public repository: no public document links it")
    with open(os.path.join(ROOT, "docs", "dev", "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    packages = sorted(d for d in os.listdir(os.path.join(ROOT, "packages")) if d.startswith("meterline"))
    # every package that has a spec appears in the table; the others are named as having none
    for pkg in packages:
        assert "`%s`" % pkg in text, pkg
        assert wiring.BY_ID["docs-dev-readme"].missing(ROOT, pkg) == [], pkg  # the predicate tools/check_new_connector.py prints the row for


GOALS = ["Try it on simulated data", "Run a connector on a real account", "Build a dashboard from gold",
         "Add a provider"]
OTHERS = ["Reference", "Explanation", "Maintainers"]


def sections(text):
    parts = re.split(r"^## +(.+?)\s*$", text, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def test_docs_readme_has_the_four_goals_each_with_a_link():
    with open(os.path.join(ROOT, "docs", "README.md"), encoding="utf-8") as fh:
        found = sections(fh.read())
    for name in GOALS + OTHERS:
        assert name in found, name
    for name in GOALS:
        assert len(INLINE_LINK.findall(found[name])) >= 1, name
    assert list(found)[:4] == GOALS
    assert "docs/dev/" in found["Maintainers"] or "dev/" in found["Maintainers"]


def test_readme_points_at_the_four_goals_and_names_the_layer_to_read():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        text = fh.read()
    assert "Where to start" in text
    block = text.split("Where to start", 1)[1][:1500]
    assert block.count("docs/README.md") + block.count("docs/") >= 4
    assert "reads **gold**" in text and "silver" in text.split("reads **gold**", 1)[1][:400]
