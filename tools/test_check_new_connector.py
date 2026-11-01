"""E2: tools/check_new_connector.py knows every place a new package must appear, and a test proves it knows every check.

The wiring items live in tools/wiring.py, one module shared by the command and the tools tests. The tests here:

- read every tools/test_*.py (ast) and require each test that reads the package list to be named by a wiring item or
  excused in wiring.NOT_WIRING with a reason, so a new tools check cannot be added without the command knowing it;
- scaffold a package into a scratch copy of the tree and run the command: it lists every item and exits 1;
- run the command on every package of the real tree: nothing missing, exit 0;
- run it where docs/dev/README.md does not exist (the public tree): that item is skipped, not missing.

Run: python3 -m pytest -q tools/test_check_new_connector.py
"""
import ast
import glob
import importlib
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from package_list import ROOT, packages  # noqa: E402

SLOW = 600
PROBE = "wiring-probe"  # the name scaffolded into the scratch tree: a name no real connector will take, so the scaffold never collides
PROBE_DIST = "meterline-" + PROBE


def wiring():
    return importlib.import_module("wiring")


# ---- the static scan: which tests read the package list ----

LISTING = {"listdir", "glob", "iglob", "rglob", "iterdir", "scandir", "walk"}
SEEDS = {"packages", "connectors", "readme_paths", "PACKAGES_DIR"}  # the names tools/package_list.py gives a test


def _call_name(func):
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


class _Module:
    """One tools/test_*.py. A name is *tainted* when it holds the package list or a function that returns it."""

    def __init__(self, path):
        self.tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        self.tainted = set()   # module-level names (and function names) that read the package list
        self.folders = set()   # module-level names that hold the packages folder
        self.imports = [(node.module, a.name, a.asname or a.name) for node in self.tree.body
                        if isinstance(node, ast.ImportFrom) and node.module for a in node.names]

    def reads(self, node, local):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and (sub.id in self.tainted or sub.id in local):
                return True
            if isinstance(sub, ast.Call) and _call_name(sub.func) in LISTING:  # a listing of the packages folder
                for inner in ast.walk(sub):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and "packages" in inner.value:
                        return True
                    if isinstance(inner, ast.Name) and inner.id in self.folders:
                        return True
        return False

    def _local(self, function):
        local, again = set(), True
        while again:
            again = False
            for sub in ast.walk(function):
                if isinstance(sub, ast.Assign):
                    pairs = [(sub.targets, sub.value)]
                elif isinstance(sub, (ast.For, ast.comprehension)):
                    pairs = [([sub.target], sub.iter)]
                else:
                    continue
                for targets, value in pairs:
                    names = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
                    if not names <= local and self.reads(value, local):
                        local |= names
                        again = True
        return local

    def function_reads(self, function):
        local = self._local(function)
        return self.reads(ast.Module(body=function.body, type_ignores=[]), local) or any(self.reads(d, local) for d in function.decorator_list)

    def grow(self, modules):
        """One more round of tainting; True when something was added."""
        before = (len(self.tainted), len(self.folders))
        for module, name, alias in self.imports:
            if (module == "package_list" and name in SEEDS) or (module in modules and name in modules[module].tainted):
                self.tainted.add(alias)
        for node in self.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {t.id for t in targets if isinstance(t, ast.Name)}
                if any(isinstance(c, ast.Constant) and c.value == "packages" for c in ast.walk(node.value)):
                    self.folders |= names
                if self.reads(node.value, set()):
                    self.tainted |= names
            elif isinstance(node, ast.FunctionDef) and not node.name.startswith("test") and self.function_reads(node):
                self.tainted.add(node.name)
        return before != (len(self.tainted), len(self.folders))

    def test_functions(self):
        return [n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]


def _modules(tools_dir):
    modules = {os.path.basename(p)[:-3]: _Module(p) for p in sorted(glob.glob(os.path.join(tools_dir, "test_*.py")))}
    while any([m.grow(modules) for m in modules.values()]):
        pass
    return modules


