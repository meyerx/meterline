"""CI runs on the runner the repository variable CI_RUNNER names while it is set, and on GitHub's hosted runner when
it is not. The weekly and manual runs, the long ones, run on the runner CI_RUNNER_HEAVY names when that is set, so
they can use the self-hosted runner while pull requests use hosted minutes. release.yml publishes to PyPI and always
stays GitHub-hosted.

Run: python3 -m pytest -q tools
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import in_working_repo  # noqa: E402

yaml = pytest.importorskip("yaml")

# The export rewrites these workflows for the public repository: every runner becomes ubuntu-latest and the self-hosted
# steps are dropped (export_public.public_workflow), so the switch below exists only in the working repository.
pytestmark = pytest.mark.skipif(not in_working_repo(), reason="the public tree has no runner switch (the export removes it)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWITCH = ("${{ (github.event_name == 'schedule' || github.event_name == 'workflow_dispatch') && vars.CI_RUNNER_HEAVY"
          " || vars.CI_RUNNER || 'ubuntu-latest' }}")


def jobs(name):
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)["jobs"]


# The one job that stays off the switch: it builds a Docker image, and the self-hosted runner may have no Docker.
# tools/test_ci_container_job.py holds the job to GitHub's hosted runner and to its other rules.
EXEMPT_FROM_THE_SWITCH = {"container": "ubuntu-latest"}


def test_every_ci_job_runs_on_the_switchable_runner():
    wrong = {job_id: job.get("runs-on") for job_id, job in jobs("ci.yml").items()
             if job.get("runs-on") != EXEMPT_FROM_THE_SWITCH.get(job_id, SWITCH)}
    assert jobs("ci.yml") and wrong == {}


def test_only_the_container_job_is_exempt_and_it_is_in_the_workflow():
    assert set(EXEMPT_FROM_THE_SWITCH) == {"container"} and set(EXEMPT_FROM_THE_SWITCH) <= set(jobs("ci.yml"))


def test_every_release_job_stays_github_hosted():
    wrong = {job_id: job.get("runs-on") for job_id, job in jobs("release.yml").items() if job.get("runs-on") != "ubuntu-latest"}
    assert jobs("release.yml") and wrong == {}


def test_self_hosted_jobs_use_the_runner_pyenv_python_instead_of_setup_python():
    """actions/setup-python cannot install Python on the self-hosted runner (its installer calls sudo), so each job that
    uses it runs it on GitHub-hosted runners only, and a self-hosted step builds a venv from pyenv right after it."""
    checked = 0
    for job_id, job in jobs("ci.yml").items():
        steps = job["steps"]
        setups = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/setup-python@")]
        for i in setups:
            checked += 1
            assert steps[i].get("if") == "runner.environment == 'github-hosted'", job_id
            nxt = steps[i + 1] if i + 1 < len(steps) else {}
            assert nxt.get("if") == "runner.environment == 'self-hosted'", job_id
            assert nxt.get("env", {}).get("PYTHON_VERSION") == str(steps[i]["with"]["python-version"]), job_id
            # directly after setup-python, so it precedes every step that runs python, pip or uv
            assert i + 1 < len(steps)
    assert checked > 0
