#!/usr/bin/env python3
"""Print the first ```sh block under a heading of README.md, so CI runs the commands the README shows.

    python3 tools/readme_block.py "Quick start" [--readme README.md] [--lang sh]
    python3 tools/readme_block.py --sync-sql examples/01-sim-to-kpis/kpis.sql --readme docs/how-to/finops-kpis.md

The block must come before the next heading. A missing heading or block is an error, so a README edit
that breaks the quick start fails the CI job instead of silently running nothing.

--sync-sql rewrites the first ```sql block under each KPI heading of a document with the query of that title in
kpis.sql, so the cookbook and the example never drift; a test fails when the document is not in sync.
"""
import argparse
import re
import sys


def _span(text, heading, lang="sh"):
    """(first, last) line indexes of the body of the first ```<lang> block under a heading, and the lines."""
    lines = text.splitlines(keepends=True)
    head = re.compile(r"^#{1,6}\s+" + re.escape(heading) + r"\s*$")
    start = next((i for i, x in enumerate(lines) if head.match(x)), None)
    if start is None:
        raise SystemExit("no heading named: " + heading)
    first = None
    for i in range(start + 1, len(lines)):
        x = lines[i]
        if first is None and re.match(r"^#{1,6}\s", x):
            break
        if first is None and x.startswith("```" + lang):
            first = i + 1
        elif first is not None and x.startswith("```"):
            return first, i, lines
    raise SystemExit("no %s block under the heading: %s" % (lang, heading))


def block_after(text, heading, lang="sh"):
    first, last, lines = _span(text, heading, lang)
    return "".join(lines[first:last])


def sql_queries(sql_text):
    """[(title, query)] of the blocks of a kpis.sql file: each starts at "-- name:", has "-- title:" and ends at its ";"."""
    out = []
    for block in re.split(r"^-- name: ", sql_text, flags=re.M)[1:]:
        title = re.search(r"^-- title: (.+)$", block, re.M).group(1)
        query = "\n".join(x for x in block.partition("\n")[2].splitlines() if not x.startswith("-- ")).strip()
        out.append((title, query))
    return out


def sync_sql(doc_text, sql_text):
    """The document with the ```sql block under each KPI heading replaced by that KPI's query."""
    for title, query in sql_queries(sql_text):
        first, last, lines = _span(doc_text, title, "sql")
        doc_text = "".join(lines[:first]) + query + "\n" + "".join(lines[last:])
    return doc_text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("heading", nargs="?")
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--lang", default="sh", help="the fence language to extract (default sh)")
    ap.add_argument("--sync-sql", metavar="KPIS_SQL", help="rewrite the sql blocks of --readme from this file")
    a = ap.parse_args(argv)
    with open(a.readme, encoding="utf-8") as fh:
        text = fh.read()
    if a.sync_sql:
        with open(a.sync_sql, encoding="utf-8") as fh:
            new = sync_sql(text, fh.read())
        with open(a.readme, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        return 0
    if not a.heading:
        ap.error("a heading is required unless --sync-sql is given")
    sys.stdout.write(block_after(text, a.heading, a.lang))
    return 0


if __name__ == "__main__":
    sys.exit(main())
