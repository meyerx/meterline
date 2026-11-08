# 06 - the warehouse layout, on DuckDB

**Goal.** Show the layout that scenario C asks of a warehouse, on a local DuckDB stand-in that needs no account: one database file per audience, each holding only that audience's tables, a restricted file for the table that maps a warehouse connection to a key, and a load that replaces what an earlier build left. You get a folder you can open with any DuckDB client and the check that no file holds more than its audience's schema.

**Reads.** `<gold root>/published`, the folder the `published` link points at, and nothing else: `central/`, `exec/` and the `manager/<key>/` folders. It never opens `builds/`, `frozen/`, silver, `records/` or `delivery/`, and `load_warehouse.py` exits 2 when it is pointed at one of them, when the published folder holds one of them, when a file inside an audience's folder is a link or leaves the folder, or when `--out` sits inside the gold root. Person folders are not loaded: [example 07](https://github.com/meyerx/meterline/blob/main/examples/07-person-messages/README.md) reads them in place. The loader needs `duckdb`, which `meterline-gold` already installs.

What it writes into `--out`:

| File | Schema | Holds |
|---|---|---|
| `db_central.duckdb` | `central` | every table of `published/central/` |
| `db_exec.duckdb` | `exec` | every table of `published/exec/`; no person key |
| `db_manager_<key>.duckdb` | `manager_<key>` | every table of `published/manager/<key>/`, for that manager only |
| `db_restricted.duckdb` | `restricted` | the table `connection_keys` with the columns `connection`, `audience` and `key`, only when `--connections` is given |

DuckDB has no roles and no secure views. One file per audience stands in for one schema per audience: in a real warehouse the grants are yours to write, and [the Snowflake excerpt on the scenario C page](https://github.com/meyerx/meterline/blob/main/docs/integrate/c-lake-and-warehouse.md) shows them. Each load writes every file afresh next to the old one and replaces it, and removes the files of an earlier build that this build does not write (a manager who left, a restricted file you no longer ask for), so no table, checkpoint or copy of an earlier build is kept. The connections file holds a plain id per warehouse connection, never a name or an email address; the loader refuses a file with any other header and a connection that contains `@`. The files are readable by their owner only.

## Run it

Run this from the repository root, after the install in the README quick start. It runs the job's three steps on simulated data (the route of [example 04](https://github.com/meyerx/meterline/blob/main/examples/04-container/README.md), on the host instead of in a container), so `published/` is the folder a real run leaves; then it makes a connections file for the demo, loads the warehouse and checks it. The key is made here for the run and is not a secret; no network call goes to a provider.

```sh
export METERLINE_GOLD_KEY="${METERLINE_GOLD_KEY:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
mkdir -p warehouse-demo/config warehouse-demo/data warehouse-demo/gold
.venv/bin/python examples/04-container/prepare_demo.py warehouse-demo/config warehouse-demo/data warehouse-demo/gold
for step in silver gold publish; do .venv/bin/meterline-job run warehouse-demo/config/job.toml --step "$step"; done
MANAGER="$(ls warehouse-demo/gold/published/manager | sed -n 1p)"
printf 'connection,audience,key\nconn-0001,exec,\nconn-0002,manager,%s\n' "$MANAGER" > warehouse-demo/connections.csv
.venv/bin/python examples/06-warehouse-duckdb/load_warehouse.py warehouse-demo/gold --out warehouse-demo/warehouse --connections warehouse-demo/connections.csv --check
```

**Expected output.** The three steps print one JSON line each with `"exit_code": 0`. The loader prints `wrote N files into warehouse: central, exec, M managers, restricted` and then `ok: N files, each holding its own schema only`, and exits 0. Open `warehouse-demo/warehouse/db_manager_<key>.duckdb` with any DuckDB client: it holds one schema, `manager_<key>`, and a query for another manager's schema fails with a catalog error. Run it again: the files are written afresh and nothing of the first load stays. The tests in `tools/test_example_06_warehouse.py` build two managers with a sentinel value in the second one's rows and look for it in every table, every returned value and every byte of the first one's file; they also check that a second load keeps no row of the first, and that the loader refuses silver, `records/` and `delivery/`.

## Run it in the container

Run this after [example 04's "Run it" block](https://github.com/meyerx/meterline/blob/main/examples/04-container/README.md#run-it) and before its "Clean up": it needs the image `meterline-job:local` and the volume `meterline-gold`, which example 04 leaves in place with the published gold in it. The image carries this example's `load_warehouse.py` at `/app/load_warehouse.py`; the loader needs no gold key, so none is passed, and the gold volume is mounted read-only. The block lists the keys of `published/manager/`, writes a connections file for the demo into the config volume, creates a volume of its own, `meterline-warehouse`, loads the DuckDB files into it and checks them. Example 04's "Clean up" removes that volume too.

```sh
docker volume create meterline-warehouse >/dev/null
MANAGER="$(docker run --rm -v meterline-gold:/gold:ro --entrypoint ls meterline-job:local /gold/published/manager | sed -n 1p)"
echo "$MANAGER"
docker run --rm -v meterline-config:/config --entrypoint sh meterline-job:local -c "printf 'connection,audience,key\nconn-0001,exec,\nconn-0002,manager,%s\n' '$MANAGER' > /config/connections.csv"
docker run --rm -v meterline-gold:/gold:ro -v meterline-config:/config:ro -v meterline-warehouse:/warehouse --entrypoint python meterline-job:local /app/load_warehouse.py /gold --out /warehouse --connections /config/connections.csv --check
docker run --rm -v meterline-warehouse:/warehouse --entrypoint ls meterline-job:local /warehouse
```

**Expected output.** The first manager key, then `wrote N files into warehouse: central, exec, M managers, restricted` and `ok: N files, each holding its own schema only` (exit 0), then the names of the DuckDB files in the volume. To open one with a DuckDB client, copy the volume's files out, for example with `docker cp` from a container that mounts it.

## Use it for real

1. Load a real warehouse from the same `published/` folder, with the grants of the scenario C page's excerpt: one schema per audience, and no role that holds `central` together with another audience.
2. Make the connections file from your own sign-in system. Use an opaque id for the connection, not an address or a name, and keep the table in a restricted schema that only the role that maintains the row access reads.
3. Load after each `publish` step, from the `published` link, and never from `builds/`, `frozen/` or silver.

## What this does not prove

It does not run a real warehouse, a role, a grant, a secure view or a row access policy, and DuckDB's checkpoint says nothing about how a managed warehouse keeps time travel or fail-safe copies. It shows the layout and the replace-on-load rule; the warehouse's own settings are yours to check.
