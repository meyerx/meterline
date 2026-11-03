"""Publish 1c docs: the delivery rule is the same sentence in four documents, the examples serve `published/`, and the
erasure and retention pages describe the layout `meterline-job` writes.

Run: python3 -m pytest -q tools/test_publish_docs.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import ROOT  # noqa: E402

RULE = (
    "A month is refreshed on every published run until it is frozen, which happens once every source has final data for it "
    "and `freeze_after_days` have passed, or at the latest `freeze_after_days + freeze_cap_extra_days` days after its end at "
    "the next run; from then on it is delivered once and does not change, except that an erased person's rows are removed, "
    "and after a change of the hashing key manager and person folders show only months frozen under the new key. Serve only "
    "the `published` folder, never `builds/` or `frozen/`, and give managers and executives only its latest state; keeping "
    "earlier states is the adopter's choice, not the tool's. A reader who keeps every refresh of an in-progress month can "
    "sometimes learn one person's usage between two refreshes, one person's month-to-date amount when a correction or an "
    "organisation change moves that person, a cell published in one refresh and hidden in the next, or a named exception "
    "that appears and then disappears. The adopter accepts this risk and has it validated by its data protection officer."
)
FOUR = ["packages/meterline-gold/README.md", "docs/PRIVACY.md", "docs/THREAT-MODEL.md", "docs/how-to/operate-daily.md"]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def flat(text):
    return " ".join(text.split())


def carries_rule(text):
    return RULE in flat(text)


def test_the_delivery_rule_is_the_same_sentence_in_the_four_documents():
    missing = [rel for rel in FOUR if not carries_rule(read(rel))]
    assert not missing, "the delivery rule is missing or edited in: %s" % ", ".join(missing)


def test_the_rule_check_notices_one_edited_copy():
    for rel in FOUR:
        edited = flat(read(rel)).replace("delivered once and does not change", "delivered once and may change")
        assert not carries_rule(edited), rel  # the negative control: an edited copy is not accepted


def test_the_threat_model_rows_follow_the_rule():
    text = read("docs/THREAT-MODEL.md")
    t6 = next(line for line in text.splitlines() if line.startswith("| T6 |"))
    a8 = next(line for line in text.splitlines() if line.startswith("| A8 |"))
    assert "refresh" in t6 and "two builds of the same month" not in t6
    assert "latest published state" in a8 and "never holds two builds" not in a8
    bug_bar = next(line for line in text.splitlines() if line.startswith("| Medium |"))
    assert "refreshes" in bug_bar


def test_the_gold_readme_states_the_refresh_limit_and_the_spike_window_limit():
    text = flat(read("packages/meterline-gold/README.md"))
    assert "**Refreshes of one month.**" in text and "**Spike windows.**" in text
    assert "previous month" in text.split("**Spike windows.**", 1)[1].split("- **", 1)[0]


def test_the_dashboard_example_and_the_integration_docs_serve_the_published_folder():
    assert re.search(r"cd \S*published/exec", read("packages/meterline-gold/README.md"))
    page = read("docs/how-to/operate-daily.md")
    assert "## The `published` folder" in page and "published/exec" in page
    body = page.split("## The `published` folder", 1)[1].split("\n## ", 1)[0]
    for word in ("builds/", "frozen/", "frozen.json", "freshness.json", "published.json", "freeze_after_days"):
        assert word in body, word
    assert "[publish]" in read("packages/meterline-job/README.md") and "published/" in read("packages/meterline-job/README.md")


def test_the_privacy_page_describes_erasure_and_retention_for_the_new_layout():
    text = read("docs/PRIVACY.md")
    erasure = text.split("## Erasure", 1)[1].split("\n## ", 1)[0]
    retention = text.split("## Retention", 1)[1].split("\n## ", 1)[0]
    step7 = next(line for line in erasure.splitlines() if line.startswith("7."))
    assert "does not rotate" in step7 and "METERLINE_GOLD_KEY" in step7
    for word in ("builds/", "frozen/", "frozen.json", "published-"):
        assert word in step7, word
    assert "old pseudonymous key" in step7 and "cannot be linked" in step7
    for word in ("`builds/`", "`frozen/`", "left"):
        assert word in retention, word
    assert "no published folder" in retention  # the leaver rule


def _changelog_section(text, heading):
    return text.split("## [%s]" % heading, 1)[1].split("\n## [", 1)[0]


def test_the_changelog_records_the_gold_accessors_the_job_step_and_the_docs():
    text = read("CHANGELOG.md")
    gold = _changelog_section(text, "meterline-gold 0.2.0")
    job = _changelog_section(text, "meterline-job 0.1.0")
    unreleased = _changelog_section(text, "Unreleased")
    assert "write_parquet_text" in gold and "dataset_forms" in gold and "policy_privacy" in gold
    assert "`publish` step" in job and "freshness.json" in job
    assert "delivery rule" in job
    for word in ("write_parquet_text", "freshness.json", "delivery rule"):
        assert word not in unreleased, "%s belongs in a dated section, not in [Unreleased]" % word
