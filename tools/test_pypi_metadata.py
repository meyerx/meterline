"""C-5 of the v0.3 hardening spec: what PyPI shows for each package.

Every package links its documentation, and its Python classifiers are the versions CI runs, no more: a classifier without a CI
job claims support nobody tested.

Run: python3 -m pytest -q tools
"""
import os
import re

import pytest
try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = sorted(d for d in os.listdir(os.path.join(ROOT, "packages"))
                  if os.path.exists(os.path.join(ROOT, "packages", d, "pyproject.toml")))
DOCUMENTATION = "https://github.com/meyerx/meterline/blob/main/docs/README.md"


def project(package):
    with open(os.path.join(ROOT, "packages", package, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)["project"]


def ci_python_versions():
    """The Python versions the test job of ci.yml runs: its matrix, read from the workflow file."""
    with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
        jobs = yaml.safe_load(fh)["jobs"]
    return {str(v) for v in jobs["tests"]["strategy"]["matrix"]["python-version"]}


@pytest.mark.parametrize("package", PACKAGES)
def test_every_package_links_the_documentation(package):
    assert project(package)["urls"].get("Documentation") == DOCUMENTATION


@pytest.mark.parametrize("package", PACKAGES)
def test_the_python_classifiers_are_exactly_the_versions_ci_runs(package):
    listed = {m.group(1) for c in project(package)["classifiers"] for m in [re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", c)] if m}
    assert listed == ci_python_versions()
    assert "Programming Language :: Python :: 3 :: Only" in project(package)["classifiers"]


def test_the_documentation_page_exists():
    assert os.path.isfile(os.path.join(ROOT, "docs", "README.md"))
