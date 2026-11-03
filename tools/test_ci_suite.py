"""tools/ci_suite.py decides which tests a pull request runs from the paths it changes, and ci.yml acts on it: a
narrower suite only where no test the narrower suite leaves out can read the change.

The wiring tests run the `changes` job's own script, taken from ci.yml, in a throwaway git repository, once per
event, and read what it writes to GITHUB_OUTPUT.

Run: python3 -m pytest -q tools/test_ci_suite.py
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ci_suite  # noqa: E402

REGISTER = next(iter(ci_suite.NO_TEST))

READER = textwrap.dedent('''
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[3]

    def test_reads_records():
        assert (ROOT / "docs/records.md").read_text()

    def test_reads_glossary():
        assert (ROOT / "docs" / "glossary.md").read_text()
''')


@pytest.fixture
def repo(tmp_path):
    """A repository whose one package test reads docs/records.md by path and glossary.md by bare name."""
    tests = tmp_path / "packages" / "meterline-x" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_docs.py").write_text(READER, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("paths, expected", [
    ([], "full"),
    ([REGISTER], "none"),
    (["docs/how-to/operate-daily.md"], "tools"),
    ([REGISTER, "docs/dev/RELEASING.md"], "tools"),
    (["llms.txt"], "tools"),
    (["docs/records.md"], "full"),           # a package test reads it by path
    (["docs/glossary.md"], "full"),          # a package test reads it by bare name
    (["docs/records.md", "docs/old/records-v1.md"], "full"),  # a rename, listed under both names (--no-renames)
    (["packages/meterline-x/README.md"], "full"),
    (["docs/how-to/operate-daily.md", "tools/ci_suite.py"], "full"),
    (["docs/gold-fixture/input/policy.yaml"], "full"),
])
def test_suite_from_the_changed_paths(repo, paths, expected):
    assert ci_suite.suite(paths, root=repo) == expected


def test_a_new_package_test_that_reads_a_document_widens_the_suite_the_day_it_lands(repo):
    assert ci_suite.suite(["docs/how-to/operate-daily.md"], root=repo) == "tools"
    (repo / "packages" / "meterline-x" / "tests" / "test_new.py").write_text(
        'def test_new():\n    assert open("docs/how-to/operate-daily.md").read()\n', encoding="utf-8")
    assert ci_suite.suite(["docs/how-to/operate-daily.md"], root=repo) == "full"


def test_the_tools_suite_runs_every_package_test_that_walks_the_repository(repo):
    (repo / "packages" / "meterline-x" / "tests" / "test_scan.py").write_text(textwrap.dedent('''
        import ast, os, subprocess
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[3]

        def tracked(root):
            return subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True).stdout

        def test_repo_scan():
            for current, dirs, names in os.walk(ROOT):
                pass

        def test_scan_through_a_helper():
            assert tracked(ROOT) is not None

        def test_parses_code():
            for node in ast.walk(ast.parse("x = 1")):
                pass
    '''), encoding="utf-8")
    assert ci_suite.tools_targets(root=repo) == [
        "tools",
        "packages/meterline-x/tests/test_scan.py::test_repo_scan",
        "packages/meterline-x/tests/test_scan.py::test_scan_through_a_helper",
    ]


def test_this_repository_the_known_readers_and_the_known_scans():
    """A canary on this repository's own tests: a rename of one of these files or tests fails it on purpose."""
    assert ci_suite.suite(["docs/records.md"]) == "full"
    assert ci_suite.suite(["docs/reference/gold-datasets.md"]) == "full"
    assert ci_suite.suite(["README.md"]) == "full"  # packages/meterline/tests/test_standards_mapping.py reads it
    assert ci_suite.suite(["docs/dev/RELEASING.md"]) == "tools"
    targets = ci_suite.tools_targets()
    assert "packages/meterline-anthropic/tests/test_done_check.py::test_done_check_11_repo_scan_clean" in targets
    assert "packages/meterline-gold/tests/test_review_f42_suppression_bounds.py::" \
        "test_keys_stay_out_of_git_no_tracked_file_holds_a_key" in targets


# ---- the workflow ----

def _ci():
    with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _changes_script():
    steps = _ci()["jobs"]["changes"]["steps"]
    return next(s for s in steps if s.get("id") == "diff")["run"]


def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True)
    return out.stdout.strip()


