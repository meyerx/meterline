# D. Gold computed in the warehouse

D is not built. A port of gold to another SQL engine is a code change to meterline, not a configuration. meterline as shipped needs one scheduled job outside the warehouse: the connectors and stage 0 at least run there, as in [scenario A](a-one-machine.md), and the port starts from the silver folder they write. If the organisation can allow that one job, the nearest built route is [scenario C](c-lake-and-warehouse.md), which gives the warehouse the same datasets. If it cannot, the port must also move the connectors, which is new code. The [base page](README.md) defines the marks.

The table below lists what a port involves. Every step is the adopter's pattern or new code, and the table says which.

| Step | How | Maturity |
|---|---|---|
| Load silver | bulk-load the silver records that stage 0 has validated and hashed into warehouse tables | pattern |
| Keep stage 0 in Python | stage 0 reads silver, keeps the latest batch per key and replaces every person identifier with an HMAC keyed by `METERLINE_GOLD_KEY` before any engine sees the data; a port that moves the hashing into SQL lets raw identifiers into the warehouse | new code |
| Port the SQL | rewrite the macros, the aggregates, the list and map types and the recursive queries for the engine | new code |
| Port the places where Python reaches the engine | the connection, the loading, the setup files, both fixed-point loops of the privacy step, the assertions and the dataset queries | new code |
| Replace the two checks that read files | assertion A09 and the manifest read the silver JSONL and the written CSV files from disk | new code |
| Replace the folder outputs | the per-manager and per-person splits, the manifest and the owner-only file permissions are made in Python; tables need a replacement for each | new code |
| Prove it | build the gold fixture on the engine and compare it with the expected files | new code |
| Who sees what | a row access policy keyed on the reader, over the `manager/` and `person/` tables | pattern |

**Least mature step:** new code

## What a port involves

Gold is Python around SQL, and the Python part would stay:

- **Stage 0 runs in Python over folders.** `load.read_silver` reads JSONL records from one folder. Stage 0 then validates them and keeps the latest batch per key. It also replaces every person and actor identifier with an HMAC keyed by `METERLINE_GOLD_KEY`. A port keeps stage 0 and bulk-loads its tables, or rewrites the hashing in SQL and lets raw identifiers into the warehouse.
- **Python reaches the engine in more than one place.** In `run.py`: the connection, the loading, the setup files, both fixed-point loops of the privacy step, the assertions, an `information_schema` check and the dataset queries. In `write.py`: the per-person and manifest queries. In `cli.py`: the engine's error class. `write.py` also expects dates, decimals and maps as the DuckDB driver returns them.
- **Two checks read files.** Assertion A09 and the manifest read the silver JSONL and the written CSV files from disk.
- **The outputs are folders.** The per-manager and per-person splits, the manifest and the owner-only file permissions are made in Python; with tables as outputs, each needs a replacement.

The SQL uses many DuckDB features that other engines spell differently or lack. The main ones are:

- macros (`00_macros.sql`) with exact money arithmetic on 128-bit integers (`HUGEINT`) and `//` floor division;
- aggregate `FILTER (WHERE ...)`, `bool_and`, `bool_or`, `string_agg(... ORDER BY ...)` and `arg_min`;
- list and map types: `list()`, `UNNEST`, `CAST([] AS VARCHAR[])`, `map(...)` and `MAP {}`;
- `SELECT * EXCLUDE (...)`, `ROW(...) IS DISTINCT FROM ROW(...)`, `json_extract_string`, `date_diff`, `generate_series` and `range`;
- `WITH RECURSIVE` down to 64 levels, and `CREATE OR REPLACE TABLE` for more than a hundred tables.

## The engines compared

The table below gives the blocker that matters most for each engine.

| Engine | What blocks or costs most |
|---|---|
| Snowflake | no aggregate `FILTER` clause (it becomes `COUNT_IF` or `IFF`); maps become `OBJECT`, which the Python driver returns as JSON text; writes to externally managed Iceberg tables run one transaction per statement ([Snowflake](https://docs.snowflake.com/user-guide/tables-iceberg-externally-managed-writes)) |
| PostgreSQL | closest in dialect; `EXCLUDE`, the macros and the maps need rewriting, and `HUGEINT` becomes `NUMERIC(38,0)` |
| Athena (Trino) | recursive queries stop at 10 levels by default, against gold's 64; no 128-bit integer; a start-up delay and a charge per statement |
| SQLite | not viable without redesigning the money arithmetic: the macros multiply scaled amounts, which overflows 64-bit integers at a few hundred currency units |

A syntax-only translation of every statement with [SQLGlot](https://github.com/tobymao/sqlglot), on 2026-10-31, passed 160 of 161 statements for PostgreSQL and Trino and 158 for Snowflake. No translated statement was run, so the figures say nothing about equal results.

## What a port must keep

A port is correct only when all of these hold:

1. **Exact money.** Every amount stays a decimal or a scaled integer with enough range, never a binary float ([decision 0001](../decisions/0001-decimal-money.md)).
2. **The assertions.** Every query marked `-- assertion:` in `99_assertions.sql` returns no row, and A09, which runs in Python, still passes.
3. **The fixture.** A build of the [gold fixture](../gold-fixture/README.md) writes the same figures as `docs/gold-fixture/expected/`. The fixture is one month of 21 people, so it does not test the range of the arithmetic, and it leaves some files out (its README lists them).
4. **Both privacy loops.** Each still repeats until nothing changes, so a suppression that causes another one is not missed.
