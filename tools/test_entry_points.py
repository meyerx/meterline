"""B-4 and B-8 of the v0.3 hardening spec: the entry points know the daily job, and the evidence date is kept in one place.

AGENTS.md, llms.txt and the root README are where a person or an agent starts. They name `meterline-job`, the exit code 4, the
job's schemas and the goal "run the daily job and serve `published/` per reader"; the job README and the code's own words
(the `--step` line, the exit table, the docstring, the package description) say the same as the code; no document states a test
count; and the date of the last live run is written once.

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
import export_public as ep  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def exported(suffixes):
    listed = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    return sorted(f for f in listed if f.endswith(suffixes) and f.split("/")[0] in ep.INCLUDE and f not in ep.EXCLUDE
                  and not f.startswith("docs/dev/") and os.path.isfile(os.path.join(ROOT, f)))


def test_the_three_entry_points_name_the_job():
    for rel in ("AGENTS.md", "llms.txt", "README.md"):
        assert "meterline-job" in read(rel), rel


def test_agents_md_has_the_install_line_the_goal_path_exit_4_and_the_job_schemas():
    text = read("AGENTS.md")
    assert 'pip install -e packages/meterline-job' in text or 'meterline-job[all]' in text
    assert re.search(r"run the daily job and serve `published/` per reader", text, re.I)
    assert re.search(r"`4`[^\n]*(job|gold|publish)", text), "exit code 4 is missing from the exit codes"
    assert "docs/schemas/job/" in text
    assert "## The five goal paths" in text and "## The four goal paths" not in text


def test_llms_txt_links_the_job_readme_and_the_job_schemas():
    text = read("llms.txt")
    assert "packages/meterline-job/README.md" in text
    assert "docs/schemas/job/" in text


def test_the_root_readme_has_an_install_from_pypi_block():
    text = read("README.md")
    assert "Install from PyPI" in text and 'pip install "meterline-job[all]"' in text
    block = text.split("Install from PyPI", 1)[1][:800]
    assert "packages/meterline-job/README.md" in block or "meterline-job/README.md" in block


def test_the_job_readme_step_line_and_exit_table_match_the_code():
    text = read("packages/meterline-job/README.md")
    step_line = next(l for l in text.splitlines() if l.startswith("`run` runs every connector"))
    assert "`publish`" in step_line and "`silver`" in step_line and "`gold`" in step_line
    exit4 = next(l for l in text.splitlines() if l.startswith("| `4` |"))
    assert "publish" in exit4 and "build" in exit4 and "skipped" in text and "`publish`" in text
    assert "Code 4 exists only on the job's step lines and exit code." in text


def test_the_job_docstring_and_package_description_name_publish():
    code = read("packages/meterline-job/src/meterline_job/job.py").split('"""', 2)[1]
    assert "publish step" in code and "``publish``" in code and "``skipped``" in code
    meta = tomllib.loads(read("packages/meterline-job/pyproject.toml"))["project"]
    assert "publish" in meta["description"].lower()


def test_example_03_readme_states_no_test_count():
    text = read("examples/03-daily-job/README.md")
    assert re.search(r"(?i)\ball (of them|the tests?) pass\b", text), "say that all of them pass"


def test_no_exported_file_states_a_test_count():
    pattern = re.compile(r"\b\d+(?:,\d{3})*\s+(?:tests\b|passed\b)|\ball\s+\d+\s+(?:tests|checks)\b", re.I)
    found = []
    for rel in exported((".md", ".txt")):
        for n, line in enumerate(read(rel).splitlines(), 1):
            if pattern.search(line):
                found.append("%s:%d: %s" % (rel, n, line.strip()[:100]))
    assert found == []


# ---- B-8: the date of the last live run is written once -----------------------------------------------------------------

DATE = r"\d{4}-\d{2}-\d{2}"


def test_the_evidence_date_appears_in_exactly_one_exported_file():
    phrases = re.compile(r"(?i)last live run[^\n.]{0,20}%s|evidence levels? as of %s|as of %s, only" % (DATE, DATE, DATE))
    files = [rel for rel in exported((".md", ".txt")) if phrases.search(read(rel))]
    assert files == ["README.md"], files


def test_agents_and_llms_link_to_the_one_place():
    for rel in ("AGENTS.md", "llms.txt"):
        text = read(rel)
        assert re.search(r"README\.md#status|README\.md\)|\]\(README\.md|blob/main/README\.md", text), rel
        assert "date of the last live run" in text, rel
