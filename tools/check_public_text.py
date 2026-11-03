#!/usr/bin/env python3
"""Check a text that will be typed into the host's web page (a pull request body, a tag message) with the export's scan.

    python3 tools/check_public_text.py FILE --denylist /path/outside/the/repo/terms.txt

Prints PASS (exit 0) or FAIL and the kind of each hit, never the text or a term (exit 1)."""
import argparse
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_public as ep  # noqa: E402


def check(path, denylist):
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(path, os.path.join(tmp, "public-text.txt"))
        return ep.scan(tmp, ep.load_terms(denylist))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--denylist", required=True)  # required: a check with no list would pass what the list exists to stop
    args = ap.parse_args()
    hits = check(args.file, args.denylist)
    print("FAIL: " + ", ".join(sorted({kind for kind, _, _ in hits})) if hits else "PASS")
    sys.exit(1 if hits else 0)
