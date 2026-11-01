"""Print the FinOps KPIs of docs/how-to/finops-kpis.md for a gold folder, with DuckDB over its CSV files.

    python examples/01-sim-to-kpis/kpis.py GOLD_DIR [--only NAME] [--schemas DIR] [--prices PRICES_YAML]

The queries are in kpis.sql beside this file. Each reads the files of one audience folder (exec/ or central/), as a
dashboard for that reader would: every file goes through meterline_gold.servable first, so delivery/ and manifest.json
are never opened. Column types come from the dataset schemas (docs/schemas/ of the repository, or the output of
`meterline-gold schemas --out DIR`), so money stays exact. A view ``token_prices`` holds the price list you gave to the
gold build (``--prices``, default the example list of meterline_gold): gold carries no price, so the cache-savings query
reads it from there.

As a library. The functions below can be imported by an app that reads a gold audience folder. ``connect`` opens a
DuckDB connection with the views of one audience folder and the ``token_prices`` table; ``read_queries`` returns the
queries of kpis.sql; ``load_views`` and ``load_prices`` are the two steps ``connect`` takes, for an app that wants its
own connection. The folder name ``01-sim-to-kpis`` is not a Python package name, so import the file by its path (see
the README of this example). Importing it runs nothing.
"""
import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import duckdb

import meterline_gold
import yaml
from meterline_gold import servable

HERE = Path(__file__).resolve().parent
DEFAULT_SCHEMAS = HERE.parents[1] / "docs" / "schemas"
DEFAULT_PRICES = Path(meterline_gold.__file__).resolve().parent / "examples" / "prices.example.yaml"
DUCKDB_TYPE = {"money": "DECIMAL(38,12)", "quantity": "DECIMAL(38,12)", "count": "BIGINT", "ratio": "DECIMAL(18,4)",
               "date": "DATE", "bool": "BOOLEAN"}  # every other gold type is read as text


def read_queries(path=HERE / "kpis.sql"):
    """{name: {"title", "audience", "dataset", "sql"}} from kpis.sql, in file order."""
    found = {}
    for block in re.split(r"^-- name: ", Path(path).read_text(encoding="utf-8"), flags=re.M)[1:]:
        name, _, rest = block.partition("\n")
        meta = dict(re.findall(r"^-- (audience|dataset): (\S+)$", rest, re.M))
        title = re.search(r"^-- title: (.+)$", rest, re.M).group(1)
        sql = "\n".join(line for line in rest.splitlines() if not line.startswith("-- ")).strip()
        found[name.strip()] = {"title": title, "audience": meta["audience"], "dataset": meta["dataset"], "sql": sql}
    return found


def schema_for(schemas, audience, dataset):
    """The dataset schema of the audience's form of a dataset (gold-dataset-<audience>-<dataset> if it has one)."""
    for stem in ("%s-%s" % (audience, dataset), dataset):
        path = Path(schemas) / ("gold-dataset-%s.schema.json" % stem)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise SystemExit("no dataset schema for %s/%s in %s" % (audience, dataset, schemas))


def load_views(con, gold, audience, schemas=DEFAULT_SCHEMAS):
    """One typed view per CSV file of the audience folder, named after the dataset; returns the dataset names.

    A file that ``servable`` refuses for this audience is not opened. A central file gets a column ``hidden`` that is
    always false, so the same query runs on a central and on an exec folder.
    """
    loaded = []
    for path in sorted((Path(gold) / audience).glob("*.csv")):
        rel = "%s/%s" % (audience, path.name)
        if not servable(rel, audience):
            continue
        dataset = path.stem
        schema = schema_for(schemas, audience, dataset)
        columns = ", ".join("'%s': '%s'" % (c, DUCKDB_TYPE.get(schema["properties"][c]["x-gold-type"], "VARCHAR"))
                            for c in schema["x-columns"])  # file order; the schema's keys are sorted
        extra = "" if "hidden" in schema["properties"] else ", false AS hidden"
        quoted = str(path).replace("'", "''")
        con.execute("CREATE VIEW %s AS SELECT *%s FROM read_csv('%s', header = true, columns = {%s})"
                    % (dataset, extra, quoted, columns))
        loaded.append(dataset)
    return loaded


def load_prices(con, prices=DEFAULT_PRICES):
    """A table ``token_prices(provider, model, valid_from, input_uncached, cache_read)`` of the price list: one row per
    provider, model and validity start, with the price of ONE token (the per-million price divided by 1,000,000 here, in
    exact decimal arithmetic, because a SQL division of decimals returns a float and money must stay exact)."""
    entries = yaml.safe_load(Path(prices).read_text(encoding="utf-8"))["token_prices"]
    con.execute("CREATE TABLE token_prices (provider VARCHAR, model VARCHAR, valid_from DATE, "
                "input_uncached DECIMAL(18,12), cache_read DECIMAL(18,12))")
    million = Decimal(1000000)
    for e in entries:
        per_token = [Decimal(str(e["per_million"][k])) / million for k in ("input_uncached", "cache_read")]
        if any(p != p.quantize(Decimal("0.000000000001")) for p in per_token):
            raise SystemExit("a price with more than 6 decimals per million: %s %s" % (e["provider"], e["model"]))
        con.execute("INSERT INTO token_prices VALUES (?, ?, ?, ?, ?)", [e["provider"], e["model"], str(e["valid_from"]), *per_token])


def connect(gold, audience, schemas=DEFAULT_SCHEMAS, prices=DEFAULT_PRICES):
    """A DuckDB connection holding the views of one audience folder and the ``token_prices`` table.

    It calls ``load_views`` and ``load_prices``; the caller runs a query on it (for example one from ``read_queries()``)
    and closes it. ``audience`` is the folder of the reader (``exec`` or ``central``): only files that
    ``meterline_gold.servable`` allows for that audience are opened. ``schemas`` is the folder of the
    ``gold-dataset-*.schema.json`` files, ``prices`` the price list given to the gold build."""
    con = duckdb.connect(":memory:")
    try:
        load_views(con, gold, audience, schemas)
        load_prices(con, prices)
    except BaseException:
        con.close()
        raise
    return con


def run_all(gold, schemas=DEFAULT_SCHEMAS, only=None, prices=DEFAULT_PRICES):
    """{name: (audience, columns, rows)} for every KPI (or the one named), each against its own audience folder."""
    results = {}
    for name, q in read_queries().items():
        if only and name != only:
            continue
        con = connect(gold, q["audience"], schemas, prices)
        try:
            cursor = con.execute(q["sql"])
            results[name] = (q["audience"], [d[0] for d in cursor.description], cursor.fetchall())
        finally:
            con.close()
    return results


def show(results):
    for name, (audience, columns, rows) in results.items():
        print("== %s (%s)" % (name, audience))
        print("\t".join(columns))
        for row in rows:
            print("\t".join("-" if v is None else str(v) for v in row))
        print()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gold", help="a gold folder (the output of meterline-gold build)")
    ap.add_argument("--only", help="run one KPI by name")
    ap.add_argument("--schemas", default=str(DEFAULT_SCHEMAS), help="folder of the gold-dataset-*.schema.json files")
    ap.add_argument("--prices", default=str(DEFAULT_PRICES), help="the price list given to the gold build (for cache savings)")
    args = ap.parse_args(argv)
    results = run_all(args.gold, args.schemas, args.only, args.prices)
    if not results:
        raise SystemExit("no KPI named %s" % args.only)
    show(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
