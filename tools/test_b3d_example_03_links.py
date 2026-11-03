"""B3-d: every connector README links the daily-job example, the example README estimates the first Copilot backfill from
the connector's own defaults, and the quick start says which timing tests CI skips.

Text checks, with reads of the constants the estimate rests on. A reviewer judges whether a sentence is true.

Run: python3 -m pytest -q tools/test_b3d_example_03_links.py
"""
import inspect
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONNECTORS = ("anthropic", "anthropic-admin", "litellm", "copilot")
LINK = "https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/README.md"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("name", CONNECTORS)
def test_the_connector_readme_links_the_daily_job_example(name):
    text = read("packages", "meterline-" + name, "README.md")
    assert "03-daily-job" in text, name
    assert LINK in text, name


def test_the_quick_start_says_both_speed_tests_are_skipped_when_ci_is_set():
    text = read("README.md")
    paragraph = next(p for p in text.split("\n\n") if "`G21`" in p)
    assert "`S14`" in paragraph and "Both are skipped" in paragraph and "`CI`" in paragraph, paragraph
    # the claim stays true only while the test files say it: G21 and S14 are both skipped on CI
    skipped = r'skipif\(bool\(os\.environ\.get\("CI"\)\).*\n(?:.*\n)?def test_done_check_%s'
    gold = read("packages", "meterline-gold", "tests", "test_done_check_G20_G21_G23_scans_speed_assertions.py")
    assert re.search(skipped % "G21", gold)
    sim = read("packages", "meterline-sim", "tests", "test_done_check_S13_S14_S15_S16_properties_speed_cli_scans.py")
    assert re.search(skipped % "S14_speed", sim)


def test_the_example_readme_estimates_the_first_copilot_backfill_from_the_defaults():
    copilot_cli = pytest.importorskip("meterline_copilot.cli")
    client = pytest.importorskip("meterline_copilot.client")
    fetch = pytest.importorskip("meterline_copilot.fetch")
    rate = inspect.signature(client.CopilotClient.__init__).parameters["rate_per_hour"].default
    text = read("examples", "03-daily-job", "README.md")
    start = text.index("## How long the first Copilot backfill takes")
    body = text[start:].split("\n## ", 1)[0]
    for number in (rate, copilot_cli.REPORT_DAYS_BACK, fetch.SEATS_PAGE_SIZE):
        assert str(number) in body, number
    assert "--rate-per-hour" in body and "hours" in body and "not measured" in body
    assert "3 × 365" in body