def all_test_functions(tools_dir):
    """Every test function of tools/test_*.py as 'tools/test_x.py::test_name'."""
    return {"tools/%s.py::%s" % (name, f.name) for name, m in _modules(tools_dir).items() for f in m.test_functions()}


def package_list_readers(tools_dir):
    """'tools/test_x.py::test_name' of each test that calls or iterates the package list, directly or through a helper or a constant.

    This file is left out: its tests run the command on the packages, they do not check a place a package must appear in."""
    return {"tools/%s.py::%s" % (name, f.name) for name, m in _modules(tools_dir).items() for f in m.test_functions()
            if m.function_reads(f) and name != "test_check_new_connector"}


def unregistered(tools_dir, module):
    """The tests that read the package list and are neither named by a wiring item nor in NOT_WIRING."""
    known = {t for item in module.ITEMS for t in item.tests} | set(module.NOT_WIRING)
    return sorted(package_list_readers(tools_dir) - known)


def test_every_test_that_reads_the_package_list_is_named_by_a_wiring_item_or_excused():
    left = unregistered(HERE, wiring())
    assert not left, ("these tests read the package list but no wiring item names them and wiring.NOT_WIRING does not excuse them; "
                      "add them to the item whose file they check (tools/wiring.py), or give a one-line reason in NOT_WIRING:\n  " + "\n  ".join(left))


def test_the_scan_reports_a_planted_test_that_reads_the_package_list(tmp_path):
    # negative control: planted modules in a scratch tools folder, one per way a test can read the list
    (tmp_path / "test_planted_call.py").write_text("from package_list import packages\n\ndef test_a():\n    for p in packages():\n        assert p\n", encoding="utf-8")
    (tmp_path / "test_planted_listdir.py").write_text(
        "import os\nROOT = '.'\nNAMES = sorted(os.listdir(os.path.join(ROOT, 'packages')))\n\n\ndef helper():\n    return NAMES\n\n\ndef test_b():\n    assert helper()\n",
        encoding="utf-8")
    (tmp_path / "test_planted_parametrized.py").write_text(
        "import glob\nimport pytest\nFOLDER = 'packages'\n\n\n" "@pytest.mark.parametrize('p', glob.glob(FOLDER + '/*'))\ndef test_c(p):\n    assert p\n", encoding="utf-8")
    (tmp_path / "test_planted_innocent.py").write_text(
        "import os\n\ndef test_d():\n    assert os.path.exists(os.path.join('packages', 'meterline', 'README.md'))\n\n\n"
        "def test_e():\n    assert os.listdir('docs')\n", encoding="utf-8")
    found = package_list_readers(str(tmp_path))
    assert found == {"tools/test_planted_call.py::test_a", "tools/test_planted_listdir.py::test_b", "tools/test_planted_parametrized.py::test_c"}, found

    class Nothing:  # a wiring module that knows none of them
        ITEMS = ()
        NOT_WIRING = {}
    assert unregistered(str(tmp_path), Nothing) == sorted(found)

    class Knows:  # a wiring module that names one and excuses one
        class _Item:
            tests = ("tools/test_planted_call.py::test_a",)
        ITEMS = (_Item,)
        NOT_WIRING = {"tools/test_planted_listdir.py::test_b": "planted"}
    assert unregistered(str(tmp_path), Knows) == ["tools/test_planted_parametrized.py::test_c"]


def test_every_name_in_wiring_is_a_test_that_exists_and_an_excuse_is_for_a_test_that_reads_the_list():
    module = wiring()
    existing, readers = all_test_functions(HERE), package_list_readers(HERE)
    named = [t for item in module.ITEMS for t in item.tests]
    assert [t for t in named if t not in existing] == [], "a wiring item names a test that does not exist"
    assert [t for t in module.NOT_WIRING if t not in existing] == [], "NOT_WIRING excuses a test that does not exist"
    assert [t for t in module.NOT_WIRING if t not in readers] == [], "NOT_WIRING excuses a test that does not read the package list"
    assert not set(module.NOT_WIRING) & set(named), "a test is both a wiring check and excused"
    assert all(reason.strip() for reason in module.NOT_WIRING.values())


