"""C-4 of the v0.3 hardening spec: a meterline package that needs another one names both ends of the version it accepts.

A connector that says ``meterline>=0.3`` would accept a meterline 0.4 that breaks it. Every requirement of one package of
this repository on another (in ``dependencies``, in an extra, in ``dev``) carries a lower bound that is a version the
repository releases and an upper bound below the next minor version, such as ``meterline>=0.3.1,<0.4``.

Run: python3 -m pytest -q tools
"""
import os
import re

import pytest
try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = sorted(d for d in os.listdir(os.path.join(ROOT, "packages"))
                  if os.path.exists(os.path.join(ROOT, "packages", d, "pyproject.toml")))
REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*?)\s*(;.*)?$")


def project(package):
    with open(os.path.join(ROOT, "packages", package, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)["project"]


def requirements(package):
    meta = project(package)
    found = list(meta.get("dependencies", []))
    for group in meta.get("optional-dependencies", {}).values():
        found += group
    return found


def intra_project(package):
    """[(requirement text, name, specifier)] of the requirements of ``package`` on other packages of the repository."""
    out = []
    for text in requirements(package):
        name, _extras, spec, _marker = REQUIREMENT.match(text).groups()
        if name in PACKAGES:
            out.append((text, name, spec))
    return out


def released_versions(name):
    """The version the tree declares for ``name`` and the versions its CHANGELOG sections name: what the repository releases."""
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
        text = fh.read()
    listed = set(re.findall(r"^## \[%s (\d+\.\d+\.\d+)\]" % re.escape(name), text, flags=re.M))
    return listed | {project(name)["version"]}


def version(text):
    return tuple(int(part) for part in text.split("."))


def test_the_packages_do_depend_on_each_other():
    assert sum(len(intra_project(p)) for p in PACKAGES) >= 10  # a test that finds nothing proves nothing


@pytest.mark.parametrize("package", PACKAGES)
def test_every_requirement_on_another_meterline_package_has_both_bounds(package):
    for text, name, spec in intra_project(package):
        lower = re.search(r">=\s*(\d+\.\d+(?:\.\d+)?)", spec)
        upper = re.search(r"<\s*(\d+\.\d+)(?:\.\d+)?\s*(?:,|$)", spec)
        assert lower and upper, "%s: %r needs a lower and an upper bound, such as >=0.3.1,<0.4" % (package, text)
        assert not re.search(r"<=|~=|==|!=|>[^=]", spec), "%s: %r uses a bound other than >= and <" % (package, text)
        low, up = version(lower.group(1)), version(upper.group(1))
        current = version(project(name)["version"])
        assert up == (current[0], current[1] + 1), (
            "%s: %r: the upper bound is not the next minor version after %s, the version the tree has of %s"
            % (package, text, project(name)["version"], name))
        assert low <= current, (package, text)
        known = released_versions(name)
        assert lower.group(1) in known or ".".join(map(str, low + (0,) * (3 - len(low)))) in known, (
            "%s: %r: %s is not a version of %s that the repository releases (%s)" % (package, text, lower.group(1), name, sorted(known)))


@pytest.mark.parametrize("package", PACKAGES)
def test_the_lower_bound_is_not_above_the_version_the_tree_declares(package):
    for text, name, spec in intra_project(package):
        lower = re.search(r">=\s*(\d+\.\d+(?:\.\d+)?)", spec)
        if lower:
            assert version(lower.group(1)) <= version(project(name)["version"]), (package, text)
