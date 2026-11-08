"""Checks that the files a stranger meets first (licence, policy files, quick start, README) are in place.

Run: python3 -m pytest -q tools
"""
import os
import re

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_list import ROOT, packages  # noqa: E402
from wiring import BY_ID  # noqa: E402  the places a package must be named in, shared with tools/check_new_connector.py

PACKAGES = packages()


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_every_package_folder_is_found_and_whole():
    folders = sorted(d for d in os.listdir(os.path.join(ROOT, "packages")) if os.path.isdir(os.path.join(ROOT, "packages", d))
                     and not d.startswith((".", "_")))
    assert PACKAGES == folders, "a folder under packages/ has no pyproject.toml"
    assert "meterline" in PACKAGES and len(PACKAGES) > 1
    for pkg in PACKAGES:
        for name in ("README.md", "LICENSE"):
            assert os.path.isfile(os.path.join(ROOT, "packages", pkg, name)), "%s lacks %s" % (pkg, name)


def test_licence_is_apache_2_with_the_owner_as_holder():
    text = read("LICENSE")
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    assert "Copyright 2026 Xavier Meyer" in text
    assert "[yyyy]" not in text and "[name of copyright owner]" not in text


def test_every_package_declares_the_licence_and_ships_it():
    root_licence = read("LICENSE")
    for pkg in PACKAGES:
        meta = read("packages", pkg, "pyproject.toml")
        assert re.search(r'^license = "Apache-2\.0"$', meta, re.M), pkg
        assert re.search(r'^license-files = \["LICENSE"\]$', meta, re.M), pkg
        # PEP 639 license expressions need setuptools 77.0.0 or later
        assert re.search(r'requires = \["setuptools==\d+(\.\d+)*"\]', meta), pkg  # an exact pin (v0.3 hardening, C-2)
        # license-files globs are relative to the package directory, so each carries a copy
        assert read("packages", pkg, "LICENSE") == root_licence, pkg


def _include():
    import ast
    line = [x for x in read("tools", "export_public.py").splitlines() if x.startswith("INCLUDE = ")][0]
    return ast.literal_eval(line.split("INCLUDE = ", 1)[1])


def test_open_source_files_exist_and_are_exported():
    for name in ("SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md"):
        assert read(name).strip(), name
        assert name in _include(), name


def test_security_policy_names_a_private_route_and_the_supported_versions():
    text = read("SECURITY.md").lower()
    assert "security advisor" in text or "private vulnerability reporting" in text
    assert "supported versions" in text
    assert "do not open a public issue" in text


def test_contributing_covers_branch_pull_request_tests_and_synthetic_data():
    text = read("CONTRIBUTING.md").lower()
    for word in ("branch", "pull request", "pytest", "synthetic", "apache"):
        assert word in text, word


def test_changelog_follows_keep_a_changelog_and_covers_every_package():
    text = read("CHANGELOG.md")
    assert "keepachangelog.com" in text and "## [Unreleased]" in text
    for pkg in PACKAGES:
        assert "`%s`" % pkg in text, pkg
        assert BY_ID["changelog"].missing(ROOT, pkg) == [], pkg


def test_readme_says_the_project_is_not_affiliated_with_the_providers():
    text = read("README.md")
    assert "not affiliated" in text
    for name in ("Anthropic", "GitHub", "Microsoft", "LiteLLM"):
        assert name in text.split("not affiliated", 1)[1][:600], name


# ---- the quick start (README) and the CI job that runs it ----

def _block(heading):
    sys_path = os.path.join(ROOT, "tools")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    import readme_block
    return readme_block.block_after(read("README.md"), heading)


def test_quick_start_installs_every_package_and_runs_the_tests():
    block = _block("Quick start")
    pip = [x for x in block.splitlines() if "pip install" in x and "-e" in x]
    assert len(pip) == 1
    for pkg in PACKAGES:
        assert "packages/%s" % pkg in pip[0], pkg
        assert BY_ID["readme-quick-start"].missing(ROOT, pkg) == [], pkg  # as a whole name: -anthropic is not -anthropic-admin
    assert "packages/meterline-gold[dev]" in pip[0]
    lines = block.splitlines()
    assert lines[0] == "python3 -m venv .venv"
    assert ".venv/bin/python -m pytest -q packages" in lines
    assert "--upgrade pip" in block


def test_quick_start_reaches_sim_and_gold_without_a_key():
    block = _block("Try it without a key")
    assert ".venv/bin/meterline-sim generate" in block and ".venv/bin/meterline-gold build" in block
    assert "--silver sim-out/tiny-s7-m6 " in block and "--org-files sim-out/tiny-s7-m6/org" in block
    assert "ANTHROPIC" not in block