@pytest.fixture
def pr_repo(tmp_path):
    """A git repository whose base commit holds the real tools/ci_suite.py, a package test that reads
    docs/records.md, and a few documents; returns (repo, base sha) with a branch checked out off the base."""
    if shutil.which("git") is None or shutil.which("bash") is None:
        pytest.skip("needs git and bash")
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "tools", "ci_suite.py"), repo / "tools" / "ci_suite.py")
    tests = repo / "packages" / "meterline-x" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_docs.py").write_text(READER, encoding="utf-8")
    (repo / "docs").mkdir()
    for name in ("records.md", "glossary.md", "guide.md"):
        (repo / "docs" / name).write_text("# %s\n\n%s\n" % (name, "text line\n" * 40), encoding="utf-8")
    (repo / REGISTER).write_text("| row |\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "pr")
    return repo, base


def _run_changes(repo, event, base="", head=""):
    out = repo.parent / "github_output"
    out.write_text("")
    temp = repo.parent / "runner_temp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, EVENT=event, PR_BASE=base, PR_HEAD=head, GITHUB_OUTPUT=str(out), RUNNER_TEMP=str(temp))
    run = subprocess.run(["bash", "-e", "-c", _changes_script()], cwd=repo, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)


def _commit(repo, message="change"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("event, suite, minimum", [
    ("push", "none", "false"),
    ("schedule", "full", "true"),
    ("workflow_dispatch", "full", "true"),
])
def test_changes_job_outside_pull_requests(pr_repo, event, suite, minimum):
    repo, _ = pr_repo
    outputs = _run_changes(repo, event)
    assert (outputs["suite"], outputs["minimum_versions"]) == (suite, minimum)


def test_changes_job_prose_only_pull_request(pr_repo):
    repo, base = pr_repo
    (repo / "docs" / "guide.md").write_text("# guide\n\nedited\n", encoding="utf-8")
    outputs = _run_changes(repo, "pull_request", base, _commit(repo))
    assert outputs["suite"] == "tools"
    assert outputs["targets"].split()[0] == "tools"


def test_changes_job_backlog_only_pull_request(pr_repo):
    repo, base = pr_repo
    (repo / REGISTER).write_text("| row |\n| row 2 |\n", encoding="utf-8")
    assert _run_changes(repo, "pull_request", base, _commit(repo))["suite"] == "none"


def test_changes_job_sees_a_renamed_document_under_its_old_name(pr_repo):
    repo, base = pr_repo
    _git(repo, "mv", "docs/records.md", "docs/records-old.md")
    assert _run_changes(repo, "pull_request", base, _commit(repo))["suite"] == "full"


def test_changes_job_runs_the_base_script_so_a_pull_request_cannot_narrow_its_own_tests(pr_repo):
    repo, base = pr_repo
    (repo / "tools" / "ci_suite.py").write_text('print("none")\n', encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("# guide\n\nedited\n", encoding="utf-8")
    assert _run_changes(repo, "pull_request", base, _commit(repo))["suite"] == "full"


def test_changes_job_falls_back_to_the_full_suite_on_an_unexpected_answer(pr_repo):
    repo, _ = pr_repo
    (repo / "tools" / "ci_suite.py").write_text('print("everything is fine\\nsuite=none")\n', encoding="utf-8")
    base = _commit(repo, "a base whose script answers something else")
    (repo / "docs" / "guide.md").write_text("# guide\n\nedited\n", encoding="utf-8")
    outputs = _run_changes(repo, "pull_request", base, _commit(repo))
    assert outputs["suite"] == "full"


@pytest.mark.parametrize("targets_answer", ['import sys; sys.exit(2)', 'print("tools; id")'])
def test_changes_job_falls_back_to_the_full_suite_when_the_targets_fail_or_look_wrong(pr_repo, targets_answer):
    """The narrower suite needs the repository scans the targets name, so it never runs without them."""
    repo, _ = pr_repo
    (repo / "tools" / "ci_suite.py").write_text(
        'import sys\nif "--targets" in sys.argv:\n    %s\nelse:\n    print("tools")\n' % targets_answer,
        encoding="utf-8")
    base = _commit(repo, "a base whose script answers tools but not its targets")
    (repo / "docs" / "guide.md").write_text("# guide\n\nedited\n", encoding="utf-8")
    outputs = _run_changes(repo, "pull_request", base, _commit(repo))
    assert (outputs["suite"], outputs["targets"]) == ("full", "tools")


def test_changes_job_falls_back_to_the_full_suite_when_the_base_has_no_script(pr_repo):
    repo, _ = pr_repo
    _git(repo, "rm", "-q", "tools/ci_suite.py")
    base = _commit(repo, "a base without the script")
    (repo / "tools").mkdir(exist_ok=True)
    shutil.copy(os.path.join(ROOT, "tools", "ci_suite.py"), repo / "tools" / "ci_suite.py")
    (repo / "docs" / "guide.md").write_text("# guide\n\nedited\n", encoding="utf-8")
    assert _run_changes(repo, "pull_request", base, _commit(repo))["suite"] == "full"


def test_the_tests_job_always_reports_its_check_and_its_steps_follow_the_suite():
    tests = _ci()["jobs"]["tests"]
    assert "if" not in tests  # a skipped matrix job reports under its unexpanded name
    by_if = {}
    for step in tests["steps"]:
        # the commit-message steps also carry a hashFiles guard (the export leaves that file out): the suite condition leads
        key = str(step.get("if")).split(" && hashFiles(")[0] if " && hashFiles(" in str(step.get("if")) else step.get("if")
        by_if.setdefault(key, []).append(step.get("run", ""))
    assert any("pytest" in r and "packages tools" in r for r in by_if["needs.changes.outputs.suite == 'full'"])
    assert any("${TARGETS}" in r for r in by_if["needs.changes.outputs.suite == 'tools'"])
    assert any("tools/test_commit_messages.py" in r for r in by_if["needs.changes.outputs.suite == 'none'"])
    install = next(s for s in tests["steps"] if "-e \"packages/meterline[" in s.get("run", ""))
    assert install["if"] == "needs.changes.outputs.suite != 'none'"


def test_the_heavy_jobs_follow_the_suite_and_the_long_ones_run_weekly_and_manually():
    jobs = _ci()["jobs"]
    assert jobs["audit"]["if"] == "needs.changes.outputs.suite == 'full'"
    assert jobs["dashboard-a11y"]["if"] == "needs.changes.outputs.suite == 'full'"
    assert jobs["quickstart"]["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    for light in ("changes", "lint", "secrets"):
        assert "if" not in jobs[light], light


def test_a_merge_never_replaces_a_pending_weekly_or_manual_run():
    assert "github.event_name" in _ci()["concurrency"]["group"]
