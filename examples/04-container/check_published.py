"""Check that a job run left something to serve: python /app/check_published.py GOLD_ROOT

Exit 0 when GOLD_ROOT/published holds at least one audience folder with a non-empty gold file and no `delivery/` folder; exit 1
otherwise, with the reason on stderr. The audience folders are the ones the gold README lists: central, exec, manager/<key>,
person/<key>. It reads sizes and names, never the content of a file.
"""
import sys
from pathlib import Path

AUDIENCES = ("central", "exec", "manager", "person")


def audience_folders(published):
    for name in AUDIENCES:
        folder = Path(published) / name
        if name in ("manager", "person"):
            yield from (p for p in sorted(folder.glob("*")) if p.is_dir())
        elif folder.is_dir():
            yield folder


def problems(gold_root):
    published = Path(gold_root) / "published"
    if not published.is_dir():
        return ["%s is absent" % published]
    if (published / "delivery").exists():
        return ["published/delivery exists: names and emails must never be served"]
    filled = [f for f in audience_folders(published) if any(p.suffix in (".csv", ".parquet") and p.stat().st_size > 0 for p in f.iterdir())]
    return [] if filled else ["no audience folder of %s holds a non-empty gold file" % published]


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip().split("\n\n")[0], file=sys.stderr)
        return 2
    found = problems(argv[0])
    for line in found:
        print(line, file=sys.stderr)
    if not found:
        print("ok: published holds %d audience folders with gold files" % len(list(audience_folders(Path(argv[0]) / "published"))))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
