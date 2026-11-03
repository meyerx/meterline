"""Which test suite a pull request needs, from the paths it changes.

Reads the changed paths on stdin, one per line (``git diff --name-only --no-renames BASE...HEAD``, so a renamed file
shows under its old name too), and prints one word:

- ``none``  - every changed path is the backlog register, which no test reads: only the commit-message check runs.
- ``tools`` - every changed path is prose that only the tests under ``tools/`` read: those tests run, plus every
  package test that scans the whole repository; the other package tests do not.
- ``full``  - anything else, or nothing on stdin: every test runs.

Prose is a ``.md`` file or ``llms.txt``. Prose a package test reads still needs the full suite: anything under
``packages/``, and every file a test under ``packages/*/tests`` names. Neither that list nor the list of package
tests that scan the whole repository is written here: both are read from the package tests each time, so a new test
written in the usual shape (a top-level test function, the file named in it or in a helper of the same module) is
covered the day it lands. The weekly run and every manual run run the full suite whatever this script says, which
catches a reading this script cannot see.

CI runs this script as it is on the base branch, against the pull request's tree (``--root``), so a pull request
cannot narrow its own tests by editing it.

Run: git diff --name-only --no-renames origin/main...HEAD | python3 tools/ci_suite.py
     python3 tools/ci_suite.py --targets   (the pytest arguments of the tools suite)
     either with --root DIR to read the package tests of another checkout
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

NO_TEST = {"BACKLOG" + "-std.md"}  # assembled: the exported tree must not name this file
# a .md name in a package test: a path ("docs/records.md", "reference/gold-datasets.md") or a bare name ("records.md")
MD_NAME = re.compile(r"[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.md\b")
# a top-level function and its body, up to the next top-level statement
FUNCTION = re.compile(r"^def (\w+)\(.*?(?=^\S|\Z)", re.S | re.M)
REPO_ROOT = re.compile(r"parents\[3\]|\bROOT\b|\bREPO\b")
# walking a tree or listing every tracked file; ast.walk walks parsed code, not files
TREE_WALK = re.compile(r"(?<!ast)\.walk\(|\.rglob\(|\bglob\.glob\(|\.glob\(\s*[\"']\*\*|ls-files")


def _package_test_sources(root):
    for path in sorted(Path(root).glob("packages/*/tests/**/*.py")):
        yield path, path.read_text(encoding="utf-8", errors="replace")


def names_package_tests_read(root=ROOT):
    """Every .md name the files under packages/*/tests mention, as written there."""
    names = set()
    for _, text in _package_test_sources(root):
        names.update(MD_NAME.findall(text))
    return names


def read_by_a_package_test(path, names):
    """True when ``path`` matches a name a package test mentions: the same path, a path ending with it, or the same
    file name for a bare name. A bare README.md therefore covers every README.md: a package test reads its own
    README and another reads the one at the root, and the bare name cannot tell them apart."""
    for name in names:
        name = name.lstrip("./")
        if "/" in name:
            if path == name or path.endswith("/" + name):
                return True
        elif path.rsplit("/", 1)[-1] == name:
            return True
    return False


def is_prose(path):
    return path.endswith(".md") or path == "llms.txt"


def suite(paths, root=ROOT):
    """``paths`` changed -> "none", "tools" or "full"."""
    paths = [p.strip() for p in paths if p.strip()]
    if not paths:
        return "full"
    if all(p in NO_TEST for p in paths):
        return "none"
    names = names_package_tests_read(root)
    for p in paths:
        if p in NO_TEST:
            continue
        if not is_prose(p) or p.startswith("packages/") or read_by_a_package_test(p, names):
            return "full"
    return "tools"


def repo_scanning_package_tests(root=ROOT):
    """pytest node ids of the package tests that walk the repository from its root, directly or through a helper
    function of the same module: any file can change their result, prose included, so the tools suite runs them."""
    found = []
    for path, text in _package_test_sources(root):
        functions = {m.group(1): m.group(0) for m in FUNCTION.finditer(text)}
        walkers = {name for name, body in functions.items() if TREE_WALK.search(body)}
        for name, body in functions.items():
            if not name.startswith("test_") or not REPO_ROOT.search(body):
                continue
            if name in walkers or any(re.search(r"\b%s\(" % re.escape(w), body) for w in walkers - {name}):
                found.append("%s::%s" % (path.relative_to(root).as_posix(), name))
    return found


def tools_targets(root=ROOT):
    """The pytest arguments of the tools suite."""
    return ["tools"] + repo_scanning_package_tests(root)


def main(argv, stdin):
    root = ROOT
    if argv[:1] == ["--root"]:
        root, argv = Path(argv[1]), argv[2:]
    if argv == ["--targets"]:
        return " ".join(tools_targets(root))
    if argv:
        raise SystemExit("usage: ci_suite.py [--root DIR] [--targets]")
    return suite(stdin.read().splitlines(), root)


if __name__ == "__main__":
    print(main(sys.argv[1:], sys.stdin))
