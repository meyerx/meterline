#!/usr/bin/env python3
"""List every place a package still has to be named in, with the exact line or table row to add.

    .venv/bin/python tools/check_new_connector.py NAME

NAME is the name given to tools/new_connector.py (``acme``) or the full package name (``meterline-acme``); the package
folder must exist under packages/. The command prints a numbered list, one item per place: its id, the file to edit,
what the place is, the text to add, what is missing now, and the tools tests that check it. It exits 1 while anything is
missing, and prints ``nothing missing: meterline-NAME is wired in`` and exits 0 when the package is wired in. A folder
that does not exist gives exit 2.

Run it again after each edit until it prints ``nothing missing``, then run ``.venv/bin/python -m pytest -q tools``. The items are
defined in tools/wiring.py, which the tools tests read as well, so the list is the same one the tests enforce. One item
(docs/dev/README.md) belongs to the private repository only: where that file is not shipped it is printed as skipped and
never counts as missing.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiring  # noqa: E402
from package_list import ROOT, packages  # noqa: E402

WIDTH = "      "


def dist_name(name, root=ROOT):
    """``acme`` and ``meterline-acme`` are the same package; ``meterline`` and ``sim`` are the packages of those names."""
    existing = packages(root)
    for candidate in (name, "meterline-" + name):
        if candidate in existing:
            return candidate
    return name if name == "meterline" or name.startswith("meterline-") else "meterline-" + name


def check(root, dist):
    """(missing, skipped): [(item, [what is missing])] for the items that apply and are not wired, and the items skipped here."""
    package = wiring.Package(root, dist)
    missing, skipped = [], []
    for item in wiring.ITEMS:
        if not wiring.applies(item, package):
            continue
        if wiring.skipped(item, root):
            skipped.append(item)
            continue
        problems = item.missing(root, dist)
        if problems:
            missing.append((item, problems))
    return missing, skipped


def render(root, dist, missing, skipped):
    package = wiring.Package(root, dist)
    lines = []
    if missing:
        lines.append("%s is not wired in: %d place(s) to edit. Do them in this order, then run this command again;" % (dist, len(missing)))
        lines.append("when it prints nothing missing, run `.venv/bin/python -m pytest -q tools`.")
        lines.append("")
    for number, (item, problems) in enumerate(missing, 1):
        lines.append("%2d. [%s] %s%s" % (number, item.id, item.file.replace("<package>", dist), "  (connectors only)" if item.connector_only else ""))
        lines.append(WIDTH + item.title)
        for text in item.add(package).splitlines():
            lines.append(WIDTH + "  " + text if text.strip() else "")
        for problem in problems:
            lines.append(WIDTH + "now: " + problem)
        if item.tests:
            lines.append(WIDTH + "checked by: " + ", ".join(item.tests))
        lines.append("")
    if not missing:
        lines.append("nothing missing: %s is wired in" % dist)
    for item in skipped:
        lines.append("[%s] %s: %s" % (item.id, item.file, wiring.PRIVATE_ONLY))
    return "\n".join(lines).rstrip("\n") + "\n"


def report(root, dist, out=None):
    """Print the list for ``dist`` and return the exit code: 1 while anything is missing, else 0."""
    out = out or sys.stdout
    missing, skipped = check(root, dist)
    out.write(render(root, dist, missing, skipped))
    return 1 if missing else 0


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("name", help="the name given to new_connector.py (acme), or the package name (meterline-acme)")
    args = parser.parse_args(argv)
    dist = dist_name(args.name, root)
    if not os.path.exists(os.path.join(root, "packages", dist, "pyproject.toml")):
        print("no package folder packages/%s (with a pyproject.toml) under %s; make it first with .venv/bin/python tools/new_connector.py %s"
              % (dist, root, args.name), file=sys.stderr)
        return 2
    return report(root, dist)


if __name__ == "__main__":
    sys.exit(main())
