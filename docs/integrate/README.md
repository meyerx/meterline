# Integrate meterline with the platform you have

This page is for an adopter who wants to run meterline on the platform that the organisation already has. It describes five scenarios, from one machine to a data lake with a warehouse, and marks every step of each with how mature meterline is for it. It ends with what meterline could add to make each scenario easier. The same facts are in [`scenarios.yaml`](scenarios.yaml), for a program or an agent to read.

**What is built today.** The connectors, the common record format, gold on DuckDB, the job that runs them all ([`meterline-job`](../../packages/meterline-job/README.md)), a container example and a minimal dashboard are built and tested on simulated data or on the connectors' fake APIs. Scenarios A and B use only that. Scenario C adds a warehouse layout on a local DuckDB stand-in ([example 06](../../examples/06-warehouse-duckdb/README.md)) and one message per person written as files ([example 07](../../examples/07-person-messages/README.md)), both run on simulated data; its loading into Snowflake and S3 and its sending through SES are patterns. Scenarios D and E need code that meterline does not have, and none of C, D and E has been run end to end.

## How the maturity marks read

Each step of a scenario carries one of four marks. The marks describe the integration, not the connectors: how far each connector has been checked against its provider is a separate scale, the [evidence levels](../glossary.md#evidence-levels).

| Mark | Meaning |
|---|---|
| **verified** | Built, and run against a real account. |
| **built** | Built, and tested on simulated data or a fake API. |
| **pattern** | Not part of meterline: the adopter assembles it from standard tools, as the page describes. |
| **new code** | Needs code that meterline does not have; [What meterline could add](#what-meterline-could-add) lists it. |

A scenario's least mature step is the step with the last mark in that order.

## The scenarios at a glance

The table below gives each scenario, who it is for and the least mature step it contains. Choose the first scenario that fits the platform: a later one adds work.

| Scenario | For | Where gold runs | Where people read | Least mature step |
|---|---|---|---|---|
| [A. One machine](a-one-machine.md) | trying meterline, or a small team | DuckDB, on the machine | the reference dashboard, or files | built |
| [B. Kubernetes and a Python dashboard](b-kubernetes.md) | a platform team that runs containers | DuckDB, in a scheduled container | a Python dashboard over the published folder | pattern |
| [C. Containers, a data lake and a warehouse](c-lake-and-warehouse.md) | an organisation with a data lake and a warehouse | DuckDB, in a scheduled container | the warehouse, through a Python dashboard or a BI tool | pattern |
| [D. Gold computed in the warehouse](d-gold-in-warehouse.md) | an organisation that may run no batch outside its warehouse | the warehouse | the warehouse | new code |
| [E. Agents over gold](e-agents.md) | teams whose AI agents build FinOps views | any of A, B or C | an MCP server over gold | new code |

Scenario C gives a warehouse everything it needs without a port of gold, so choose D only when C is ruled out.

## What every scenario shares

Every scenario runs the same four steps in this order, once a day, as one command: `meterline-job run CONFIG`. The configuration format is in [example 03](../../examples/03-daily-job/README.md) and the command in the [`meterline-job` README](../../packages/meterline-job/README.md).

1. **The connectors** pull each provider into its own data folder with their `auto` command, `backfill` until one has published and `daily` afterwards (built; one connector verified). The folder must persist between runs: `state.json` and the raw files are what `daily`, resume and `rebuild` rely on.
2. **The silver step**, `meterline-job run CONFIG --step silver`, copies every connector's published records into one folder, because gold reads `records/<kind>/*.jsonl` from a single folder and reads JSONL only (built).
3. **Gold**, `--step gold`, builds into a new, dated folder under `builds/`, one per run, because a build refuses an output folder that is not empty (built). `parquet = true` in the configuration writes Parquet beside the CSV files.
4. **Publish**, `--step publish`, freezes the months that are closed, delivers each once, and points the `published` link of the gold root at the folder to serve (built).

Each step can run alone. Each reader receives only their own sub-folder of `published/`, never the latest build folder and never the whole of `published/`: [`meterline_gold.servable(path, audience)`](../../packages/meterline-gold/README.md#who-gets-which-folder) is the check.

What a run must never do, in any scenario:

- serve `delivery/` to anyone: `delivery/directory.csv` is the only file with names and emails;
- serve a folder to a reader whose audience it is not: [`meterline_gold.servable(path, audience)`](../../packages/meterline-gold/README.md#who-gets-which-folder) says which file may go to whom;
- serve `builds/`, `frozen/` or a `published-*` folder: they are central-only material;
- retry gold on its own. A failed gold build is exit 4 of the job and a wrong input is exit 2; gold is never retried by the job, because neither clears on a retry. Only exit 1, a transient failure of a connector or a busy folder, is worth retrying ([exit codes of the job](../../packages/meterline-job/README.md#exit-codes), [of a connector](../reference/connector-contract.md#r11)).

**One run at a time.** The job holds a lock per data folder and one on the gold root, so a second run that meets a busy folder skips it and exits 1. The lock is tested on local disk only; a network disk is the adopter's to verify. A scheduler should still start one job at a time per data folder.

## Out of scope: per-request real time

meterline cannot count or block each AI request as it happens. It runs as a batch and enforces nothing, and the provider usage APIs it reads report with a delay, so they cannot feed a decision taken during a request.

A component on the path of every request is a different product. It adds its delay to every request and must hold many connections with small, steady memory use. If it stops, every request stops or goes through unchecked. Those constraints favour a compiled language with cheap concurrency, such as Go or Rust, deployed beside the proxy; some proxies have budget limits of their own, which are the first thing to try. Such a component could read meterline's policy file and write meterline records, so that gold reports its counts. It is not built and not planned.

## What meterline could add

These additions would turn the pattern and new-code steps above into built ones. They are proposals, not plans, and each says which scenarios it helps.

| Addition | Helps |
|---|---|
| A published container image, so that an adopter pulls one instead of building example 04's Dockerfile | B, C |
| A loader from a gold build to Iceberg tables and Snowflake, safe to run twice for the same build | C |
| An alert hook on a failed step (webhook or SNS) | B, C |
| A supported email sender from the person folders and `delivery/directory.csv` (SES, Microsoft Graph); [example 07](../../examples/07-person-messages/README.md) is an example of one, locked, and not a module | C |
| One engine interface for every place Python reaches the engine, with a conformance test that builds the gold fixture on each engine | D |
| Portable SQL where it costs nothing (`EXCLUDE`, `FILTER`, `//`), and Parquet as a silver input | D |
| A read-only MCP server over gold | E |

The job command, `auto`, the lock per data folder and the `published` folder are built and no longer additions. The Dockerfile ([example 04](../../examples/04-container/README.md)) and the reference dashboard ([example 05](../../examples/05-dashboard/README.md)) are built examples.
