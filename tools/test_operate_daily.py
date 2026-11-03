"""D: the operations page for the daily job says what the job does, for every connector, and agrees with the code.

Run: python3 -m pytest -q tools/test_operate_daily.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import ROOT, connectors  # noqa: E402
from wiring import BY_ID  # noqa: E402  the places a package must be named in, shared with tools/check_new_connector.py

PAGE = os.path.join(ROOT, "docs", "how-to", "operate-daily.md")
JOB = os.path.join(ROOT, "packages", "meterline-job", "src", "meterline_job", "job.py")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_the_page_covers_what_the_spec_asks():
    text = read(PAGE)
    for heading in ("The order of a run", "Which command to run", "One run at a time", "What the job does with a failure",
                    "How long a first backfill takes", "Where to look when a run fails", "What this does not prove"):
        assert re.search(r"^## %s$" % re.escape(heading), text, re.M), heading
    assert "flock" in text and "OnCalendar" in text and "[Timer]" in text  # a cron line and the systemd units
    assert "estimates" in text and "not measurements" in text  # the durations are labelled


def test_the_duration_table_names_every_connector():
    table = read(PAGE).split("## How long a first backfill takes", 1)[1].split("\n## ", 1)[0]
    for connector in connectors():
        assert "`%s`" % connector in table, connector
        assert BY_ID["operate-daily"].missing(ROOT, connector) == [], connector  # the predicate tools/check_new_connector.py prints the row for


def test_the_page_agrees_with_the_job_about_the_retry_and_the_alerts():
    job = read(JOB)
    text = read(PAGE)
    wait = re.search(r"^DEFAULT_RETRY_WAIT_SECONDS = (\d+)", job, re.M).group(1)
    assert "`retry_wait_seconds` (%s)" % wait in text
    assert re.search(r"^DEFAULT_RETRIES = 1", job, re.M)
    for kind in ("input", "permanent"):
        assert '"%s"' % kind in job.split("ALERTS = {", 1)[1].split("}", 1)[0]
    assert "transient" not in job.split("ALERTS = {", 1)[1].split("}", 1)[0]  # a transient failure is retried, not alerted


def test_the_page_is_linked_from_the_index_and_the_example():
    assert "how-to/operate-daily.md" in read(os.path.join(ROOT, "docs", "README.md"))
    assert "docs/how-to/operate-daily.md" in read(os.path.join(ROOT, "examples", "03-daily-job", "README.md"))
