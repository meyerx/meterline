"""Check that an image holds this repository's version of every meterline package.

    docker run --rm --entrypoint pip IMAGE list --format=json | python tools/check_image_versions.py

Reads `pip list --format=json` on stdin and compares each package under packages/ (name and version, from its pyproject.toml)
with the installed one. Exit 0 when all match, 1 when one is missing or differs (each is printed), 2 on bad input. The CI
container job runs it: an image that took a meterline package from the package index instead of the wheel built here fails.
"""
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def normal(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def repository_versions(root=ROOT):
    found = {}
    for pyproject in sorted(Path(root).glob("packages/*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        found[normal(project["name"])] = project["version"]
    return found


def mismatches(installed, wanted):
    """[(name, wanted, installed)] for each package that is missing from ``installed`` (a pip list) or has another version."""
    have = {normal(p["name"]): p["version"] for p in installed}
    return [(name, version, have.get(name)) for name, version in sorted(wanted.items()) if have.get(name) != version]


def main(stdin):
    try:
        installed = json.load(stdin)
        bad = mismatches(installed, repository_versions())
    except (ValueError, KeyError, TypeError) as exc:
        print("bad input: %s" % exc, file=sys.stderr)
        return 2
    for name, want, got in bad:
        print("%s: the repository has %s, the image has %s" % (name, want, got or "nothing"), file=sys.stderr)
    if not bad:
        print("ok: the image holds this repository's version of %d packages" % len(repository_versions()))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin))
