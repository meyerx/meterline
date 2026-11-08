# AGENTS.md - meterline for coding agents

meterline turns AI usage data from provider APIs (Anthropic Enterprise Analytics, Anthropic Admin, LiteLLM, GitHub Copilot) into one common record format, one line per usage event, and builds per-person and per-group datasets ("gold") from those records with privacy rules applied. It is a batch tool you run yourself: it reads a provider's usage API once a day, keeps the raw responses, and writes files you own.

Python 3.10 or later. Layers: **bronze** (raw responses), **silver** (the common records), **gold** (datasets per audience). Terms are defined in `docs/glossary.md`; the index by goal is `docs/README.md`.

## Evidence level

Levels: [docs/glossary.md](docs/glossary.md#evidence-levels). The date of the last live run is in the Status paragraph of [README.md](README.md).

- `meterline-anthropic-admin`: verified live on a small Console organisation (one model, two days of usage; cache tokens, priority tier and several workspaces not yet seen).
- `meterline-litellm`: built against documented shapes and checked against LiteLLM v1.106.6's own route code.
- `meterline-anthropic`: built against documented shapes.
- `meterline-copilot`: built against documented shapes.

No other connector has run against a live account. A real response may differ from the documented shape.

## Install

Run from the repository root, in a virtual environment (`python3 -m venv .venv`). Upgrade pip first (`.venv/bin/python -m pip install --upgrade pip`): an older pip cannot do an editable install.

- Simulated data to gold, and everything offline: `.venv/bin/python -m pip install -e packages/meterline -e packages/meterline-sim -e packages/meterline-gold`
- A connector on a real account: `.venv/bin/python -m pip install -e packages/meterline -e packages/meterline-anthropic` (or `-anthropic-admin`, `-litellm`, `-copilot`). Add `[parquet]` to the package for Parquet output.
- The daily job: add `-e packages/meterline-job` and the connectors it runs, or `pip install "meterline-job[all]"`; see `packages/meterline-job/README.md`.
- The test suites: install every package with its `dev` extra, as the Quick start in `README.md` shows.

## Commands: offline and provider

Safe offline (no key, no network):

- `meterline-sim generate --preset tiny --seed 7 --out sim-out` writes an invented organisation and its silver records.
- `meterline-gold build --silver SILVER --org-files ORG --policy POLICY --prices PRICES --out gold` builds the gold folder. It needs `METERLINE_GOLD_KEY`, a key of at least 32 bytes that you invent and keep out of git. The call is in the README section "Try it without a key".
- `python -m pytest -q ...` (see Tests).

These call a provider and need that provider's key in the environment. The other connectors need read access only; the Anthropic Admin key is not read-only (it can also manage members, workspaces and API keys; see the README of `meterline-anthropic-admin`). Do not run them without the owner's say-so:

- `meterline-anthropic`, `meterline-anthropic-admin`, `meterline-litellm`, `meterline-copilot`, each with `backfill`, `daily` or `rebuild`. Only `rebuild` makes no call: it rebuilds records from the stored raw responses.
- `meterline-job run job.toml` runs the connectors, silver, gold and publish, one JSON status line per step.

## Examples

- `examples/01-sim-to-kpis/`: simulated data to the FinOps KPIs with DuckDB (`docs/how-to/finops-kpis.md` explains them).
- `examples/02-exec-report/`: a stdlib-only static HTML report from `exec/` only, checking `meterline_gold.servable` for every file.
- `examples/05-dashboard/`: a stdlib-only dashboard that serves one audience folder of `published/` and refuses every path `meterline_gold.servable` refuses; `--check` renders once.
- `examples/04-container/`: a Dockerfile for `meterline-job`, a Kubernetes CronJob and an ECS task definition; no key in the image; the image also carries example 05's dashboard.
- `examples/06-warehouse-duckdb/`: loads `published/` into DuckDB, one file per audience; it refuses silver, `records/` and `delivery/`; `--check` checks each file holds its own schema only.
- `examples/07-person-messages/`: writes one `.eml` file per person from `published/person/<key>/` and `builds/<latest>/delivery/directory.csv`, with no key in a message; sending through SES needs `--send` and `METERLINE_MAIL_CONFIRM=yes` and is refused when `CI` is set.
- `examples/03-daily-job/`: every connector through its Python entry point, with a public fake in `<package>.testing`, then gold, as JSON status lines; `docs/how-to/operate-daily.md` covers running it for real.

## The five goal paths

To run meterline on a platform, read [docs/integrate/README.md](docs/integrate/README.md); the same five scenarios, with the maturity of every step, are in [docs/integrate/scenarios.yaml](docs/integrate/scenarios.yaml). For a data lake and a warehouse, read [docs/integrate/c-lake-and-warehouse.md](docs/integrate/c-lake-and-warehouse.md).

1. **Try it on simulated data:** README "Try it without a key", then `docs/tutorial.md`.
2. **Run a connector on a real account:** README "Run a connector" and the connector's own README under `packages/`.
3. **Build a dashboard from gold:** `packages/meterline-gold/README.md` for what a build writes; read the audience folder of the reader.
4. **Add a provider:** `docs/how-to/add-a-provider.md`; the rules a connector keeps are in `docs/reference/connector-contract.md`, checked by `meterline.conformance`, and `.venv/bin/python tools/new_connector.py NAME` makes the skeleton.
5. **Run the daily job and serve `published/` per reader:** `docs/how-to/operate-daily.md`, `packages/meterline-job/README.md`, `examples/03-daily-job/`. Each reader receives only their own sub-folder; `meterline_gold.servable(path, audience)` is the check.

## Where the contracts live

- **Silver record schemas** (JSON Schema, one per record kind): `docs/schemas/usage.schema.json`, `activity.schema.json`, `org_totals.schema.json`, `seats.schema.json`, `seat_assignments.schema.json`. Field meanings: `docs/records.md`.
- **Gold inputs and manifest:** the `gold-org-*.schema.json` files and `gold-manifest.schema.json` in `docs/schemas/`.
- **Gold dataset schemas** (one row of every file a build writes, with types, units, descriptions, keys and joins): `docs/schemas/gold-dataset-<name>.schema.json`; the same as a page, with the rule for which cost column to sum: `docs/reference/gold-datasets.md`.
- **Audience rules** (which file may be served to which reader): `docs/schemas/gold-audiences.json`; from Python, `meterline_gold.servable(path, audience)`.
- **Connector contract** (layout, state, revision rule, exit codes, the fake API, the wiring checklist): `docs/reference/connector-contract.md`.
- **Exit codes:** each connector README has an "Exit codes" table: `0` success, `1` transient (a 5xx, a 429 after retries, a timeout; run again later), `2` input or checks (bad arguments, a missing key, a damaged state file, a failed record check; fix the input), `3` permanent provider refusal (400, 401, 403, 404, 422; fix the key, scope or identifier, do not retry). `RunResult.failure_kind` names the same fact (`none`, `transient`, `input`, `permanent`) and `RunResult.http_status` the HTTP status.
- **The job:** it adds exit code `4` (gold or publish failed) to the connectors' 0 to 3. `central/published.json` and `freshness.json` have schemas in `docs/schemas/job/`.
- **Record compatibility rules:** `docs/records.md`, section Compatibility.

## Privacy rules

Follow these without exception, in any dashboard or script you write.

1. Never serve `delivery/` or `manifest.json` to anyone but the people who run the build: `delivery/directory.csv` holds names and emails, and the manifest lists every person and manager key.
2. A dashboard reads the audience folder of its reader (`exec/`, `manager/<key>/` or `person/<key>/`), never a wider one.
3. `central/` is for the central team only. Do not serve it to a manager, a person or an executive.
4. An empty value with `hidden = true` is withheld, never zero. Show it as withheld; do not sum it, chart it as zero or fill it in.
5. Never commit a key, a token or a real export. Read keys from the environment; keep `.env` out of git; use synthetic data only in fixtures and examples.
6. Silver holds person identifiers (emails, logins) with no suppression. Treat the silver folder like personal data and build any dashboard on gold.

Who gets which folder (rules 2 and 3 for each role; `meterline_gold.servable(path, audience)` checks a single file):

| Role | Folder it gets | Never receives |
|---|---|---|
| Central team, platform lead, FinOps | `central/` (and `manifest.json`) | `delivery/` (names and emails; only the people who run the build see it) |
| Executive | `exec/` | `central/`, `manager/`, `person/`, `delivery/` or `manifest.json`; no per-person row (the exec files hold no person key) |
| Manager | `manager/<key>/` | another manager's folder, `central/`, `exec/`, `person/`, `delivery/` or `manifest.json`; never the organisation cell |
| Individual | `person/<key>/` | another person's folder or key, `central/`, `manager/`, `exec/`, `delivery/` or `manifest.json` |

## Tests

- **Fast tier**, seconds to a minute, enough while you work: `.venv/bin/python -m pytest -q packages/meterline -p no:cacheprovider`, or one package folder at a time (`packages/meterline-gold`).
- **Tools tier**, about 10 minutes: `.venv/bin/python -m pytest -q -n 4 tools -p no:cacheprovider`. It imports every package, so install every package first, as the Quick start in `README.md` shows.
- **Full suite**, as CI runs it: `.venv/bin/python -m pytest -q -n 4 packages tools -p no:cacheprovider -rs`. It takes a long time (the simulator's property tests are most of it); a growing line of dots is healthy.
- Acceptance tests are the ones named `test_done_check_*`; `-k done_check` selects them. Nothing in the suite needs the network or a key.
- Lint: `ruff check packages/*/src`.

## What a change must keep

- **Determinism:** the same inputs give byte-identical outputs. Time and randomness are injected; a seed fixes the simulator. Do not read the clock or the environment inside record or gold code.
- **Decimal money:** amounts are `Decimal` end to end and serialised as strings. Never use `float` on a cost path.
- **No prompt content:** no record, log line or file holds the text of a prompt or a response.
- **Null means not reported:** a measure a source does not report is `null`, never `0`.
- **Frozen specs:** an accepted spec under `docs/dev/` is not edited; findings go to the change list.
- **Stage files by name,** never `git add -A`.
