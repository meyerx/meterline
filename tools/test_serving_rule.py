"""B-1 of the v0.3 hardening spec: the serving rule is said the same way everywhere.

``published/`` holds every audience: ``central/`` with person-level rows, ``manifest.json`` with every key, and a folder for
each person and manager. "Serve only the published folder" is therefore wider than the rule. Each reader receives only their
own sub-folder, and ``servable(path, audience)`` is the check; the one table is the gold README's "Who gets which folder".
Every other place says it in one sentence and links there.

Run: python3 -m pytest -q tools
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_public as ep  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = ("serve only the `published`", "serve this link", "and nothing else under the gold root")
TABLE_LINK = re.compile(r"meterline-gold/README\.md#who-gets-which-folder")
SAY_IT = (
    "docs/how-to/operate-daily.md",
    "packages/meterline-job/README.md",
    "docs/PRIVACY.md",
    "docs/THREAT-MODEL.md",
)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def flat(text):
    return " ".join(text.split())


def exported_markdown():
    listed = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    return sorted(f for f in listed if f.endswith(".md") and f.split("/")[0] in ep.INCLUDE and f not in ep.EXCLUDE
                  and not f.startswith("docs/dev/") and os.path.isfile(os.path.join(ROOT, f)))


def integration_pages_that_serve():
    pages = sorted(f for f in os.listdir(os.path.join(ROOT, "docs", "integrate")) if f.endswith(".md"))
    return ["docs/integrate/" + f for f in pages if re.search(r"\bserv(e|ed|ing)\b", read("docs/integrate/" + f), re.I)]


def test_the_exported_markdown_is_found():
    files = exported_markdown()
    assert "README.md" in files and "docs/PRIVACY.md" in files and len(files) > 20


def test_no_exported_file_tells_a_reader_to_serve_the_whole_published_folder():
    wrong = []
    for rel in exported_markdown():
        text = flat(read(rel)).lower()
        wrong += ["%s: %r" % (rel, phrase) for phrase in FORBIDDEN if phrase in text]
    assert wrong == []


@pytest.mark.parametrize("rel", SAY_IT + ("packages/meterline-gold/README.md",))
def test_each_file_names_servable_as_the_check(rel):
    assert "servable(path, audience)" in flat(read(rel)), rel


@pytest.mark.parametrize("rel", SAY_IT)
def test_each_file_links_the_gold_readme_serving_table(rel):
    assert TABLE_LINK.search(read(rel)), rel


def test_the_gold_readme_has_the_table_the_others_link():
    text = read("packages/meterline-gold/README.md")
    assert "\n## Who gets which folder\n" in text
    assert "servable(path, audience)" in text.split("## Who gets which folder", 1)[1].split("\n## ", 1)[0]


def test_the_integration_pages_that_talk_about_serving_link_the_table_and_name_the_check():
    pages = integration_pages_that_serve()
    assert "docs/integrate/README.md" in pages
    for rel in pages:
        text = read(rel)
        assert TABLE_LINK.search(text), rel
        assert "servable(path, audience)" in flat(text), rel


@pytest.mark.parametrize("rel", ("docs/how-to/operate-daily.md", "packages/meterline-job/README.md"))
def test_the_operations_pages_say_each_reader_receives_only_their_own_sub_folder(rel):
    text = flat(read(rel))
    assert re.search(r"each reader (receives|gets) only (their|his or her) own", text, re.I), rel
    for part in ("`central/`", "`exec/`", "`manager/<key>/`", "`person/<key>/`", "`builds/`", "`frozen/`"):
        assert part in text, (rel, part)


def test_the_privacy_page_says_the_manifest_is_copied_into_published_for_central_analysts():
    row = [line for line in read("docs/PRIVACY.md").splitlines() if line.startswith("| Gold | `manifest.json`")]
    assert len(row) == 1
    assert "copied into `published/`" in row[0] and "central analysts" in row[0].lower()
    assert "Central analysts only; never delivered." not in row[0]  # the old line, which the copy into published/ contradicts
