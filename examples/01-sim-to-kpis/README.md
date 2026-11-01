# 01 - simulated data to FinOps KPIs

**Goal.** Go from nothing to the FinOps KPIs of the [cookbook](https://github.com/meyerx/meterline/blob/main/docs/how-to/finops-kpis.md) with no key and no network: simulate an organisation, build gold, query it with DuckDB.

**Reads.** `kpis.py` opens only files that `meterline_gold.servable` allows for the audience of each query: `exec/costs_month.csv`, `exec/groups_month.csv`, `exec/seats_month.csv` for the exec queries and `central/person_day.csv`, `central/person_month.csv`, `central/units.csv`, `central/reconciliation_month.csv` and `central/data_quality.csv` for the central ones. It also reads the price list you gave to the build (`--prices`, by default the example list that the command above uses), for the cache-savings query only. It never opens `delivery/` or `manifest.json`. `kpis.sql` holds the queries; the cookbook includes them word for word.

## Run it

Run this from the repository root, after the install in the README quick start.

```sh
.venv/bin/meterline-sim generate --preset tiny --seed 7 --out sim-out
export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
.venv/bin/meterline-gold build --silver sim-out/tiny-s7-m6 --org-files sim-out/tiny-s7-m6/org --policy packages/meterline-gold/src/meterline_gold/examples/policy.example.yaml --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml --out gold
.venv/bin/python examples/01-sim-to-kpis/kpis.py gold
```

**Expected output.** One block per KPI, each headed `== <name> (<audience>)` and followed by a tab-separated table. A `-` is an empty cell: in an exec table it means withheld. For example `spend_by_provider_month` lists `litellm` as withheld in every month, because too few people used it for an exec reader to see its cost.

## Use it from an app

An app that reads a gold audience folder can reuse the loaders of `kpis.py` instead of writing its own. The folder name `01-sim-to-kpis` is not a valid Python package name, so import the file by its path with `importlib`. Importing it runs nothing; `python kpis.py GOLD_DIR` is unchanged.

```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("kpis", Path("examples/01-sim-to-kpis/kpis.py"))
kpis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kpis)

con = kpis.connect("gold", "exec")                     # one view per file of gold/exec/, and the token_prices table
queries = kpis.read_queries()                          # {name: {"title", "audience", "dataset", "sql"}}, in cookbook order
rows = con.execute(queries["idle_seat_cost"]["sql"]).fetchall()
con.close()
```

- `connect(gold, audience, schemas=..., prices=...)` returns a DuckDB connection holding the views and the price table. `audience` is the folder of the reader (`exec` or `central`); only files that `meterline_gold.servable` allows for it are opened, so `delivery/` and `manifest.json` are never read. The caller closes the connection.
- `load_views(con, gold, audience, schemas=...)` and `load_prices(con, prices=...)` are the two steps `connect` takes, for an app that owns its connection. `load_views` returns the dataset names it created; a central file gets a `hidden` column that is always false.
- `read_queries(path=...)` reads the queries of `kpis.sql`. `schema_for` gives the dataset schema of an audience's file.

An empty value in an exec view is withheld, never zero; show it as withheld (see [how hidden cells show](https://github.com/meyerx/meterline/blob/main/docs/how-to/finops-kpis.md#how-hidden-cells-show)).
