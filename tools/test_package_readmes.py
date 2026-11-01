"""B8: a package README reads the same on PyPI as in the repository.

PyPI shows the README without the repository around it, so a relative link is dead there. Every link is absolute,
built from the package's project.urls Homepage; the first screen says what the package is, how to install it and
one thing to run. python -m build then twine check proves the long description renders.

Run: python3 -m pytest -q tools/test_package_readmes.py
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_docs_structure import INLINE_LINK, REF_LINK, FENCE, INLINE_CODE, relative_targets  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = sorted(d for d in os.listdir(os.path.join(ROOT, "packages"))
                  if os.path.exists(os.path.join(ROOT, "packages", d, "pyproject.toml")))
FIRST_SCREEN = 40  # lines


def readme(pkg):
    with open(os.path.join(ROOT, "packages", pkg, "README.md"), encoding="utf-8") as fh:
        return fh.read()


def homepage(pkg):
    with open(os.path.join(ROOT, "packages", pkg, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)["project"]["urls"]["Homepage"].rstrip("/")


@pytest.mark.parametrize("pkg", PACKAGES)
def test_no_relative_link_in_a_package_readme(pkg):
    assert list(relative_targets(readme(pkg))) == []


def test_the_relative_link_check_flags_one():
    # negative control: the helper the test uses sees a relative link, and not an absolute one or a code span
    text = "[a](../../README.md) [b](https://github.com/x/y/blob/main/README.md) `[c](d.md)`\n"
    assert list(relative_targets(text)) == ["../../README.md"]


@pytest.mark.parametrize("pkg", PACKAGES)
def test_links_into_the_repository_are_built_from_the_homepage(pkg):
    text = INLINE_CODE.sub("", FENCE.sub("", readme(pkg)))
    for target in INLINE_LINK.findall(text) + REF_LINK.findall(text):
        if target.startswith(homepage(pkg)):
            assert target.startswith(homepage(pkg) + "/blob/main/") or target == homepage(pkg), target


@pytest.mark.parametrize("pkg", PACKAGES)
def test_first_screen_has_purpose_an_install_line_and_something_to_run(pkg):
    head = "\n".join(readme(pkg).split("\n")[:FIRST_SCREEN])
    assert re.search(r"^> .{40,}", head, re.M), "purpose"
    assert re.search(r"^(?:python3? -m )?pip install ", head, re.M), "install line"
    blocks = re.findall(r"^```(?:sh|python|bash)?\n(.*?)^```", head, re.M | re.S)
    runnable = [b for b in blocks if re.search(r"^(?:meterline[\w-]* |python3? )", b, re.M)]
    assert runnable, "a runnable example"


def _have(*modules):
    try:
        for m in modules:
            __import__(m)
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _have("build", "twine") and not os.environ.get("CI"),
                    reason="build and twine are not installed (pip install build twine); CI installs them")
@pytest.mark.parametrize("pkg", PACKAGES)
def test_build_then_twine_check_passes(pkg, tmp_path):
    out = tmp_path / "dist"
    build = subprocess.run([sys.executable, "-m", "build", "--outdir", str(out), os.path.join(ROOT, "packages", pkg)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stdout[-1500:] + build.stderr[-1500:]
    files = sorted(str(out / f) for f in os.listdir(out))
    assert len(files) == 2  # one sdist, one wheel
    check = subprocess.run([sys.executable, "-m", "twine", "check", "--strict", *files], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.parametrize("pkg", PACKAGES)
def test_every_link_into_the_repository_names_an_existing_path(pkg):
    base = homepage(pkg) + "/blob/main/"
    text = INLINE_CODE.sub("", FENCE.sub("", readme(pkg)))
    links = [t for t in INLINE_LINK.findall(text) + REF_LINK.findall(text) if t.startswith(base)]
    assert links
    missing = [t for t in links if not os.path.exists(os.path.join(ROOT, t[len(base):].split("#")[0]))]
    assert missing == []


# ---- B2-a: the PyPI page shows an install line that works from PyPI ----

def _install_lines(pkg):
    return [l for l in readme(pkg).split("\n") if l.startswith("pip install ")]


@pytest.mark.parametrize("pkg", PACKAGES)
def test_the_first_install_line_installs_the_package_from_the_index(pkg):
    first = _install_lines(pkg)[0]
    assert re.fullmatch(r'pip install "?%s(\[[a-z,]+\])?"?( +#.*)?' % re.escape(pkg), first), first


@pytest.mark.parametrize("pkg", PACKAGES)
def test_the_clone_form_comes_after_the_index_form_under_from_a_clone(pkg):
    text = readme(pkg)
    lines = _install_lines(pkg)
    clone = [i for i, l in enumerate(lines) if l.startswith("pip install -e")]
    assert clone and all(not l.startswith("pip install -e") for l in lines[:min(clone)])
    assert "**From a clone**" in text
    assert text.index("**From a clone**") < text.index(lines[min(clone)])
    assert text.index(lines[0]) < text.index("**From a clone**")
