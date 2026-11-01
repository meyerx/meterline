#!/usr/bin/env python3
"""Make the skeleton of a new connector package from the template in tools/connector_template/.

    .venv/bin/python tools/new_connector.py NAME [--out packages] [--two-endpoints]

NAME is lowercase letters, digits and hyphens (``acme`` or ``acme-admin``). The command writes
``<out>/meterline-NAME/``: pyproject.toml, README.md with every required section, a LICENSE, and under ``src/`` the
modules ``cli.py``, ``client.py``, ``fetch.py``, ``normalise.py`` and ``testing.py``, plus ``tests/test_conformance_NAME.py``,
which runs the conformance kit (``meterline.conformance``). The stub talks to an invented endpoint, ``GET /v1/usage?date=``,
that pages with a cursor (``next_page``, sent back as ``page``), and passes the whole kit against its own fake API. It
already follows the contract's raw file format, asks for one day per query, calls a day final 4 days after it ended and
re-reads the last 5 closed days: replace the endpoint, the fake and the mapping, change those three numbers only from the
provider's documentation, and keep the kit green.
It is driven by a table, ``ENDPOINTS`` in ``fetch.py`` (endpoint name to path and record kind). With ``--two-endpoints`` the
package has a second endpoint, ``GET /v1/totals``, that feeds ``OrgTotal`` records next to the ``usage`` ones.

A template file marks the lines that belong to one variant with ``# two-endpoints: begin`` and ``# two-endpoints: end``
(or ``# one-endpoint: ...``; in Markdown ``<!-- two-endpoints: begin -->``). The lines between are kept in that variant and
dropped in the other, and the marker lines themselves are always removed.

The template is a command and not a folder of stubs because a command can be tested: tools/test_new_connector.py makes a
package, runs the kit against it and checks its files. It refuses to write over an existing folder.

When the package is written into this repository's ``packages/``, the command ends by printing what the package still lacks, the list
of tools/check_new_connector.py: that command lists every place to name the package in, with the exact line to add.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "connector_template"
LICENSE = HERE.parent / "packages" / "meterline" / "LICENSE"
CI_WORKFLOW = HERE.parent / ".github" / "workflows" / "ci.yml"
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
MARKER = re.compile(r"^\s*(?:#|<!--)\s*(one-endpoint|two-endpoints): (begin|end)\s*(?:-->)?\s*$")

# template file -> path inside the new package ({pkg} is the module name, {name} the name with underscores)
FILES = {
    "pyproject.toml.tmpl": "pyproject.toml",
    "README.md.tmpl": "README.md",
    "src/__init__.py.tmpl": "src/{pkg}/__init__.py",
    "src/cli.py.tmpl": "src/{pkg}/cli.py",
    "src/client.py.tmpl": "src/{pkg}/client.py",
    "src/fetch.py.tmpl": "src/{pkg}/fetch.py",
    "src/normalise.py.tmpl": "src/{pkg}/normalise.py",
    "src/testing.py.tmpl": "src/{pkg}/testing.py",
    "tests/test_conformance.py.tmpl": "tests/test_conformance_{name}.py",
}


def ci_pin(name):
    """``name==X.Y.Z`` as the CI workflow installs it, so a generated package never pins a stale version."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    pins = set(re.findall(r'"%s==([0-9.]+)"' % re.escape(name), text))
    if len(pins) != 1:
        raise SystemExit("expected one pin of %s in %s, found %s" % (name, CI_WORKFLOW, sorted(pins) or "none"))
    return "%s==%s" % (name, pins.pop())


def variant(text, two_endpoints):
    """``text`` with the blocks of the other variant dropped and every marker line removed."""
    keep = "two-endpoints" if two_endpoints else "one-endpoint"
    out, inside = [], None
    for line in text.splitlines(keepends=True):
        found = MARKER.match(line.rstrip("\n"))
        if found is None:
            if inside in (None, keep):
                out.append(line)
        elif found.group(2) == "begin":
            if inside is not None:
                raise ValueError("a marker block starts inside another: %r" % line)
            inside = found.group(1)
        else:
            if inside != found.group(1):
                raise ValueError("a marker block ends without its start: %r" % line)
            inside = None
    if inside is not None:
        raise ValueError("a marker block is never closed: %s" % inside)
    return "".join(out)


def names(name):
    """The words the template substitutes for a connector called ``name``."""
    underscored = name.replace("-", "_")
    return {
        "@@NAME@@": name,
        "@@DIST@@": "meterline-" + name,
        "@@PKG@@": "meterline_" + underscored,
        "@@ENV@@": underscored.upper() + "_API_KEY",
        "@@TITLE@@": name.replace("-", " ").title(),
        "@@XDIST_PIN@@": ci_pin("pytest-xdist"),
        "@@RUFF_PIN@@": ci_pin("ruff"),
    }


def scaffold(name, out, two_endpoints=False):
    """Write ``<out>/meterline-<name>/`` and return its path; ``two_endpoints`` adds the second endpoint and record kind."""
    if not NAME.match(name):
        raise SystemExit("NAME must be lowercase letters, digits and single hyphens, e.g. acme or acme-admin: %r" % name)
    words = names(name)
    target = Path(out) / words["@@DIST@@"]
    if target.exists():
        raise SystemExit("%s already exists; not writing over it" % target)
    pkg, underscored = words["@@PKG@@"], name.replace("-", "_")
    for source, destination in FILES.items():
        text = variant((TEMPLATE / source).read_text(encoding="utf-8"), two_endpoints)
        for token, value in words.items():
            text = text.replace(token, value)
        path = target / destination.format(pkg=pkg, name=underscored)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    shutil.copyfile(LICENSE, target / "LICENSE")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("name", help="lowercase name of the provider, e.g. acme")
    parser.add_argument("--out", default="packages", help="folder to write meterline-NAME into (default: packages)")
    parser.add_argument("--two-endpoints", action="store_true", help="add a second endpoint, /v1/totals, that feeds OrgTotal records")
    args = parser.parse_args(argv)
    target = scaffold(args.name, args.out, two_endpoints=args.two_endpoints)
    print("wrote %s" % target)
    print("next: .venv/bin/python -m pip install -e %s[dev], then run its tests; then wire the package in with .venv/bin/python tools/check_new_connector.py %s (the list below)" % (target, target.name))
    if Path(args.out).resolve() == HERE.parent / "packages":  # the wiring of a package made elsewhere (a test) is not this repository's
        print()
        try:
            import check_new_connector
            check_new_connector.report(check_new_connector.ROOT, target.name)  # the list of `check_new_connector.py NAME`; this exit stays 0
        except ImportError:  # tomli is missing on Python 3.10: the package is written, only the list is not
            print('the wiring list needs the core package\'s dev extra (tomli on Python 3.10): install it with .venv/bin/python -m pip '
                  'install -e "packages/meterline[conformance,dev]", then run .venv/bin/python tools/check_new_connector.py %s' % target.name,
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
