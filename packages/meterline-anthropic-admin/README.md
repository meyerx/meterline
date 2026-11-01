# meterline-anthropic-admin

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Reads the Anthropic Admin Usage and Cost API of a Console organisation and emits [`meterline`](https://github.com/meyerx/meterline/blob/main/packages/meterline/README.md) records. Python 3.10 or later; runtime dependencies `meterline` and `httpx`.

**Status:** verified live ([evidence level](https://github.com/meyerx/meterline/blob/main/docs/glossary.md#evidence-levels)) on a small Console organisation (one model, two days of usage; cache tokens, priority tier and several workspaces not yet seen), as of 2026-10-30. It is also tested on a fake API in the shapes of the Anthropic API reference. The live run is described under [Known limits](#known-limits).

```sh
pip install meterline-anthropic-admin
pip install "meterline-anthropic-admin[parquet]"      # optional: Parquet output
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline -e "packages/meterline-anthropic-admin[dev]"
pip install -e "packages/meterline-anthropic-admin[parquet]"      # optional: Parquet output
```

Then, with an Admin key in the environment (see "Before you run it"):

```sh
export ANTHROPIC_ADMIN_KEY=...                            # never commit this
meterline-anthropic-admin backfill --provider-account-id my-org --since 2026-01-01
```

## Read this first: which Anthropic connector

meterline has two Anthropic connectors, and they read different APIs with different keys.

- `meterline-anthropic` reads the Enterprise Analytics API (key `ANTHROPIC_ANALYTICS_KEY`): per-person activity of a Claude Enterprise organisation.
- `meterline-anthropic-admin` (this package) reads the Admin Usage and Cost API (key `ANTHROPIC_ADMIN_KEY`): API usage and billed cost of a Console organisation, per API key and model.

This API sees API-key usage only. Subscription chat and Claude Code subscription usage are not in it.

## Provider status

As of 2026-10-30, from Anthropic's documentation read on 2026-10-29.

- **Both endpoints are legacy.** `GET /v1/organizations/usage_report/messages` and `GET /v1/organizations/cost_report` became legacy on 2026-10-29 (the Usage and Cost API, in Anthropic's release notes). They are still supported, and no sunset date is known. The live run of 2026-10-30 (see Known limits) used them.
- **The successors.** `GET /v1/organizations/analytics/usage` and `GET /v1/organizations/analytics/cost` (beta; they need the header `anthropic-beta: analytics-usage-cost-2026-08-24`). This connector does not call them: it stays on the legacy endpoints in the current releases. The new endpoints answer in another shape (rows with a `group` object and a `usage` or `cost` object, `time_bucket_width` instead of `bucket_width`), so the move needs new shape files and a new normaliser, not only a new header. Follow-up: the move to the new endpoints is tracked separately.
- **The version header.** The connector sends `anthropic-version: 2023-06-01`, as Anthropic's guide does, and the live run used it. Anthropic's reference examples show `2026-10-21`. **Unverified:** whether the two versions answer differently on these endpoints.

## Before you run it

Check these four things first:

- **An organisation.** The Admin API is unavailable for individual accounts. The Console account must be set up as an organisation.
- **An Admin key.** A key of the form `sk-ant-admin...`, created in the Console by an admin of the organisation. It can do much more than read reports: read [What the Admin key can do](#what-the-admin-key-can-do) before you create one. It is read from the environment variable `ANTHROPIC_ADMIN_KEY` in `run` and nowhere else. It is never read from a keychain. It is sent only as the `x-api-key` header and is never written to disk, a log or a manifest.
- **An organisation label.** `--provider-account-id` is required for `backfill` and `daily`: any non-empty label you choose. It becomes `provider_account_id`. The Anthropic organisation id is not needed and is not fetched.
- **A private data directory.** Records name API keys and show your usage. The default `exports/meterline-anthropic-admin` is gitignored. Never commit real records.

A wrapper that loads the key without echoing it keeps it out of the shell history. On macOS: `ANTHROPIC_ADMIN_KEY="$(security find-generic-password -s <service> -w)" meterline-anthropic-admin daily --provider-account-id <label>`. Run it with no `set -x` and no `env` dump.

## What the Admin key can do

An Admin key is not a read-only key, and it cannot be narrowed. The Console gives an Admin key no scopes: it has full access to every endpoint that accepts an Admin key. Besides the two reports this connector reads, the Admin API lets such a key manage members and roles, invites, workspaces and workspace members, and API keys (as the Anthropic documentation said on 2026-10-29). Treat the key like an administrator's password.

- **Who creates it.** Only a member of the Console organisation with the admin role, on the Admin keys page of the Console (`platform.claude.com/settings/admin-keys`). The key is shown once. Individual accounts have no Admin API.
- **Which keys work.** An Admin key, of the form `sk-ant-admin...`. The Anthropic documentation says a workspace API key does not work. The Anthropic documentation names other credentials that can call these endpoints (an OAuth token with the `org:admin` scope, or a personal or service-account key that is not tied to a workspace). meterline is built for the Admin key and has not been tried with them.
- **Make a dedicated key.** Create one key for meterline, name it so, give it an expiry date when the Console asks, and do not reuse it anywhere else: not in another script and not in another tool. A key that does one job can be revoked without breaking anything else, and its expiry limits the harm of a leak that nobody notices.
- **Keep it out of reach.** The environment of the process that runs meterline, or a secret store that puts it there. Not git, not a job file, not the shell history, not the data directory. meterline never writes the key, so rotating it changes nothing in your data.
- **Rotate it.** Before the expiry date, and whenever someone who had it leaves: create a new Admin key, put it in the environment, run `meterline-anthropic-admin daily` once and check that it exits 0, then revoke the old key in the Console. The documentation says an expired, revoked or malformed key gets a 401; the run then exits 3.
- **Revoke it at once** if it may have leaked (a log, a chat, a commit, a shared screen): revoke it in the Console first, then make a new one as above. Then look at the organisation's members, invites, workspaces and API keys for changes you did not make, because the key could change them.

**What meterline calls.** Only these two requests, both `GET`, both to `https://api.anthropic.com`, both with the key in the `x-api-key` header. It sends nothing else, writes nothing to your organisation and never calls the endpoints that manage members, invites, workspaces or keys:

- `GET /v1/organizations/usage_report/messages`
- `GET /v1/organizations/cost_report`

## Commands

All commands take `--data-dir` (default `exports/meterline-anthropic-admin`) and `--parquet`.

| Command | What it does |
|---|---|
| `backfill --since YYYY-MM-DD` | Walks 31-day windows back from yesterday to the start date. Resumes after a failure by re-running the same command. |
| `daily` | Reads every closed UTC day since the last day loaded, the three days before today, and every day whose latest record is not yet final. With no `state.json` it stops (exit 2) and asks for `--since YYYY-MM-DD`; `--since` is ignored once a state file exists. Safe to run twice a day: unchanged data publishes nothing. |
| `rebuild` | Re-creates every record from the raw store and the published run manifests. No API calls, no key. |

Options of `backfill` and `daily`: `--provider-account-id` (required), `--workspace-id` (optional filter), `--rate-per-minute` (default 20; the API asks for about one poll a minute, and bursts for paging are fine), `--max-retry-after`.

The state file `state.json` is checked on every `backfill` and `daily`. If it is cut short, empty, not the shape the program writes or points outside the data directory, the run stops with exit 2 before any request and names the file. Restore it from a backup, or move it aside and run `backfill --since`: published records are kept, and unchanged rows are not written twice. `rebuild` does not read it.

Exit codes, which a run also reports as `failure_kind` (`RunResult.failure_kind` from Python; a manifest written by a run that failed a check carries it too):

| Code | `failure_kind` | Meaning | What to do |
|---|---|---|---|
| `0` | `none` | The run finished. | Nothing. |
| `1` | `transient` | A 5xx, a 429 after the retries, a timeout or a connection error. | Run the same command again later; progress is kept. |
| `2` | `input` | A failed check, a response that lacks a field the connector needs or holds one of the wrong type, bad arguments or a configuration error such as a missing key. | Fix the input; running the same command again fails the same way. |
| `3` | `permanent` | The provider refused the request: 400, 401, 403, 404 or 422. The message starts "permanent:" and `RunResult.http_status` holds the status. | Fix the key, its scope or the identifier the message names; do not retry unchanged. |

To run this connector every day, with a retry rule and a gold build after it, see [the daily job example](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/README.md).

## How it works

A run has three parts:

1. **Fetcher** (`client.py`, `fetch.py`). Per window of at most 31 days, two reports. `GET /v1/organizations/usage_report/messages` grouped by `api_key_id`, `workspace_id`, `model` and `service_tier` (arrays sent as `group_by[]`), and `GET /v1/organizations/cost_report` grouped by `description` and `workspace_id`. Both use one-day buckets and follow `next_page` while `has_more` is true. Every response page is stored as received under `raw/<endpoint>/<window>/<pulled_at>.json.gz`.
2. **Normaliser** (`normalise.py`). Reads raw files only. It checks every field against the documented shape. A field it does not know goes under `unknown_fields` in the run manifest, with one warning on stderr, and the run goes on. A field it needs and does not find, or one of the wrong type, stops the run. Then it maps rows to records and checks the batch before anything is written.
3. **Publisher** (`cli.py`). Writes only the records that differ from the latest published record of the same key. A later batch wins (the silver revision rule).

The `--workspace-id` filter is sent with the usage query. The cost report has no workspace filter, so the normaliser applies it to the cost rows by their `workspace_id`.

## Files under the data directory

A run reads and writes these four things under `--data-dir`:

| Path | Contents |
|---|---|
| `raw/<endpoint>/<window>/<pulled_at>.json.gz` | One file per report and window pull, with every page's status and body text, the organisation label and the workspace filter. Endpoints: `usage_report_messages`, `cost_report`. |
| `runs/<batch_id>.json` | The run manifest: `command`, `batch_id`, `status`, `windows`, `pages`, `row_counts`, `totals` (figures per day and model), `raw_files`, `published_counts`, `failures` and `unknown_fields` (the fields of the provider that the connector does not know, as endpoint, key path and count, never a value). |
| `records/<kind>/<batch_id>.jsonl` | Published records (v2); kinds are `usage` and `org_totals`. A batch with no changed records writes no file. |
| `state.json` | `days_loaded`, and a `pending` block that lets an interrupted run resume. |

## Record mapping

Two kinds of record come from the two reports:

| Record | From | Notes |
|---|---|---|
| `UsageRecord`, `api_key` | usage rows, per day, key and model | `actor_id` is the `api_key_id`. `product` `api`, `grain` `day`, `scope` `""`, `provider` `anthropic`, `cost` null (the API does not report cost per key). |
| `UsageRecord`, `unassigned` | usage rows whose `api_key_id` is null (Console playground use) | `actor_id` is `(unassigned)`; the format requires that value. |
| `OrgTotal`, `product` `api` | the same usage rows summed per day and model, with the token cost rows of the cost report | `cost` is the sum of `amount` divided by 100, `currency` `USD`, `cost_basis` `billed`. A model with usage and no cost row has a null cost. |
| `OrgTotal`, `api-web-search` or `api-code-execution` | the cost rows of that `cost_type` | `model` `""`, tokens null. |

Fields: `tokens_input_uncached` = `uncached_input_tokens`; `tokens_cache_read` = `cache_read_input_tokens`; `tokens_cache_write_5m` and `tokens_cache_write_1h` = the two `cache_creation` buckets; `tokens_cache_write` = their sum; `tokens_input_total` = uncached + read + write; `tokens_output` = `output_tokens`. `requests`, `tokens_reasoning` and `cost_list` are null. `actor_email`, `actor_login`, `actor_deleted` and `model_provider` are null; `cost_only` is false.

`extras` of a usage record: `web_search_requests` when the row reports `server_tool_use`, and `priority_tokens` (all token buckets of the rows with `service_tier` `priority`) when it is above zero. Priority Tier is in the usage report but has no line in the cost report, so its tokens are named here and not priced.

The cost report sends `amount` as a decimal string in cents with sub-cent digits. The connector keeps every digit: `"123.78912"` becomes `Decimal("1.2378912")` dollars. A day's `is_final` is true when its bucket ended at least 48 hours before `pulled_at`.

## Response shapes

The shapes are in [`docs/api-shapes/anthropic-admin/`](https://github.com/meyerx/meterline/blob/main/docs/api-shapes/anthropic-admin/README.md), transcribed from the Anthropic API reference on 2026-10-17. The API version header is `anthropic-version: 2023-06-01`. For a new API version, read the changelog, update the shape files, and re-run the shape check before trusting a run.

What the documentation leaves open is learned from the first live response: which fields are present when a query does not group by them, which can be null, and how the cost is rounded. A field it does not know goes under `unknown_fields` in the run manifest and in one warning on stderr, and the run goes on. A needed field that is missing or of the wrong type stops the run with exit 2, so nothing wrong is published.

**When a live response differs:**

1. Run `backfill` for one day, then read the failed manifest under `runs/`. It names the endpoint and the field, never a value.
2. Copy the structure of the raw file's page into `docs/api-shapes/anthropic-admin/`, and replace every value with an invented one: ids, models, amounts. The shape files are synthetic and never carry real data. Never commit a real response.
3. Change `testing.py` and `normalise.py` to match, run `python -m pytest -q packages/meterline-anthropic-admin`, and note the difference in this package's change list under `docs/dev/`.

## Known limits

The limits of this connector, grouped by kind.

**Live run.** On 2026-10-30 a backfill and a daily run against a small Console organisation both exited 0, published, and reported no field the connector did not know. The billed cost per day in the records equalled the Console's Cost page for the same days, at the Console's two-decimal display. That organisation had one model and two days of usage, so cache tokens, the priority tier and several workspaces were not exercised. This paragraph is the record that level 3 of the [evidence levels](https://github.com/meyerx/meterline/blob/main/docs/glossary.md#evidence-levels) asks for.

**What the API shows.** The records carry these limits of the source:

- **API-key usage only.** Subscription chat and Claude Code subscription usage are not in this API. Claude Code analytics is a separate endpoint and out of scope.
- **Dimensions summed away.** Context window, inference geography and speed are summed in the query and are not split in the records. The service tier is split only to name Priority Tier tokens.
- **Cost per model and day, not per key.** The cost report has no key dimension, so a usage record has no cost. The sum of the model's cost is on the `OrgTotal`.
- **Gold's `ref_vs_source_gap` is blank for this connector.** Gold's `reconciliation_month` computes `ref_vs_source_gap` as the reference cost minus `actor_cost_source`, the cost the usage records carry. Admin usage records carry none, so `actor_cost_source` is empty and so are `ref_vs_source_gap` and `ref_vs_source_share`. The billed cost of the organisation stays in `org_cost_source`. Gold does not compare its reference cost with that billed cost.
- **Freshness.** Data is usually available within minutes and sometimes later. Cost is daily, so compare a day against the Console only after it has closed.
- **Priority Tier has no cost line.** Its tokens appear in `priority_tokens` and are not priced.

**How the connector reads it.** These follow from the connector's rules:

- **A key in two workspaces fails the batch.** The format has one record per day, key and model. If a named key shows up in two workspaces on one day, the run stops with exit 2 and names the key, instead of adding or dropping usage.
- **The console (`unassigned`) record sums workspaces.** Rows with no key are summed across workspaces, because there is no key to confuse.
- **Raw files are kept and never replaced.** A pull in the same UTC second as a finished run is refused (exit 2). Wait a second and run again.
- **A resumed run reuses the pages it already has.** After a failure, the same command picks up the pages the failed run stored (the `pending` block of `state.json`) and publishes them under the new run's `pulled_at`.
- **Finality.** A day is final from 48 hours after its bucket ended. `daily` reads again the three days before today and every day published as not final, so a gap between runs does not leave a day open.
- **Waits are capped.** A `Retry-After` header is honoured up to 300 seconds (`--max-retry-after`). One query follows at most 10,000 pages.

**Personal data.** Key ids and usage appear in raw files and records. `api_key_id` is the record's `actor_id` and can identify a person. `workspace_id`, `account_id`, `service_account_id` and the cost `description` stay in raw files and never reach a record. Logs and manifests carry counts, statuses and totals, with two exceptions. The unknown-field report names a field the provider sent, never its value, and a key seen in two workspaces on one day is named by its id. The first is kept in the `unknown_fields` and the second in the `failures` of `runs/<batch_id>.json`. [`docs/PRIVACY.md`](https://github.com/meyerx/meterline/blob/main/docs/PRIVACY.md#error-messages-that-name-an-identifier) lists them.

## From Python

```python
from meterline_anthropic_admin import run

result = run("backfill", since="2026-01-01", provider_account_id="my-org", data_dir="exports/admin",
             credentials={"ANTHROPIC_ADMIN_KEY": my_key})   # my_key: from your own secret store
print(result.exit_code, result.batch_id, result.records)
```

`run(command, *, data_dir, credentials=None, transport=None, **options)` does what the command line does, and `command` is `backfill`, `daily` or `rebuild`. The options are those of the command under their Python names (`since`, `provider_account_id`, `workspace_id`, `rate_per_minute`, `max_retry_after`, `parquet`); dates are `datetime.date` or `YYYY-MM-DD` text, and an unknown name raises `TypeError`. `credentials` is a mapping from `ANTHROPIC_ADMIN_KEY` to the key, or a function that returns one; when it is given, the environment is not read for the key. With `None`, the key comes from the environment as on the command line. The key goes only to the HTTP client.

It returns a `RunResult` (from `meterline`): `exit_code`, `ok`, `batch_id`, `manifest_path`, `records` (count per kind), `windows`, `skipped`, `failure_kind` (`none`, `transient`, `input` or `permanent`), `http_status` and `unknown_fields` (the same list as the manifest's). A failed run does not raise: it prints the same message as the command line and returns the exit code the command line would exit with (see the exit-code table under Commands).

## Testing your integration without a key

```python
from datetime import datetime, timezone
from meterline_anthropic_admin import run
from meterline_anthropic_admin.testing import FakeAPI

fake = FakeAPI()   # serves a small synthetic dataset, 2026-09-01 to 2026-09-14
result = run("backfill", since="2026-09-01", provider_account_id="my-org", data_dir="demo-data",
             credentials={"ANTHROPIC_ADMIN_KEY": fake.api_key}, transport=fake.transport,
             now=lambda: datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc))
assert result.ok
```

`FakeAPI` answers the usage and cost endpoints in the documented shapes on an `httpx.MockTransport`; pass your own rows (`FakeAPI(usage_rows, cost_rows)`) or script faults (`fake.fail(...)`). Everything it serves is synthetic. It proves that your code drives the connector and reads its records. It does not prove live authentication, the live response shapes or rate limits.

## Public interface

What later releases keep working, under the rules of the compatibility policy in [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md#compatibility):

- the `meterline-anthropic-admin` command line: its commands, options and exit codes, as listed under Commands;
- the records it publishes under `records/`, in the v2 format of [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md);
- from Python, the names in `meterline_anthropic_admin.__all__`: `run` and `RunResult` (see From Python), the error classes `ApiError` and `PaginationError`, and `__version__`;
- the `meterline_anthropic_admin.testing` module: `FakeAPI` and `sample_dataset`, as described under Testing your integration without a key.

Everything else is internal and may change in any release: every other module of the `meterline_anthropic_admin` package, and the layout of the raw files, the run manifests and `state.json`.

## Tests

```sh
python -m pytest -q packages/meterline-anthropic-admin                 # everything
python -m pytest -q packages/meterline-anthropic-admin -k done_check   # the acceptance checks A01 to A14
```

Tests run against a fake API built on `httpx.MockTransport` that serves synthetic data in the documented shapes and can script faults. The clock and sleep are injected, so nothing really waits. The network guard in `packages/conftest.py` fails any test that opens a connection to a non-loopback address.

## Calibration: a known spend, then a check

`tools/calibrate_anthropic_admin.py` spends about 1.20 USD of API credit on calls whose usage it records exactly, and then checks that the connector finds the same numbers. It spends real money, so the owner runs it; the tests never do.

1. Create a workspace with its own spend limit (for example 5 USD), and a standard API key in it. The default workspace cannot carry a limit; the limit is the hard stop.
2. Before 20:00 UTC, run `ANTHROPIC_API_KEY=... python tools/calibrate_anthropic_admin.py`. It makes the planned calls on three models, stops before a call that could take the total past 2.00 USD, and writes `calibration-<date>.jsonl` with each response's own `usage`.
3. An hour after the last call, run the connector for that one day: `meterline-anthropic-admin backfill --provider-account-id <label> --workspace-id <workspace> --since <date>`.
4. Run `python tools/calibrate_anthropic_admin.py --compare --calibration <file> --data-dir <dir> --day <date>`. Tokens must match exactly per model and bucket; cost must agree within 0.01 USD per model. The output is figures only.
