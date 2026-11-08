"""B-5 of the v0.3 hardening spec: the changelog says what was released when.

A released version has a dated section whose date is the date of its tag on the public repository; a version that is not
released yet sits under [Unreleased]; the entries of v0.1 are the v0.1 entries, not "unreleased"; and one paragraph tells
someone who upgrades from 0.1 that the gold folder moved.

Run: python3 -m pytest -q tools
"""
import os
import re
import subprocess
import sys

import pytest
try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import package_list  # noqa: E402
import test_released_versions as released  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
DATED = re.compile(r"^## \[([a-z][a-z-]*) (\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", re.M)
UNRELEASED = re.compile(r"^## \[Unreleased\]$", re.M)
VERSION_HEADING = re.compile(r"^### ([a-z][a-z-]*) (\d+\.\d+\.\d+)$", re.M)


def key(version):
    return tuple(int(p) for p in version.split("."))


def dated_sections():
    return {(p, v): d for p, v, d in DATED.findall(TEXT)}


def unreleased_versions():
    start = UNRELEASED.search(TEXT)
    assert start, "no [Unreleased] section"
    rest = TEXT[start.end():]
    nxt = re.search(r"^## ", rest, re.M)
    body = rest[:nxt.start()] if nxt else rest
    return {(p, v) for p, v in VERSION_HEADING.findall(body)}


def tree_version(package):
    with open(os.path.join(ROOT, "packages", package, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_unreleased_comes_first_and_holds_one_subsection_per_changed_package():
    assert TEXT.index("## [Unreleased]") < min(m.start() for m in DATED.finditer(TEXT))
    assert unreleased_versions(), "the entries of integrate part 2 and of the v0.3 hardening go under [Unreleased]"


def test_every_tree_version_is_in_the_changelog_exactly_once():
    for package in package_list.packages():
        version = tree_version(package)
        dated = (package, version) in dated_sections()
        waiting = (package, version) in unreleased_versions()
        assert dated != waiting, "%s %s is %s" % (package, version, "in both places" if dated else "in neither place")


def test_no_released_version_sits_under_unreleased():
    for package, version in sorted(unreleased_versions()):
        newest_dated = max((key(v) for p, v in dated_sections() if p == package), default=(0, 0, 0))
        assert key(version) > newest_dated, "%s %s is under [Unreleased] but %s is already dated" % (package, version, newest_dated)


def test_the_v0_1_entries_are_dated_2026_11_01_and_the_v0_2_entries_2026_11_03():
    dated = dated_sections()
    v01 = {("meterline", "0.2.0"), ("meterline-anthropic", "0.2.0"), ("meterline-anthropic-admin", "0.1.0"),
           ("meterline-litellm", "0.1.0"), ("meterline-copilot", "0.1.0"), ("meterline-sim", "0.1.0"), ("meterline-gold", "0.1.0")}
    v02 = {("meterline", "0.3.0"), ("meterline-anthropic", "0.2.1"), ("meterline-anthropic-admin", "0.1.1"),
           ("meterline-litellm", "0.1.1"), ("meterline-copilot", "0.1.1"), ("meterline-gold", "0.2.0"), ("meterline-job", "0.1.0")}
    assert {k: dated.get(k) for k in v01} == {k: "2026-11-01" for k in v01}
    assert {k: dated.get(k) for k in v02} == {k: "2026-11-03" for k in v02}


def test_the_v0_1_known_limits_state_the_evidence_as_it_stood():
    assert "No connector has been run against a live account yet" not in TEXT
    section = TEXT.split("## [Repository v0.1] - 2026-11-01", 1)[1].split("\n## ", 1)[0]
    assert "meterline-anthropic-admin" in section and "small" in section and "live" in section


def test_upgrading_from_0_1_says_the_gold_folder_moved_and_what_stays_behind():
    text = " ".join(TEXT.split())
    assert "## Upgrading from 0.1" in TEXT
    paragraph = " ".join(TEXT.split("## Upgrading from 0.1", 1)[1].split("\n## ", 1)[0].split())
    for needle in ("`builds/`", "`frozen/`", "`published/`", "<gold_root>/<date>", "no new data", "person-level data", "retention"):
        assert needle in paragraph, needle
    readme = " ".join(open(os.path.join(ROOT, "README.md"), encoding="utf-8").read().split())
    assert "Upgrading from 0.1" in readme and "CHANGELOG.md" in readme
    assert text


@pytest.fixture(scope="module")
def public_tag_dates():
    tags, reason = released.public_release_tags(ROOT)
    if tags is None:
        pytest.skip("the public repository could not be reached, so the tag dates cannot be compared: %s" % reason)
    out = subprocess.run(["git", "--no-optional-locks", "for-each-ref", "--format=%(refname) %(taggerdate:short)", "refs/public-tags/"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {line.split()[0].split("/")[-1]: line.split()[1] for line in out.splitlines() if len(line.split()) == 2}


def test_every_released_tag_has_a_dated_section_with_its_date(public_tag_dates):
    assert public_tag_dates
    dated = dated_sections()
    problems = []
    for tag, date in sorted(public_tag_dates.items()):
        package, _, version = tag.rpartition("-v")
        if dated.get((package, version)) != date:
            problems.append("%s: tag %s, changelog %s" % (tag, date, dated.get((package, version))))
    assert problems == []
