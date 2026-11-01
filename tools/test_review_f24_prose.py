"""F-24: the READMEs read in short sentences, every table and list is framed, Known limits are grouped by kind,
and the live-run caveat is stated once per file.

Words are counted outside code blocks and tables, with inline code and link targets collapsed to one word.

Run: python3 -m pytest -q tools/test_review_f24_prose.py
"""
import glob
import os
import re

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import connectors  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READMES = ["README.md"] + sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "packages", "*",
                                                                                            "README.md")))
CONNECTORS = ["packages/%s/README.md" % c for c in connectors()]
LEARNING = ["docs/records.md", "docs/tutorial.md", "docs/glossary.md", "docs/architecture.md",
            "docs/how-to/configure-policy.md", "docs/how-to/add-a-provider.md"]
MAX_WORDS = 45


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def prose_lines(text):
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            fence = not fence
            continue
        if fence or line.startswith(("|", "#", "<!--", "---")):
            continue
        yield n, line


def sentences(text):
    for n, line in prose_lines(text):
        plain = re.sub(r"`[^`]*`", "CODE", line)
        plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain)
        plain = re.sub(r"^\s*(>|[-*]|\d+\.)\s+", "", plain)
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z`*(\"])", plain):
            yield n, len(part.split()), part


def section(text, heading):
    match = re.search(r"^## %s\s*$(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def unframed_groups(text):
    """Tables, and lists of three or more items, whose nearest line above is a heading."""
    lines, bad, fence, i = text.splitlines(), [], False, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            fence = not fence
        if fence or not (line.startswith("|") or re.match(r"^(- |\d+\. )", line)):
            i += 1
            continue
        table = line.startswith("|")
        j, items = i, 0
        while j < len(lines):
            here = lines[j]
            if table and here.startswith("|"):
                items += 1
            elif not table and re.match(r"^(- |\d+\. )", here):
                items += 1
            elif not table and (here.startswith("  ") or (here == "" and j + 1 < len(lines)
                                                           and re.match(r"^(- |\d+\. )", lines[j + 1]))):
                pass
            else:
                break
            j += 1
        k = i - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if (table or items >= 3) and (k < 0 or lines[k].startswith("#")):
            bad.append("line %d after %r" % (i + 1, lines[k] if k >= 0 else ""))
        i = j
    return bad


def test_no_sentence_is_longer_than_45_words():
    long = ["%s:%d (%d words)" % (rel, n, w) for rel in READMES + LEARNING for n, w, _ in sentences(read(rel))
            if w > MAX_WORDS]
    assert not long, long


def test_every_table_and_list_has_a_sentence_before_it():
    bad = ["%s %s" % (rel, b) for rel in READMES for b in unframed_groups(read(rel))]
    assert not bad, bad


def test_known_limits_are_grouped_by_kind():
    for rel in ["README.md"] + CONNECTORS:
        body = section(read(rel), "Known limits")
        labels = re.findall(r"^\*\*[^*\n]+\.\*\*", body, re.M)
        assert len(labels) >= 2, rel
        assert not re.match(r"\s*- ", body), "%s: Known limits opens with a list, not a framing sentence" % rel


def test_the_live_run_caveat_is_stated_once_per_file():
    for rel in READMES:
        count = len(re.findall(r"not (yet )?(been )?run against a live", read(rel), re.I))
        assert count <= 1, (rel, count)


def test_root_known_limits_copies_no_bullet_of_a_package_readme():
    # compared without a leading label ("Out of scope (v1 and v2): ..."), so a relabelled copy still counts
    root = [b.split(": ", 1)[-1].strip() for b in re.findall(r"^- (.+)$", section(read("README.md"), "Known limits"),
                                                              re.M)]
    for rel in READMES[1:]:
        text = read(rel)
        assert not [b for b in root if len(b) > 40 and b in text], rel
