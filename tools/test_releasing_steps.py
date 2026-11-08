"""C-6 and C-7 of the v0.3 hardening spec: the release procedure names the repository-version release note and the two
public-repository settings.

The wording is for a person to review; this test holds the parts a reader must not lose: the step and its warning, the two
settings with the command that shows each one on, and the corrected Dependabot sentence.

Run: python3 -m pytest -q tools
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def section(text, heading):
    start = text.index("\n## %s\n" % heading)
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


RELEASING = read("docs", "dev", "RELEASING.md")


def test_later_public_versions_has_the_repository_version_release_note_step():
    later = section(RELEASING, "Later public versions")
    for phrase in ("release note", "who it is for", "what changed", "upgrading", "known limits",
                   "GitHub release named `meterline vX.Y`", "tag `vX.Y`", "public `main`", "after the export merge"):
        assert phrase in later, phrase
    warning = re.search(r"a tag (?:that )?match(?:es|ing) `meterline\*-v\*`[^.]*start", later, re.I)
    assert warning, "the step must warn that a tag matching meterline*-v* starts the package release job"
    assert "export/vX.Y" in later and "not `export/vX.Y`" in later  # the repository-version tag is not the private export tag


def test_the_first_hour_list_and_the_later_versions_list_name_the_two_settings_with_their_command():
    first_hour = RELEASING[RELEASING.index("The first hour of the new repository"):RELEASING.index("## Pre-mortem")]
    later = section(RELEASING, "Later public versions")
    for where, text in (("the first-hour list", first_hour), ("the later-versions list", later)):
        assert "require actions to be pinned to a full-length commit SHA" in text, where
        assert "automatically delete head branches" in text, where
        assert "gh api repos/meyerx/meterline/actions/permissions --jq .sha_pinning_required" in text, where
        assert "gh api repos/meyerx/meterline --jq .delete_branch_on_merge" in text, where
    assert "`export/v0.2`" in later and "delete" in later.split("`export/v0.2`")[0][-200:] + later.split("`export/v0.2`")[1][:200]


def test_the_dependabot_sentence_says_the_maintainers_working_repository():
    sentence = [line for line in RELEASING.splitlines() if "Dependabot" in line and "pull requests" in line]
    assert sentence and "in the maintainer's working repository" in " ".join(sentence)
    assert "the public repository gets Dependabot alerts only" in RELEASING


def test_the_readme_front_page_links_the_releases_page():
    assert "https://github.com/meyerx/meterline/releases" in read("README.md")
