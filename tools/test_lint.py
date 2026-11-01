"""F-47: the committed lint configuration (ruff.toml) holds for the package
sources, and no function in them is longer than 100 logical lines.

ruff.toml selects E, F, B, UP, S, SIM, C90 (McCabe complexity at most 20) and
DTZ; each rule it leaves off says why. The length limit has no ruff rule, so it
is counted here with ast. When ruff is not installed, the ruff check skips and
says how to install it; the length check needs nothing.
"""

import ast
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "ruff.toml"
MAX_LOGICAL_LINES = 100
SELECT = '["E", "F", "B", "UP", "S", "SIM", "C90", "DTZ"]'


def _ruff():
    local = os.path.join(os.path.dirname(sys.executable), "ruff")
    return local if os.path.exists(local) else shutil.which("ruff")


def _sources():
    return sorted(glob.glob(str(ROOT / "packages" / "*" / "src")))


def test_ruff_config_selects_the_agreed_rules():
    text = CONFIG.read_text(encoding="utf-8")
    assert "select = %s" % SELECT in text
    assert "max-complexity = 20" in text


def test_package_sources_pass_the_committed_ruff_config():
    ruff = _ruff()
    if not ruff:
        pytest.skip("ruff is not installed: pip install ruff==0.17.3 (the CI lint job installs it)")
    done = subprocess.run([ruff, "check", "--config", str(CONFIG), "--output-format", "concise", *_sources()],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-1000:]


def logical_lines(node, lines):
    """Non-blank, non-comment lines of a function, its docstring left out."""
    skip = set()
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        skip = set(range(body[0].lineno, body[0].end_lineno + 1))
    count = 0
    for number in range(node.lineno, node.end_lineno + 1):
        text = lines[number - 1].strip()
        if text and not text.startswith("#") and number not in skip:
            count += 1
    return count


def long_functions(paths, limit=MAX_LOGICAL_LINES):
    found = []
    for path in paths:
        source = Path(path).read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = logical_lines(node, lines)
                if size > limit:
                    found.append("%s:%d %s has %d logical lines" % (os.path.relpath(path, ROOT), node.lineno, node.name, size))
    return found


def test_no_function_in_the_package_sources_is_longer_than_100_logical_lines():
    paths = sorted(glob.glob(str(ROOT / "packages" / "*" / "src" / "**" / "*.py"), recursive=True))
    assert paths
    assert long_functions(paths) == []


def test_long_function_check_counts_logical_lines_only(tmp_path):
    # negative control: 101 statements fail; a docstring, comments and blank lines do not count
    body = "".join("    x%d = %d\n" % (i, i) for i in range(101))
    (tmp_path / "long.py").write_text("def long():\n" + body, encoding="utf-8")
    padded = '    """doc\n\n    string"""\n' + "    # note\n\n" * 50 + "".join("    y%d = %d\n" % (i, i) for i in range(99))
    (tmp_path / "padded.py").write_text("def padded():\n" + padded, encoding="utf-8")
    found = long_functions([tmp_path / "long.py", tmp_path / "padded.py"])
    assert len(found) == 1 and found[0].endswith("long.py:1 long has 102 logical lines")


def _ci_job(name):
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    start = text.index("\n  %s:\n" % name) + 1
    following = re.search(r"^  [\w-]+:$", text[start + 1:], flags=re.M)
    return text[start:start + 1 + following.start()] if following else text[start:]


def test_ci_lints_the_package_sources_with_the_committed_config():
    lint = _ci_job("lint")
    assert re.search(r"^\s*run: ruff check packages/\*/src\s*$", lint, flags=re.M)  # picks up ruff.toml
    assert "ruff check --isolated --select E9,F63,F7,F82 ." in lint  # the rest of the tree: syntax and names


def test_ci_tests_job_can_run_the_ruff_and_history_checks():
    tests = _ci_job("tests")
    assert '"ruff==0.17.3"' in tests  # tools/test_lint.py and the F-33 check run instead of skipping
    assert re.search(r"^\s*fetch-depth: 0(\s*#.*)?$", tests, flags=re.M)  # tools/test_commit_messages.py needs the history


def test_ci_runs_the_dashboard_accessibility_check():
    # F-43: axe-core over the fixture dashboard, pinned, in its own job
    job = _ci_job("dashboard-a11y")
    assert "node tools/dashboard_axe.js" in job
    assert "axe-core@4.10.3" in job and "puppeteer@23.11.1" in job
    assert "meterline-gold build" in job
