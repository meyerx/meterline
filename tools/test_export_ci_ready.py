"""The public tree carries every repository-root file that CI and the tests read by name, and its core suite passes there.

A file left off the export allowlist leaves the first public commit with red CI, and nothing in the private repository shows it.
Run: python3 -m pytest -q tools"""
import os
import pathlib
import re
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml")

sys.path.insert(0, os.path.dirname(__file__))
import export_public as ep  # noqa: E402
from package_list import in_working_repo  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Root files that stay private on purpose; the exporter's own scan rejects the first two by name.
PRIVATE_ROOT_FILES = {"CLAUDE" + ".md", "BACKLOG" + "-std.md", ".gitleaksignore"}


def _tracked(*args):
    return subprocess.run(["git", "ls-files", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\n")


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    out = tmp_path_factory.mktemp("export") / "public"
    denylist = tmp_path_factory.mktemp("terms") / "terms.txt"
    denylist.write_text("no-such" + "-term-in-the-tree\n")  # assembled, so this file does not match its own term
    code = ep.main(["--repo", str(ROOT), "--ref", "HEAD", "--out", str(out), "--denylist", str(denylist), "--scan-only"])
    assert code == 0
    return out


def _readers():
    """The files whose content names root files: workflows, packages/conftest.py and the tools tests that are exported."""
    files = [f for f in _tracked(".github/workflows") if f and f not in ep.EXCLUDE]  # a workflow the export leaves out runs nowhere public
    files += ["packages/conftest.py"]
    files += [f for f in _tracked("tools") if re.fullmatch(r"tools/test_[^/]+\.py", f) and f not in ep.EXCLUDE]
    return files


# Config files a tool reads from the root without anyone naming them: pytest finds pytest.ini by itself, and that file
# makes the root the confcutdir, so packages/conftest.py (the network guard) loads on a per-package run.
IMPLIED = {"pytest.ini": r"\bpytest\b"}


def _root_files_read_by_name():
    root_files = [f for f in _tracked() if f and "/" not in f and f not in PRIVATE_ROOT_FILES]
    found = {}
    for reader in _readers():
        text = (ROOT / reader).read_text(encoding="utf-8", errors="replace")
        for name in root_files:
            if re.search(r"(?<![\w./-])" + re.escape(name) + r"(?![\w-])", text):
                found.setdefault(name, []).append(reader)
    for name, command in IMPLIED.items():
        for reader in _readers():
            if name in root_files and reader.startswith(".github/workflows/") and re.search(command, (ROOT / reader).read_text(encoding="utf-8")):
                found.setdefault(name, []).append(reader)
    return found


def test_the_derivation_finds_the_known_config_files():
    assert {"pytest.ini", "ruff.toml"} <= set(_root_files_read_by_name())


def test_every_root_file_that_ci_or_a_test_reads_is_exported(exported):
    missing = {n: r[:2] for n, r in _root_files_read_by_name().items() if not (exported / n).is_file()}
    assert not missing, "read by name but not in the exported tree (add to INCLUDE in tools/export_public.py): %s" % missing


def test_core_suite_passes_in_the_exported_tree(exported):
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "packages/meterline", "-p", "no:cacheprovider"],
                       cwd=exported, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]


# ---- a workflow step that names a file the export drops must not run where the file is missing ----

def unguarded_references(workflows, dropped):
    """``{workflow name: yaml text}`` and the tracked files the export drops -> ``"workflow: job: step names path"`` for each
    step whose run, name, env or with names a dropped path and whose ``if`` has no ``hashFiles('<path>') != ''`` for it.
    Without the guard the step runs in the public tree, finds no file and turns the public CI red."""
    problems = []
    for name, text in sorted(workflows.items()):
        for job_id, job in (yaml.safe_load(text).get("jobs") or {}).items():
            for step in job.get("steps", []):
                body = yaml.safe_dump({k: v for k, v in step.items() if k != "if"})
                condition = str(step.get("if", ""))
                for path in sorted(dropped):
                    named = re.search(r"(?<![\w./-])" + re.escape(path) + r"(?![\w-])", body)
                    if named and ("hashFiles('%s') != ''" % path) not in condition:
                        problems.append("%s: %s: %s names %s without a hashFiles guard" % (name, job_id, step.get("name", step.get("uses", "?")), path))
    return problems


def _dropped_tracked_files(exported):
    """The tracked files the export leaves out of the tree: EXCLUDE, the private root files, and anything outside INCLUDE."""
    kept = {str(p.relative_to(exported)).replace(os.sep, "/") for p in exported.rglob("*") if p.is_file()}
    return {f for f in _tracked() if f and f not in kept}


def _workflows():
    return {os.path.basename(f): (ROOT / f).read_text(encoding="utf-8") for f in _tracked(".github/workflows") if f and f not in ep.EXCLUDE}


