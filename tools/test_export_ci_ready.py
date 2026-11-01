"""The public tree carries every repository-root file that CI and the tests read by name, and its core suite passes there.

A file left off the export allowlist leaves the first public commit with red CI, and nothing in the private repository shows it.
Run: python3 -m pytest -q tools"""
import os
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import export_public as ep  # noqa: E402

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
    files = [f for f in _tracked(".github/workflows") if f]
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