def test_the_items_have_distinct_ids_a_file_and_the_words_to_print():
    module = wiring()
    ids = [item.id for item in module.ITEMS]
    assert len(ids) == len(set(ids)) and ids
    for item in module.ITEMS:
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", item.id) and item.file and item.title, item.id


# ---- the command ----

def _env(root, leave_out=None):
    """The environment of a run in ``root``: the sources of its packages first, so imports resolve to that tree."""
    sources = [os.path.join(root, "packages", p, "src") for p in sorted(os.listdir(os.path.join(root, "packages")))
               if p != leave_out and os.path.isdir(os.path.join(root, "packages", p, "src"))]
    return dict(os.environ, PYTHONPATH=os.pathsep.join(sources + [os.environ.get("PYTHONPATH", "")]))


def command(root, *args, leave_out=None):
    return subprocess.run([sys.executable, os.path.join(root, "tools", "check_new_connector.py"), *args], cwd=root,
                          env=_env(root, leave_out), capture_output=True, text=True, timeout=SLOW)


def listed_ids(output):
    return re.findall(r"^\s*\d+\. \[([a-z-]+)\]", output, re.M)


def test_a_missing_package_folder_exits_2_with_a_message():
    done = command(ROOT, "no-such-thing")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "packages/meterline-no-such-thing" in done.stderr


@pytest.mark.parametrize("package", packages())
def test_every_package_of_the_tree_is_wired_in(package):
    done = command(ROOT, package)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "nothing missing: %s is wired in" % package in done.stdout, done.stdout


def test_the_short_name_and_the_full_name_are_the_same_package():
    short, full = command(ROOT, "litellm"), command(ROOT, "meterline-litellm")
    assert short.returncode == full.returncode == 0
    assert short.stdout == full.stdout and "nothing missing: meterline-litellm is wired in" in short.stdout


# ---- a scratch copy of the tree ----

def _git(*args, cwd=ROOT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check).stdout