def test_a_ci_job_runs_the_readme_blocks_in_a_fresh_checkout():
    ci = read(".github", "workflows", "ci.yml")
    assert re.search(r"^  quickstart:", ci, re.M)
    job = ci.split("\n  quickstart:", 1)[1].split("\n  lint:", 1)[0]
    assert 'tools/readme_block.py "Quick start"' in job
    assert 'tools/readme_block.py "Try it without a key"' in job


def test_anthropic_readme_scopes_the_rate_flag_to_the_commands_that_call_the_api():
    text = read("packages", "meterline-anthropic", "README.md")
    assert "`--rate-per-minute` applies to `backfill` and `daily`" in text


# ---- no private working traces in the exported tree ----

PRIVATE_TRACE = re.compile(r"METR-?[0-9]+|BACKLOG" r"-std|CLAUDE\.m" r"d|[Pp]arked by|/Users/|/home/")
EXPORTED_TEXT = ("README.md", "AGENTS.md", "llms.txt", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md")


def _exported_files():
    """The committed files of docs/ and packages/, plus the top-level text files: what the export ships."""
    import subprocess
    import export_public  # the files the export leaves out are not "shipped"
    left_out = set(export_public.EXCLUDE)
    listed = subprocess.run(["git", "ls-files", "-z", "docs", "packages"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.split("\0")
    for rel in listed:
        if rel and rel not in left_out and os.path.exists(os.path.join(ROOT, rel)):
            yield os.path.join(ROOT, rel)
    for name in EXPORTED_TEXT:
        yield os.path.join(ROOT, name)


def test_no_private_working_trace_in_the_exported_tree():
    bad = []
    for path in _exported_files():
        if path.endswith((".pyc", ".gz", ".parquet", ".png")):
            continue
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
        for n, line in enumerate(text.splitlines(), 1):
            if PRIVATE_TRACE.search(line):
                bad.append("%s:%d" % (os.path.relpath(path, ROOT), n))
    assert not bad, bad[:20]


def test_no_file_name_carries_a_ticket_id():
    names = [os.path.relpath(p, ROOT) for p in _exported_files()]
    assert not [n for n in names if re.search(r"METR", n)]


def test_readme_repo_map_lists_only_files_the_export_ships():
    section = read("README.md").split("## Repo map", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```\n(.*?)```", section, re.S).group(1)
    shipped = set(_include())
    listed = [x.split()[0] for x in block.splitlines() if x and not x.startswith(" ")]
    assert listed
    for entry in listed:
        path = entry.rstrip("/")
        assert path.split("/")[0] in shipped, entry
        assert os.path.exists(os.path.join(ROOT, path)), entry
    for pkg in PACKAGES:
        assert "packages/%s/" % pkg in listed, pkg
        assert BY_ID["readme-repo-map"].missing(ROOT, pkg) == [], pkg


def test_exported_text_points_only_at_exported_directories():
    for name in ("README.md", "CONTRIBUTING.md"):
        text = read(name)
        assert "python -m pytest -q tools" not in text, name


# ---- no hand-maintained counts in the README ----

def test_readme_states_no_test_or_check_counts():
    text = read("README.md")
    assert not re.search(r"\b(340|430|90|140)\b", text)
    assert not re.search(r"\b\d+ (tests?|test items|done-checks?|acceptance tests?|checks?)\b", text)
    assert not re.search(r"exactly \d", text)
    # one home for the run time: the Quick start
    assert text.count("about twenty") == 1 and "fifteen minutes" not in text


# ---- the public repository ships its own CI and the tools that CI runs ----

def test_the_export_ships_ci_and_the_tools_ci_runs():
    shipped = set(_include())
    assert {".github", "tools", "docs"} <= shipped


def test_the_exported_tree_scans_clean_with_no_denylist_hits(tmp_path):
    """The whole allowlisted tree (CI and tools included) passes the scanner as committed."""
    import subprocess
    import sys
    term_file = tmp_path / "terms.txt"
    term_file.write_text("zzz-not" + "-a-term\n")
    run = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "export_public.py"), "--scan-only",
                          "--repo", ROOT, "--ref", "HEAD", "--out", str(tmp_path / "pub"),
                          "--denylist", str(term_file)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-800:]


def test_ci_runs_the_tools_tests_with_the_package_tests():
    ci = read(".github", "workflows", "ci.yml")
    assert re.search(r"pytest[^#\n]*packages tools", ci)


# ---- the release checklist, pre-mortem and scan limits ----

def _section(text, heading):
    return text.split("\n## " + heading, 1)[1].split("\n## ", 1)[0]


def test_releasing_doc_has_the_checklist_premortem_and_limits():
    doc = read("docs", "dev", "RELEASING.md")
    steps = _section(doc, "Release checklist")
    assert len(re.findall(r"^\d+\. ", steps, re.M)) >= 7
    text = steps.lower()
    order = ["--scan-only", "review", "--author-name", "empty public repository", "push",
             "secret scanning", "push protection", "branch protection"]
    at = [text.find(k) for k in order]
    assert all(i >= 0 for i in at), dict(zip(order, at))
    assert at == sorted(at)
    scenarios = re.findall(r"^### .*", _section(doc, "Pre-mortem"), re.M)
    assert len(scenarios) >= 4
    for block in _section(doc, "Pre-mortem").split("\n### ")[1:]:
        assert re.search(r"\*\*Control:\*\* .+", block), block[:60]
        assert re.search(r"\*\*Detection:\*\*", block) and re.search(r"\*\*Response:\*\*", block)
    limits = _section(doc, "Scan limits").lower()
    for gap in ("deflate", "base64", "url-encod", "utf-16", "double gzip", "split across lines"):
        assert gap in limits, gap


def test_releasing_doc_names_the_stop_conditions_and_the_time_buffer():
    doc = read("docs", "dev", "RELEASING.md").lower()
    assert "stop" in _section(doc, "pre-mortem") and "buffer" in doc


def test_quickstart_runs_the_whole_readme_block_on_the_weekly_and_manual_runs_only():
    suite_line = ".venv/bin/python -m pytest -q packages"
    block_lines = _block("Quick start").splitlines()
    assert block_lines.count(suite_line) == 1
    ci = read(".github", "workflows", "ci.yml")
    job = ci.split("\n  quickstart:", 1)[1].split("\n  audit:", 1)[0]
    assert "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in job
    # the block runs as the README writes it: nothing is filtered out of it
    assert 'bash -euxo pipefail "$RUNNER_TEMP/quick-start.sh"' in job
    assert "grep -v" not in job


# ---- MF-6: the history scans, the gitleaks version, the first-hour list, the name of the repository ----

def _ci_job_names():
    import pytest
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load(read(".github", "workflows", "ci.yml"))["jobs"]
    return {job_id: job.get("name", job_id) for job_id, job in jobs.items()}


def test_releasing_documents_the_history_scan_and_the_exact_gitleaks_commands():
    doc = read("docs", "dev", "RELEASING.md")
    steps = _section(doc, "Release checklist")
    assert "--scan-history" in steps
    assert "gitleaks git --no-banner --redact" in steps
    assert "--scan-history" in _section(doc, "Scan limits")


def test_releasing_pins_the_gitleaks_version_ci_pins_and_says_the_minimum():
    ci = read(".github", "workflows", "ci.yml")
    pinned = re.search(r'GITLEAKS_VERSION: "([0-9.]+)"', ci).group(1)
    doc = read("docs", "dev", "RELEASING.md")
    assert pinned in doc and "8.30" in doc
    assert "MIN_GITLEAKS" in read("tools", "export_public.py")


def test_releasing_frees_the_repository_name_before_the_public_repository_is_created():
    steps = _section(read("docs", "dev", "RELEASING.md"), "Release checklist")
    text = steps.lower()
    free = text.find("free the name")
    create = text.find("empty public repository")
    assert 0 <= free < create, (free, create)
    assert "never" in text[free:create] and "visibility" in text[free:create]


def test_the_first_hour_is_one_ordered_sequence_that_names_every_setting_and_ci_check():
    steps = _section(read("docs", "dev", "RELEASING.md"), "Release checklist").lower()
    start = steps.find("empty public repository")
    first_hour = steps[start:]
    order = ["private vulnerability reporting", "secret scanning", "push protection", "branch protection",
             "deletion", "outside collaborators", "description", "topics", "pypi", "ci_runner"]
    at = [first_hour.find(k) for k in order]
    assert all(i >= 0 for i in at), dict(zip(order, at))
    assert at == sorted(at), dict(zip(order, at))
    for job_id, name in _ci_job_names().items():
        assert job_id in first_hour and name.split(" (")[0].lower() in first_hour, job_id
    assert "tests (python 3.12)" in first_hour and "python 3.10" in first_hour  # 3.10 runs on main only: never required


def test_security_md_promises_private_reporting_and_the_checklist_turns_it_on():
    assert "private vulnerability reporting" in read("SECURITY.md").lower()
    assert "private vulnerability reporting" in _section(read("docs", "dev", "RELEASING.md"), "Release checklist").lower()
