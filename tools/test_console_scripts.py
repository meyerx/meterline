"""Every [project.scripts] target of a package points into that package's own import package, and names a function that exists.

A command that runs another package's code installs without complaint, so nothing else catches it.
Run: python3 -m pytest -q tools"""
import importlib
import pathlib
import re

import pytest

PACKAGES = pathlib.Path(__file__).resolve().parent.parent / "packages"


def _scripts(pyproject):
    """The (command, target) pairs of the [project.scripts] table; the file is small and regular, so no TOML parser is needed."""
    section = re.search(r"^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)", pyproject.read_text(encoding="utf-8"), re.M | re.S)
    if not section:
        return []
    return re.findall(r'^([\w.\-]+)\s*=\s*"([^"]+)"', section.group(1), re.M)


def _cases():
    out = []
    for pyproject in sorted(PACKAGES.glob("*/pyproject.toml")):
        src = pyproject.parent / "src"
        own = sorted(d.name for d in src.iterdir() if d.is_dir() and (d / "__init__.py").exists())
        for command, target in _scripts(pyproject):
            out.append(pytest.param(pyproject.parent.name, own, command, target, id="%s:%s" % (pyproject.parent.name, command)))
    return out


def test_every_package_declares_a_command():
    assert len(_cases()) >= 6  # every package but the common-format core ships a command


@pytest.mark.parametrize("package, own, command, target", _cases())
def test_console_script_target_is_the_packages_own_function(package, own, command, target):
    module, _, func = target.partition(":")
    assert func, "%s: target %r has no :function" % (command, target)
    assert module.split(".")[0] in own, "%s of %s runs %r, which is not in its own import package %s" % (command, package, target, own)
    assert callable(getattr(importlib.import_module(module), func, None)), "%s: %s has no function %s" % (command, module, func)
