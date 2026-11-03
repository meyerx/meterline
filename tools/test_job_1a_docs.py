"""Job 1a docs: the exit tables list code 4, the operations page is about `meterline-job`, and the lock is written down.

Run: python3 -m pytest -q tools/test_job_1a_docs.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import ROOT  # noqa: E402

PAGE = os.path.join("docs", "how-to", "operate-daily.md")
EXAMPLE = os.path.join("examples", "03-daily-job", "README.md")
PACKAGE = os.path.join("packages", "meterline-job", "README.md")
EXPECTED = {"0", "1", "2", "3", "4"}


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def exit_codes(text):
    """The exit codes that a table row of ``text`` starts with: ``| `4` | ...``."""
    return set(re.findall(r"^\| `(\d)` \|", text, re.M))


def missing_codes(text):
    return EXPECTED - exit_codes(text)


def test_every_exit_table_lists_the_codes_0_to_4():
    for rel in (PAGE, EXAMPLE, PACKAGE):
        assert missing_codes(read(rel)) == set(), "%s: no row for exit code(s) %s" % (rel, sorted(missing_codes(read(rel))))


def test_the_exit_table_check_fails_on_a_copy_without_4():
    for rel in (PAGE, EXAMPLE, PACKAGE):
        without = re.sub(r"^\| `4` \|.*\n", "", read(rel), flags=re.M)
        assert missing_codes(without) == {"4"}, rel  # the negative control: the check does notice a table with no 4


def test_the_operations_page_runs_the_package_and_names_the_lock():
    text = read(PAGE)
    assert "meterline-job run" in text and "meterline-job check" in text
    assert "runs/.lock" in text and "flock" in text  # the lock is the job's own; the word stays for the cron line
    assert "--step" in text and "--no-retry" in text
    order = text.split("## The order of a run", 1)[1].split("\n## ", 1)[0]
    assert "silver" in order.lower() and "gold" in order.lower()


def test_the_page_states_the_admin_connectors_verified_status_not_a_blanket_claim():
    text = read(PAGE)
    assert "no connector has run against a live account" not in text
    assert "verified live" in text and "meterline-anthropic-admin" in text


def test_the_changelog_names_the_lock_and_rule_r17():
    text = read("CHANGELOG.md")  # the entries sit in the dated sections of the packages that release them
    for word in ("meterline-job", "R17", "data_folder_lock", "exits 4"):
        assert word in text, word
