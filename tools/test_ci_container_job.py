"""R-7 of the integrate part 1 spec: the CI job that builds and runs example 04's image.

The job runs on GitHub's hosted Ubuntu runner (outside the runner switch), only when the ``container`` output of the ``changes`` job
is true or on the weekly and manual runs, reads no ``secrets.*`` and uploads no artifact, and runs the steps that prove the image:
the README block, a forced failed run, the version check and the key check on the history, the settings and both logs. The path
rule that sets ``container`` is run on sample paths in tools/test_ci_suite.py.

Run: python3 -m pytest -q tools/test_ci_container_job.py
"""
import json
import os

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def workflow():
    with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def job_problems(job):
    """What breaks the job's rules: a secret read, an artifact upload, a runner other than ubuntu-latest, a missing guard."""
    text = json.dumps(job)
    bad = []
    if "secrets." in text:
        bad.append("reads a secret")
    if "upload-artifact" in text:
        bad.append("uploads an artifact")
    if job.get("runs-on") != "ubuntu-latest":
        bad.append("runs on %r, not ubuntu-latest" % job.get("runs-on"))
    condition = job.get("if", "")
    for needle in ("needs.changes.outputs.container == 'true'", "github.event_name == 'schedule'", "github.event_name == 'workflow_dispatch'"):
        if needle not in condition:
            bad.append("its condition lacks %s" % needle)
    return bad


def test_the_container_job_follows_its_rules():
    job = workflow()["jobs"]["container"]
    assert job_problems(job) == []
    assert job["needs"] == "changes" and "container" in workflow()["jobs"]["changes"]["outputs"]


def test_negative_control_a_secret_an_upload_or_the_switchable_runner_is_caught():
    job = workflow()["jobs"]["container"]
    planted = json.loads(json.dumps(job))
    planted["steps"].append({"run": "echo ${{ secrets.TOKEN }}"})
    planted["steps"].append({"uses": "actions/upload-artifact@v4"})
    planted["runs-on"] = "${{ vars.CI_RUNNER || 'ubuntu-latest' }}"
    del planted["if"]
    assert len(job_problems(planted)) == 6


def test_the_job_builds_runs_and_checks_the_image():
    steps = workflow()["jobs"]["container"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "docker build -f examples/04-container/Dockerfile" in runs
    assert 'tools/readme_block.py "Run it" --readme examples/04-container/README.md' in runs
    assert "tools/check_image_versions.py" in runs
    assert "docker history --no-trunc" in runs and "docker image inspect" in runs
    assert "tools/check_image_key.py METERLINE_GOLD_KEY" in runs
    # the key check reads the log of the success and the log of the forced failed run
    assert "container.log" in runs and "failed.log" in runs
    # the forced failed run must exit non-zero or the job fails
    assert "if docker run" in runs and "exit 1" in runs


def test_the_key_is_made_inside_the_job_and_the_run_block_is_not_traced():
    steps = workflow()["jobs"]["container"]["steps"]
    make = next(s for s in steps if "throwaway key" in s.get("name", ""))
    assert "openssl rand" in make["run"] and "GITHUB_ENV" in make["run"] and "echo \"METERLINE_GOLD_KEY" in make["run"]
    run_block = next(s for s in steps if s.get("name", "").startswith('Run the "Run it" block'))
    assert "set -euo pipefail" in run_block["run"]
    assert "-x" not in run_block["run"].replace("-xe", "").split("bash")[-1], "a trace would echo the key into the log"


def test_the_weekly_and_manual_runs_set_the_container_output():
    script = next(s for s in workflow()["jobs"]["changes"]["steps"] if s.get("id") == "diff")["run"]
    assert 'echo "container=${container}" >> "$GITHUB_OUTPUT"' in script
    assert "^(examples/04-container/|packages/|docs/integrate/)" in script


@pytest.mark.parametrize("name", ["tests", "minimum-versions"])
def test_the_jobs_that_run_the_tools_install_pyyaml(name):
    steps = workflow()["jobs"][name]["steps"]
    assert any("pyyaml" in s.get("run", "").lower() for s in steps), name