@pytest.fixture(scope="module")
def scratch(tmp_path_factory):
    """A detached worktree of HEAD with this working tree laid over it (uncommitted changes included), removed afterwards."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    try:
        if _git("rev-parse", "--is-inside-work-tree").strip() != "true":
            pytest.skip("not a git checkout")
        if _git("rev-parse", "--is-shallow-repository").strip() == "true":
            pytest.skip("the clone is shallow: no worktree of HEAD")
    except subprocess.CalledProcessError:
        pytest.skip("not a git checkout")
    path = str(tmp_path_factory.mktemp("scratch") / "tree")
    _git("worktree", "add", "--detach", path, "HEAD")
    try:
        wanted = [p for p in _git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0") if p and os.path.exists(os.path.join(ROOT, p))]
        for rel in wanted:
            target = os.path.join(path, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, rel), target)
        for rel in _git("ls-files", "-z", cwd=path).split("\0"):
            if rel and rel not in wanted and os.path.exists(os.path.join(path, rel)):
                os.remove(os.path.join(path, rel))
        _git("add", "-A", cwd=path)  # the copy is what the tree would be if committed; the new package is added after this
        yield path
    finally:
        _git("worktree", "remove", "--force", path, check=False)
        _git("worktree", "prune", check=False)


@pytest.fixture(scope="module")
def unwired(scratch):
    """The scratch tree with a probe connector scaffolded into packages/ and nothing else done, and what the scaffold command printed."""
    made = subprocess.run([sys.executable, "tools/new_connector.py", PROBE, "--out", "packages"], cwd=scratch, env=_env(scratch),
                          capture_output=True, text=True, timeout=SLOW)
    assert made.returncode == 0, made.stdout + made.stderr
    assert os.path.isdir(os.path.join(scratch, "packages", PROBE_DIST))
    return scratch, made


def _applicable_to_a_new_connector(module, tree_has_dev_readme):
    """The ids a fresh scaffold of a connector must get listed: the items for a connector that its template does not already satisfy."""
    out = set()
    for item in module.ITEMS:
        if item.private_only and not tree_has_dev_readme:
            continue
        if not item.listed_when_unwired:
            continue
        out.add(item.id)
    return out


def test_an_unwired_package_gets_every_item_listed_and_exit_1(unwired):
    scratch, made = unwired
    module = wiring()
    done = command(scratch, PROBE, leave_out=PROBE_DIST)
    assert done.returncode == 1, done.stdout + done.stderr
    expected = _applicable_to_a_new_connector(module, os.path.exists(os.path.join(scratch, "docs", "dev", "README.md")))
    assert "is wired in" not in done.stdout
    assert set(listed_ids(done.stdout)) == expected, (sorted(set(listed_ids(done.stdout)) ^ expected), done.stdout)
    assert listed_ids(done.stdout) == [i for i in (item.id for item in module.ITEMS) if i in expected], "the items come in the order of wiring.ITEMS"
    # the list has the exact line to add, built from the name
    assert PROBE_DIST in done.stdout and "WIRING_PROBE_API_KEY" in done.stdout
    assert "| [`packages/%s`](packages/%s/README.md) |" % (PROBE_DIST, PROBE_DIST) in done.stdout
    assert '-e "packages/%s[dev,parquet]"' % PROBE_DIST in done.stdout
    assert module.NOT_RELEASED % PROBE_DIST in done.stdout
    # new_connector.py printed the same list at the end of the scaffold (its own exit stayed 0)
    assert set(listed_ids(made.stdout)) == expected, made.stdout


def test_an_unwired_package_named_with_the_full_name_gives_the_same_list(unwired):
    scratch, _ = unwired
    short, full = command(scratch, PROBE, leave_out=PROBE_DIST), command(scratch, PROBE_DIST, leave_out=PROBE_DIST)
    assert short.returncode == full.returncode == 1 and short.stdout == full.stdout


def test_the_item_that_needs_docs_dev_is_skipped_where_that_folder_is_not_shipped(unwired):
    scratch, _ = unwired
    index = os.path.join(scratch, "docs", "dev", "README.md")
    if not os.path.exists(index):
        pytest.skip("this tree has no docs/dev/README.md")
    saved = open(index, "rb").read()
    try:
        os.remove(index)
        done = command(scratch, PROBE, leave_out=PROBE_DIST)
        assert done.returncode == 1  # the other items are still missing
        assert "docs-dev-readme" not in listed_ids(done.stdout)
        assert "private repository only - skipped here" in done.stdout
        # and a wired package does not turn non-zero for the file that is not there
        wired = command(scratch, "litellm")
        assert wired.returncode == 0 and "nothing missing" in wired.stdout, wired.stdout
        assert "private repository only - skipped here" in wired.stdout
    finally:
        with open(index, "wb") as fh:
            fh.write(saved)
    assert "docs-dev-readme" in listed_ids(command(scratch, PROBE, leave_out=PROBE_DIST).stdout)


def test_a_count_in_words_is_listed_with_its_file_and_line(unwired):
    scratch, _ = unwired
    readme = os.path.join(scratch, "README.md")
    saved = open(readme, "rb").read()
    try:
        with open(readme, "ab") as fh:
            fh.write(b"\nThe two connectors read an API.\n")
        done = command(scratch, PROBE, leave_out=PROBE_DIST)
        assert "counts" in listed_ids(done.stdout)
        assert re.search(r"README\.md:\d+: 'two connectors'", done.stdout), done.stdout
    finally:
        with open(readme, "wb") as fh:
            fh.write(saved)
    assert "counts" not in listed_ids(command(scratch, PROBE, leave_out=PROBE_DIST).stdout)


def test_a_known_limits_section_without_pending_is_listed(unwired):
    scratch, _ = unwired
    readme = os.path.join(scratch, "packages", PROBE_DIST, "README.md")
    saved = open(readme, "rb").read()
    try:
        with open(readme, "wb") as fh:
            fh.write(saved.replace(b"`pending`", b"`wait`"))
        assert "known-limits-pending" in listed_ids(command(scratch, PROBE, leave_out=PROBE_DIST).stdout)
    finally:
        with open(readme, "wb") as fh:
            fh.write(saved)
    assert "known-limits-pending" not in listed_ids(command(scratch, PROBE, leave_out=PROBE_DIST).stdout)


# ---- the release decision, on a small tree ----

def _small_tree(tmp_path, releasing_extra="", in_case=True, in_order=True, in_reservation=True):
    for name in ("meterline", "meterline-acme"):
        folder = tmp_path / "packages" / name
        folder.mkdir(parents=True)
        (folder / "pyproject.toml").write_text('[project]\nname = "%s"\nversion = "0.1.0"\n' % name, encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    case = "meterline|meterline-acme" if in_case else "meterline"
    (tmp_path / ".github" / "workflows" / "release.yml").write_text('          case "$package" in\n            %s) ;;\n' % case, encoding="utf-8")
    (tmp_path / "docs" / "dev").mkdir(parents=True)
    names = "`meterline`, `meterline-acme`" if in_reservation else "`meterline`"
    order = "`meterline`, then `meterline-acme`" if in_order else "`meterline`"
    (tmp_path / "docs" / "dev" / "RELEASING.md").write_text(
        "# Releasing\n\n## Publishing to PyPI\n\nIntro.\n%s\n\n### Once\n\n1. **Reserve the package names.** %s.\n2. **Create the environment.**\n\n"
        "### Each release\n\n2. Release %s.\n3. Tag the merged commit.\n" % (releasing_extra, names, order), encoding="utf-8")
    return str(tmp_path)


def test_a_package_is_released_or_marked_not_released_yet_and_nothing_between(tmp_path):
    module = wiring()
    line = module.NOT_RELEASED % "meterline-acme"
    root = _small_tree(tmp_path / "a")
    assert module.release_problems(root, "meterline-acme") == [] and module.released_packages(root) == ["meterline", "meterline-acme"]
    # released, but left out of the case list, the reservation step and the order: each is named
    root = _small_tree(tmp_path / "b", in_case=False, in_order=False, in_reservation=False)
    problems = " ".join(module.release_problems(root, "meterline-acme"))
    assert "release.yml" in problems and "reservation" in problems and "release order" in problems
    # marked not released and absent from the case list and the order: fine, and it is not a released package
    root = _small_tree(tmp_path / "c", releasing_extra=line, in_case=False, in_order=False)
    assert module.release_problems(root, "meterline-acme") == []
    assert module.released_packages(root) == ["meterline"] and module.not_released_packages(root) == ["meterline-acme"]
    # marked not released but still in the case list: the two statements disagree
    root = _small_tree(tmp_path / "d", releasing_extra=line, in_case=True, in_order=False)
    assert "release.yml" in " ".join(module.release_problems(root, "meterline-acme"))
    # marked not released but still in the release order
    root = _small_tree(tmp_path / "e", releasing_extra=line, in_case=False, in_order=True)
    assert "release order" in " ".join(module.release_problems(root, "meterline-acme"))


def test_dependabot_is_read_from_the_pip_directories_list_whatever_surrounds_it(tmp_path):
    # the layout with `groups:` blocks (grouped version updates) next to the list: a package is one more item of the list
    folder = tmp_path / ".github"
    folder.mkdir()
    (folder / "dependabot.yml").write_text(
        'version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: "/"\n    groups:\n      github-actions:\n        patterns:\n          - "*"\n'
        '  - package-ecosystem: pip\n    directories:\n      - "/packages/meterline"\n      - "/packages/meterline-a"\n    schedule:\n      interval: weekly\n'
        '    groups:\n      python-dependencies:\n        applies-to: version-updates\n        patterns:\n          - "*"\n', encoding="utf-8")
    module = wiring()
    assert module.dependabot_directories(str(tmp_path)) == ["packages/meterline", "packages/meterline-a"]
    assert module.dependabot(str(tmp_path), "meterline-a") == [] and module.dependabot(str(tmp_path), "meterline-b")
    assert module.dependabot(str(tmp_path), "meterline-") != []  # a prefix of a listed name is not listed


def test_the_words_of_the_not_released_line_are_one_constant():
    assert wiring().NOT_RELEASED % "meterline-acme" == "`meterline-acme` is not released yet."
