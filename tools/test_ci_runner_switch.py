"""CI runs on the self-hosted runner while the repository variable CI_RUNNER is set, and on GitHub's hosted
runner when it is not. release.yml publishes to PyPI and always stays GitHub-hosted.

Run: python3 -m pytest -q tools
"""
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWITCH = "${{ vars.CI_RUNNER || 'ubuntu-latest' }}"


def jobs(name):
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)["jobs"]


def test_every_ci_job_runs_on_the_switchable_runner():
    wrong = {job_id: job.get("runs-on") for job_id, job in jobs("ci.yml").items() if job.get("runs-on") != SWITCH}
    assert jobs("ci.yml") and wrong == {}


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
