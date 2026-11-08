"""Check that a key does not appear in the text files given: python tools/check_image_key.py KEY_ENV_NAME FILE [FILE ...]

The CI container job generates a throwaway key inside the job, runs the container with it and then calls this with the output of
`docker history --no-trunc` and the log of the container. The key is read from the environment variable KEY_ENV_NAME, never from
the command line, and is never printed. Exit 0 when no file holds it, 1 when one does (the file names are printed), 2 on bad use
or an empty key, which would match everything.
"""
import os
import sys
from pathlib import Path

MIN_LENGTH = 16  # a shorter value is not a key, and could match by chance


def files_holding(key, paths):
    """The paths whose text contains ``key``."""
    return [p for p in paths if key in Path(p).read_text(encoding="utf-8", errors="replace")]


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().split("\n\n")[0], file=sys.stderr)
        return 2
    key = os.environ.get(argv[0], "")
    if len(key) < MIN_LENGTH:
        print("%s is not set to a key of at least %d characters" % (argv[0], MIN_LENGTH), file=sys.stderr)
        return 2
    found = files_holding(key, argv[1:])
    for path in found:
        print("the key is in %s" % path, file=sys.stderr)
    if not found:
        print("ok: the key is in none of %d files" % len(argv[1:]))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
