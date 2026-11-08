"""B-2 of the v0.3 hardening spec: the operations page tells how to watch the daily job and what to do when it goes wrong.

A job that fails silently, or that never starts, leaves readers with figures that stop moving while the date stays fresh.
The page has a "Watch it" section, a "When something goes wrong" table of at least eight "if you see X, do Y" rows and a
"Back up" paragraph; example 03 ships a complete systemd service with an environment file, a timer and a list of the
variables to put in it.

Run: python3 -m pytest -q tools
"""
import configparser
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "03-daily-job")
PAGE = os.path.join(ROOT, "docs", "how-to", "operate-daily.md")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section(heading):
    text = read(PAGE)
    match = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(heading), text, re.M | re.S)
    assert match, "no section '## %s'" % heading
    return match.group(1)


def test_the_page_has_the_three_sections():
    for heading in ("Watch it", "When something goes wrong", "Back up"):
        section(heading)


def test_watch_it_shows_two_ways_to_alert_and_an_independent_check():
    text = section("Watch it")
    assert "OnFailure=" in text and "systemd" in text
    assert re.search(r"cron", text) and ("MAILTO" in text or "wrapper" in text)
    assert "36 hours" in text and "published_at" in text and "stale_sources" in text and "central/published.json" in text
    assert "exit" in text.lower() and "other than 0" in text


def test_the_table_has_at_least_eight_if_you_see_do_rows_and_covers_the_cases():
    text = section("When something goes wrong")
    rows = [line for line in text.splitlines() if line.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", line)]
    header, rows = rows[0], rows[1:]
    assert "If you see" in header and "Do" in header
    assert len(rows) >= 8, len(rows)
    assert all(len([c for c in row.strip("|").split("|") if c.strip()]) == 2 for row in rows)
    joined = " ".join(rows)
    for needle in ("stale_source", "two days", "`2`", "`3`", "gold", "checksum", "frozen_entry_recovered", "forced_freeze", ".lock"):
        assert needle in joined, needle


def test_back_up_says_frozen_and_frozen_json_are_the_only_copy_and_how_to_restore():
    text = section("Back up")
    for needle in ("frozen/", "frozen.json", "only copy", "--step publish"):
        assert needle in text, needle


def test_the_example_ships_a_service_a_timer_and_the_variables():
    for name in ("meterline-job.service", "meterline-job.timer", "job.env.example"):
        assert os.path.isfile(os.path.join(EXAMPLE, name)), name


def _unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(read(os.path.join(EXAMPLE, name)))
    return parser


def test_the_service_unit_is_complete_and_reads_keys_from_a_private_environment_file():
    unit = _unit("meterline-job.service")
    assert unit["Unit"]["Description"] and unit["Unit"]["OnFailure"].endswith(".service")
    service = unit["Service"]
    assert service["Type"] == "oneshot"
    assert service["EnvironmentFile"].startswith("/") and not service["EnvironmentFile"].startswith("-")
    assert service["ExecStart"].split()[0].startswith("/") and "meterline-job run" in service["ExecStart"]
    assert service["WorkingDirectory"].startswith("/") and service["User"] != "root"
    assert "0600" in read(os.path.join(EXAMPLE, "meterline-job.service"))  # the file says the mode the environment file needs
    assert os.path.isfile(os.path.join(EXAMPLE, unit["Unit"]["OnFailure"]))


def test_the_timer_unit_runs_the_service_daily_and_catches_up():
    timer = _unit("meterline-job.timer")
    assert "OnCalendar" in timer["Timer"] and timer["Timer"]["Persistent"] == "true"
    assert timer["Install"]["WantedBy"] == "timers.target"


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed here; the unit is parsed instead")
def test_systemd_analyze_accepts_the_units(tmp_path):
    """The units are checked for syntax. The paths of the example (the program, the working folder, the environment file, the
    user) do not exist on the machine that runs the test, so what `systemd-analyze` says about a missing path or user is left out."""
    for name in os.listdir(EXAMPLE):
        if name.endswith((".service", ".timer")):
            shutil.copy(os.path.join(EXAMPLE, name), tmp_path / name)
    units = sorted(str(tmp_path / n) for n in os.listdir(tmp_path))
    done = subprocess.run(["systemd-analyze", "verify", *units], capture_output=True, text=True)
    about_the_machine = ("not executable", "No such file", "Failed to resolve user", "Failed to determine user", "does not exist",
                         "Unit meterline-job-alert.service not found")
    problems = [line for line in done.stderr.splitlines() if line.strip() and not any(w in line for w in about_the_machine)]
    assert problems == [], problems


def test_the_environment_example_lists_every_variable_with_no_value():
    lines = [l for l in read(os.path.join(EXAMPLE, "job.env.example")).splitlines() if l.strip() and not l.startswith("#")]
    names = []
    for line in lines:
        name, sep, value = line.partition("=")
        assert sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", name) and value == "", line  # nothing after the equals sign
        names.append(name)
    read_by_the_code = set()
    for pkg in os.listdir(os.path.join(ROOT, "packages")):
        src = os.path.join(ROOT, "packages", pkg, "src")
        for base, _dirs, files in os.walk(src):
            for f in files:
                if f.endswith(".py") and "conformance" not in f and "testing" not in base:
                    text = read(os.path.join(base, f))
                    read_by_the_code |= set(re.findall(r'(?:environ\.get\(|environ\[|getenv\()\s*"([A-Z][A-Z0-9_]+)"', text))
                    read_by_the_code |= set(re.findall(r'_ENV\w* = "([A-Z][A-Z0-9_]+)"', text))
    assert read_by_the_code, "the scan found no variable"
    assert set(names) >= read_by_the_code, sorted(read_by_the_code - set(names))
    assert {"ANTHROPIC_ANALYTICS_KEY", "ANTHROPIC_ADMIN_KEY", "LITELLM_API_KEY", "LITELLM_BASE_URL", "COPILOT_TOKEN",
            "COPILOT_BILLING_TOKEN", "METERLINE_GOLD_KEY"} <= set(names)
