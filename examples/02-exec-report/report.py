"""Write one static HTML page from the exec/ folder of a gold build, and nothing else.

    python examples/02-exec-report/report.py GOLD_DIR [--out report.html]

The pattern to copy for any audience view: every file goes through ``meterline_gold.servable(path, "exec")`` before it
is opened, so the page cannot be built from central/, delivery/ or manifest.json. An empty cell on a row whose ``hidden``
flag is ``true`` is shown as "withheld", never as 0. An empty cell on a row whose flag is ``false`` means there is no value
and is shown as "none". A folder without the flag (schema 1.0) cannot tell the two apart and shows "withheld".
Standard library only for the data.
"""
import argparse
import csv
import html
import os
import sys
from pathlib import Path

from meterline_gold import servable

AUDIENCE = "exec"
FILES = ("costs_month", "seats_month")


def read_served(gold, rel):
    """Rows of a CSV file of the gold folder, refusing any file that is not servable to the exec audience."""
    if not servable(rel, AUDIENCE):
        raise PermissionError("%s is not servable to %s" % (rel, AUDIENCE))
    with open(Path(gold) / rel, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def cell(value, hidden=None):
    """An empty cell is "none" on a row marked ``hidden = false``, and "withheld" on any other row."""
    return html.escape(value) if value != "" else ("none" if hidden == "false" else "withheld")


def table(title, rows, columns):
    head = "".join("<th>%s</th>" % html.escape(c) for c in columns)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell(r[c], r.get("hidden")) for c in columns) for r in rows)
    return "<h2>%s</h2><table><tr>%s</tr>%s</table>" % (html.escape(title), head, body)


def build(gold):
    costs = read_served(gold, "exec/costs_month.csv")
    seats = read_served(gold, "exec/seats_month.csv")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Exec report</title></head><body>"
            "<h1>Exec report</h1><p>An empty figure is never zero. \"withheld\" means a privacy rule hides a value of that row; "
            "\"none\" means the row has no such value.</p>"
            + table("Costs per month", costs, ["month", "provider", "usage_cost_ref", "seat_cost", "total_cost_ref"])
            + table("Seats per month", seats, ["month", "provider", "seats_billed", "active_seats", "idle_seats"])
            + "</body></html>\n")


def write_private(path, text):
    """Write the page readable by its owner only (0600), whatever the umask and whatever mode an earlier file had."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gold")
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args(argv)
    write_private(args.out, build(args.gold))
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
