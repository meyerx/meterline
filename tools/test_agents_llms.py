"""B2: AGENTS.md and llms.txt exist, say the privacy rules, link only to tracked files, and carry no private trace.

Run: python3 -m pytest -q tools/test_agents_llms.py
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import export_public as ep  # noqa: E402
from test_docs_structure import tracked_files  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def homepage():
    with open(os.path.join(ROOT, "packages", "meterline", "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)["project"]["urls"]["Homepage"].rstrip("/")


def test_both_files_exist_at_the_root():
    assert os.path.isfile(os.path.join(ROOT, "AGENTS.md"))
    assert os.path.isfile(os.path.join(ROOT, "llms.txt"))


def test_agents_md_is_at_most_1500_words():
    assert len(read("AGENTS.md").split()) <= 1500


# each rule: the words that must appear in its numbered line
PRIVACY_RULES = {
    "never serve delivery or the manifest": ["never serve", "delivery/", "manifest.json"],
    "a dashboard reads the audience folder of its reader": ["audience folder", "reader"],
    "central is for the central team only": ["central/", "central team only"],
    "an empty hidden value is withheld, never zero": ["hidden = true", "withheld", "never zero"],
    "never commit a key or a real export": ["never commit", "key", "real export"],
    "silver holds person identifiers": ["silver", "person identifiers"],
}


def privacy_lines(text):
    body = re.split(r"^## +", text, flags=re.M)
    section = next(s for s in body if s.lower().startswith("privacy rules"))
    return re.findall(r"^\d+\. (.+)$", section, flags=re.M)


@pytest.mark.parametrize("rule", sorted(PRIVACY_RULES))
def test_agents_md_states_each_privacy_rule_as_a_numbered_imperative(rule):
    lines = privacy_lines(read("AGENTS.md"))
    assert len(lines) >= len(PRIVACY_RULES)
    words = PRIVACY_RULES[rule]
    assert any(all(w.lower() in line.lower() for w in words) for line in lines), rule


def test_agents_md_has_the_sections_an_agent_looks_for():
    text = read("AGENTS.md").lower()
    for heading in ("install", "offline", "provider", "contracts", "privacy rules", "tests", "keep"):
        assert re.search(r"^## .*%s" % heading, text, flags=re.M), heading


def test_llms_txt_follows_llmstxt_org():
    lines = read("llms.txt").split("\n")
    assert lines[0] == "# meterline"
    assert lines[1] == "" and lines[2].startswith("> ") and len(lines[2]) > 40
    headings = [l[3:] for l in lines if l.startswith("## ")]
    assert headings == ["Docs", "Reference", "Examples", "Optional"]
    for line in lines:
        if line.startswith("- "):
            assert re.match(r"- \[[^\]]+\]\(https://[^)\s]+\): \S.*", line), line


def llms_links():
    return re.findall(r"\]\((https?://[^)\s]+)\)", read("llms.txt"))


def test_every_llms_txt_link_maps_to_a_tracked_file():
    base = homepage() + "/blob/main/"
    tracked = set(tracked_files(ROOT))
    links = llms_links()
    assert links
    bad = [l for l in links if not l.startswith(base) or l[len(base):].split("#")[0] not in tracked]
    assert bad == []


def test_link_check_flags_a_link_outside_the_repository_base():
    # negative control: the same predicate on a foreign host and on a path that is not tracked
    base = homepage() + "/blob/main/"
    tracked = {"README.md"}
    for link in ("https://example.org/README.md", base + "nope.md"):
        assert not (link.startswith(base) and link[len(base):] in tracked)


def test_neither_file_carries_a_private_trace(tmp_path):
    for name in ("AGENTS.md", "llms.txt"):
        (tmp_path / name).write_text(read(name), encoding="utf-8")
    assert ep.scan(str(tmp_path), []) == []


def test_the_scan_catches_a_planted_trace(tmp_path):
    # negative control: a home path in an AGENTS.md copy
    (tmp_path / "AGENTS.md").write_text("see /Us" + "ers/someone/x\n", encoding="utf-8")
    assert [h[0] for h in ep.scan(str(tmp_path), [])] == ["absolute home path"]


# MF-7: an agent reads the first lines of these files and nothing more, so the evidence level of each connector
# must be there. What a level means is defined in docs/glossary.md#evidence-levels.
DOCUMENTED = "built against documented shapes"
CONNECTOR_LEVELS = {
    "meterline-anthropic": [DOCUMENTED],
    "meterline-anthropic-admin": ["verified live", "small Console organisation"],
    "meterline-litellm": [DOCUMENTED, "LiteLLM v1.106.6"],
    "meterline-copilot": [DOCUMENTED],
}


def head30(name):
    return read(name).split("\n")[:30]


@pytest.mark.parametrize("name", ["AGENTS.md", "llms.txt"])
def test_the_first_30_lines_say_no_other_connector_has_met_a_live_account(name):
    assert any("live account" in line for line in head30(name))


@pytest.mark.parametrize("name", ["AGENTS.md", "llms.txt"])
def test_the_first_30_lines_link_the_evidence_levels(name):
    assert any(re.search(r"docs/glossary\.md#evidence-levels", line) for line in head30(name))


@pytest.mark.parametrize("name", ["AGENTS.md", "llms.txt"])
@pytest.mark.parametrize("package", sorted(CONNECTOR_LEVELS))
def test_the_first_30_lines_give_each_connector_its_evidence_level(name, package):
    # the package name must not be a prefix of a longer one (meterline-anthropic vs meterline-anthropic-admin)
    named = re.compile(r"%s(?![\w-])" % re.escape(package))
    lines = [line for line in head30(name) if named.search(line)]
    assert any(all(words in line for words in CONNECTOR_LEVELS[package]) for line in lines), (name, package)