def test_every_workflow_step_that_names_a_dropped_file_is_guarded(exported):
    if not (ROOT / "tools" / "test_commit_messages.py").exists() and not in_working_repo():
        pytest.skip("the public tree has no commit-message test: it is the file this guard exists for, and it is already dropped")
    dropped = _dropped_tracked_files(exported)
    assert "tools/test_commit_messages.py" in dropped, "the derivation lost the file this guard exists for"
    assert unguarded_references(_workflows(), dropped) == []


def test_the_check_flags_a_step_whose_hashfiles_guard_was_removed():
    # negative control: ci.yml as it is, with the guard taken off the commit-message step
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    guard = " && hashFiles('tools/test_commit_messages.py') != ''"
    assert guard in text, "ci.yml no longer carries the guard this control removes"
    flagged = unguarded_references({"ci.yml": text.replace(guard, "")}, {"tools/test_commit_messages.py"})
    assert flagged and all("tools/test_commit_messages.py" in f for f in flagged), flagged
    assert unguarded_references({"ci.yml": text}, {"tools/test_commit_messages.py"}) == []


# ---- the public tree has no self-hosted runner and no runner variable (the host runs a stranger's pull request there) ----

def test_the_exported_workflows_name_no_self_hosted_runner_and_no_runner_variable(exported):
    found = sorted(str(p.relative_to(exported)) for p in (exported / ".github").rglob("*")
                   if p.is_file() and re.search(r"self-hosted|CI_RUNNER", p.read_text(encoding="utf-8", errors="replace")))
    assert found == [], "the export must rewrite these (runs-on to ubuntu-latest, no self-hosted step): %s" % found


_PLANTED = """jobs:
  a:
    runs-on: ${{ (github.event_name == 'schedule') && vars.CI_RUNNER_HEAVY || vars.CI_RUNNER || 'ubuntu-latest' }}
    steps:
      - uses: actions/checkout@x
      - uses: actions/setup-python@x
        if: runner.environment == 'github-hosted'
      - name: Use the runner's pyenv Python (self-hosted)
        if: runner.environment == 'self-hosted'
        run: |
          echo one
          echo two

      - name: Keep me
        run: echo three
  # export:omit-begin
  # a comment that names the runner
  # export:omit-end
  b:
    runs-on: ubuntu-latest
    steps:
      - name: Last step is the self-hosted one
        if: runner.environment == 'self-hosted'
        run: echo four
"""


def test_the_public_workflow_has_no_runner_switch_no_self_hosted_step_and_no_omitted_comment():
    out = ep.public_workflow(_PLANTED)
    assert not re.search(r"self-hosted|CI_RUNNER|github-hosted|omit|pyenv|echo (one|two|four)", out), out
    assert "runs-on: ubuntu-latest" in out and out.count("runs-on:") == 2
    assert "Keep me" in out and "actions/setup-python@x" in out and "echo three" in out
    assert yaml.safe_load(out)["jobs"]["a"]["runs-on"] == "ubuntu-latest"
    assert [s.get("name") for s in yaml.safe_load(out)["jobs"]["a"]["steps"]] == [None, None, "Keep me"]


@pytest.mark.parametrize("text", ["# export:omit-begin\nx: 1\n", "x: 1\n# export:omit-end\n", "# export:omit-begin\n# export:omit-begin\n# export:omit-end\n"])
def test_an_unpaired_omit_marker_stops_the_export(text):
    with pytest.raises(SystemExit):
        ep.public_workflow(text)


def test_the_dependency_update_file_is_not_exported(exported):
    if not (ROOT / ".github" / "dependabot.yml").exists() and not in_working_repo():
        pytest.skip("the public tree has no dependency-update file: there is nothing left to leave out of an export")
    assert ".github/dependabot.yml" in ep.EXCLUDE
    assert (ROOT / ".github" / "dependabot.yml").is_file() and not (exported / ".github" / "dependabot.yml").exists()


@pytest.mark.parametrize("path", [".github/workflows/claude.yml", ".github/ISSUE_TEMPLATE/task.yml"])
def test_the_owners_agent_route_files_are_not_exported(tmp_path, path):
    if not (ROOT / path).exists() and not in_working_repo():
        pytest.skip("the public tree has no %s: it is the file this guard exists for, and it is already left out" % path)
    # The issue-to-pull-request workflow and its issue form belong to the working repository only. This runs its own
    # scan-only export (not the shared fixture) and reads the tree whatever the scan says, so it names the file.
    denylist = tmp_path / "terms.txt"
    denylist.write_text("no-such" + "-term-in-the-tree\n")
    out = tmp_path / "public"
    ep.main(["--repo", str(ROOT), "--ref", "HEAD", "--out", str(out), "--denylist", str(denylist), "--scan-only"])
    assert (ROOT / path).is_file(), "the file this guard exists for is gone: update the guard, not the export"
    assert not (out / path).exists(), "%s would be published" % path
    assert path in ep.EXCLUDE
