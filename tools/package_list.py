"""The package list, read from packages/*/pyproject.toml, so a test or a doc check never hard-codes a count or a name.

``packages()`` is every folder under packages/ that has a pyproject.toml. ``connectors()`` is the packages that call a
provider: those that depend on httpx. Run from any folder; the repository root is found from this file.
"""
import os

try:
    import tomllib
except ImportError:  # Python 3.10: pip install tomli
    import tomli as tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES_DIR = os.path.join(ROOT, "packages")


def in_working_repo(root=ROOT):
    """True in the maintainer's working repository, False in the public tree (which has no backlog file).

    A test that reads a docs/dev file that no public document links uses this: the export leaves that file out, so
    in the public tree the test skips, and in the working repository a missing file is a failure."""
    return os.path.exists(os.path.join(root, "BACKLOG" + "-std.md"))


def packages(root=ROOT):
    """Sorted names of the package folders. ``root`` is another checkout of the repository, for a tool that checks one."""
    folder = os.path.join(root, "packages")
    return sorted(d for d in os.listdir(folder) if os.path.exists(os.path.join(folder, d, "pyproject.toml")))


def metadata(package, root=ROOT):
    with open(os.path.join(root, "packages", package, "pyproject.toml"), "rb") as handle:
        return tomllib.load(handle)["project"]


def connectors(root=ROOT):
    """Sorted names of the packages that depend on httpx: the ones that read a provider's API."""
    return [p for p in packages(root) if any(d.lower().replace(" ", "").startswith("httpx") for d in metadata(p, root).get("dependencies", []))]


def readme_paths():
    return [os.path.join("packages", p, "README.md") for p in packages()]
