"""Review finding F-40: every package's metadata is complete and its dependencies are declared with tested bounds.

Run: python3 -m pytest -q tools
"""
import ast
import os
import re
import sys

import pytest

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = sorted(d for d in os.listdir(os.path.join(ROOT, "packages"))
                  if os.path.exists(os.path.join(ROOT, "packages", d, "pyproject.toml")))
REPO_URL = "https://github.com/meyerx/meterline"
# import name -> distribution name, for the third-party modules the packages import
DISTRIBUTIONS = {"yaml": "PyYAML", "typing_extensions": "typing_extensions", "httpx": "httpx", "duckdb": "duckdb",
                 "pydantic": "pydantic", "pyarrow": "pyarrow", "pytest": "pytest", "hypothesis": "hypothesis",
                 "jsonschema": "jsonschema", "tomli": "tomli", "meterline": "meterline",
                 "meterline_sim": "meterline-sim", "meterline_gold": "meterline-gold"}


def project(package):
    with open(os.path.join(ROOT, "packages", package, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)["project"]


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def ci_python_versions():
    match = re.search(r'^\s*python-version: \[([^\]]+)\]', read(".github", "workflows", "ci.yml"), flags=re.M)
    return [v.strip().strip('"') for v in match.group(1).split(",")]


def requirement_name(req):
    return re.match(r"^\s*([A-Za-z0-9_.\-]+)", req).group(1)


def all_requirements(meta):
    reqs = list(meta.get("dependencies", []))
    for extra in meta.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    return reqs


@pytest.mark.parametrize("package", PACKAGES)
def test_metadata_is_complete(package):
    meta = project(package)
    assert meta["readme"] == "README.md"
    assert os.path.exists(os.path.join(ROOT, "packages", package, "README.md"))
    assert meta["authors"] == [{"name": "Xavier Meyer"}]
    assert len(meta["keywords"]) >= 3
    urls = meta["urls"]
    assert urls["Homepage"] == REPO_URL
    assert urls["Issues"] == REPO_URL + "/issues"
    assert urls["Changelog"] == REPO_URL + "/blob/main/CHANGELOG.md"
    assert urls["Source"] == REPO_URL + "/tree/main/packages/" + package


@pytest.mark.parametrize("package", PACKAGES)
def test_python_classifiers_and_minimum_match_the_ci_matrix(package):
    meta = project(package)
    ci = ci_python_versions()
    claimed = sorted(c.rsplit(" :: ", 1)[1] for c in meta["classifiers"]
                     if re.match(r"^Programming Language :: Python :: 3\.\d+$", c))
    assert claimed == sorted(ci, key=lambda v: tuple(map(int, v.split("."))))
    assert meta["requires-python"] == ">=" + min(ci, key=lambda v: tuple(map(int, v.split("."))))


@pytest.mark.parametrize("package", PACKAGES)
def test_every_dependency_has_a_lower_bound(package):
    # an exact pin (the dev extra pins the tools CI pins) is a lower bound too
    unbounded = [r for r in all_requirements(project(package)) if ">=" not in r and "==" not in r]
    assert unbounded == []


@pytest.mark.parametrize("package", [p for p in PACKAGES if p != "meterline"])
def test_dependents_require_the_meterline_that_has_the_names_they_import(package):
    reqs = [r for r in project(package)["dependencies"] if requirement_name(r) == "meterline"]
    # meterline.run.data_folder_lock first shipped in meterline 0.3.0 and the auto rule for an empty first backfill in 0.3.1;
    # a package whose source names the lock needs 0.3.1, and the other packages need only the 0.2.0 that has the v2 records.
    # Both name an upper bound below the next minor version (the v0.3 hardening, C-4)
    src = os.path.join(ROOT, "packages", package, "src")
    uses_lock = any("data_folder_lock" in open(os.path.join(base, n), encoding="utf-8").read()
                    for base, _dirs, files in os.walk(src) for n in files if n.endswith(".py"))
    assert reqs == ["meterline>=0.3.1,<0.4" if uses_lock else "meterline>=0.2.0,<0.4"]


def _imports(folder):
    names = set()
    for base, _dirs, files in os.walk(folder):
        for name in files:
            if name.endswith(".py"):
                tree = ast.parse(read(os.path.relpath(os.path.join(base, name), ROOT)))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names.update(a.name.split(".")[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        names.add(node.module.split(".")[0])
    return names


def _local_modules(folder):
    return {os.path.splitext(n)[0] for n in os.listdir(folder) if n.endswith(".py")}


@pytest.mark.parametrize("package", PACKAGES)
def test_every_third_party_import_is_declared(package):
    meta = project(package)
    own = package.replace("-", "_")
    stdlib = set(sys.stdlib_module_names) | {"tomllib"}  # tomllib is stdlib from 3.11; a package on 3.10 declares tomli for it
    runtime = {requirement_name(r) for r in meta.get("dependencies", [])}
    extras = {requirement_name(r) for r in all_requirements(meta)}
    src = os.path.join(ROOT, "packages", package, "src")
    tests = os.path.join(ROOT, "packages", package, "tests")
    used_src = _imports(src) - stdlib - {own}
    used_tests = _imports(tests) - stdlib - {own} - _local_modules(tests) - {"conftest"}
    unknown = (used_src | used_tests) - set(DISTRIBUTIONS)
    assert not unknown, "add the distribution name of %s to DISTRIBUTIONS" % sorted(unknown)
    # a src import is a runtime dependency, or an optional one (pyarrow, guarded) in an extra
    missing_src = {DISTRIBUTIONS[m] for m in used_src} - extras
    assert missing_src == set()
    assert {DISTRIBUTIONS[m] for m in used_src} - runtime <= {"pyarrow", "httpx"}  # both guarded: parquet, and the conformance kit
    missing_tests = {DISTRIBUTIONS[m] for m in used_tests} - extras - {"meterline"}
    assert missing_tests == set()


def test_gold_declares_what_it_imports_and_what_its_tests_need():
    meta = project("meterline-gold")
    assert any(requirement_name(r) == "typing_extensions" for r in meta["dependencies"])
    assert any(requirement_name(r) == "meterline-sim" for r in meta["optional-dependencies"]["dev"])


def test_ci_has_a_minimum_versions_job():
    text = read(".github", "workflows", "ci.yml")
    start = text.index("\n  minimum-versions:\n") + 1
    following = re.search(r"^  [\w-]+:$", text[start + 1:], flags=re.M)
    job = text[start:start + 1 + following.start()] if following else text[start:]
    assert '"%s"' % min(ci_python_versions(), key=lambda v: tuple(map(int, v.split(".")))) in job
    assert "--resolution lowest-direct" in job
    assert "pytest" in job


# ---- MF-11: the dev extra installs what AGENTS.md and the tests use ----

def _ci_pin(name):
    ci = open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8").read()
    pins = set(re.findall(r'"%s==([0-9.]+)"' % re.escape(name), ci))
    assert len(pins) == 1, (name, pins)
    return "%s==%s" % (name, pins.pop())


def test_dev_extra_has_xdist_and_ruff():
    wanted = [_ci_pin("pytest-xdist"), _ci_pin("ruff")]
    for pkg in PACKAGES:
        dev = [d.replace(" ", "") for d in project(pkg)["optional-dependencies"]["dev"]]
        for pin in wanted:
            assert pin in dev, "%s: the dev extra lacks %s" % (pkg, pin)


def test_the_core_dev_extra_has_httpx_for_the_http_tests():
    dev = " ".join(project("meterline")["optional-dependencies"]["dev"])
    assert re.search(r"httpx\s*[>=]", dev), dev


# ---- B2-d: a base install skips the tests of an extra it lacks, it does not error ----

def _runtime_dists(package):
    names = {package.replace("-", "_"), package}
    for req in project(package).get("dependencies", []):
        names.add(requirement_name(req).replace("-", "_"))
        names.add(requirement_name(req))
    return names


def _guarded(text, module):
    return re.search(r"importorskip\(\s*[\"']%s[\"']" % re.escape(module), text) is not None


def _unguarded_optional_imports(package):
    """(file, module) pairs: a test module that imports a third-party module the package does not depend on,
    with no pytest.importorskip for it in that module (a fixture that imports it counts as the module)."""
    tests = os.path.join(ROOT, "packages", package, "tests")
    have = _runtime_dists(package)
    problems = []
    for name in sorted(os.listdir(tests)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(tests, name), encoding="utf-8").read()
        for node in ast.walk(ast.parse(text)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else []
            for module in {m.split(".")[0] for m in modules}:
                dist = DISTRIBUTIONS.get(module)
                if dist is None or module == "pytest" or dist in have or dist.replace("-", "_") in have:
                    continue
                if not _guarded(text, module):
                    problems.append((name, module))
    return sorted(set(problems))


@pytest.mark.parametrize("package", PACKAGES)
def test_a_test_that_needs_an_extra_the_base_install_lacks_skips_instead_of_erroring(package):
    assert _unguarded_optional_imports(package) == []


# ---- B2-c: a package does not promise 1.0 stability while it is labelled Alpha ----

@pytest.mark.parametrize("package", PACKAGES)
def test_a_package_at_1_0_or_later_is_not_alpha(package):
    meta = project(package)
    major = int(meta["version"].split(".")[0])
    alpha = any(c.endswith("Development Status :: 3 - Alpha") for c in meta["classifiers"])
    assert not (major >= 1 and alpha), "%s %s is Alpha but promises 1.0 stability" % (package, meta["version"])


def test_records_md_names_no_package_at_1_0_while_none_is():
    text = open(os.path.join(ROOT, "docs", "records.md"), encoding="utf-8").read()
    at_1 = [p for p in PACKAGES if int(project(p)["version"].split(".")[0]) >= 1]
    sentence = re.search(r"- Before 1\.0\.0 \(([^)]*)\)", text).group(1)
    assert (at_1 == []) == ("no package is at 1.0.0" in text), (at_1, sentence)
