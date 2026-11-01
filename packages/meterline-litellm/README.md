# meterline-litellm

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Reads a LiteLLM proxy's admin API and emits [`meterline`](https://github.com/meyerx/meterline/blob/main/packages/meterline/README.md) records. Python 3.10 or later; runtime dependencies `meterline` and `httpx`.

**Status:** built against documented shapes ([evidence level](https://github.com/meyerx/meterline/blob/main/docs/glossary.md#evidence-levels)), here the LiteLLM source code (spec v2 sections 5 and 8, [`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md)), with acceptance tests and unit tests on a fake proxy. It was also checked against LiteLLM v1.106.6's own route code, which covers the shape of the replies, not the database, the sign-in or the timing. Not yet run against a live account. See [Known limits](#known-limits).

```sh
pip install meterline-litellm
pip install "meterline-litellm[parquet]"      # optional: Parquet output
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline -e "packages/meterline-litellm[dev]"
pip install -e "packages/meterline-litellm[parquet]"      # optional: Parquet output
```

Then, with a read-only proxy key in the environment (see "Before you run it"):

```sh
export LITELLM_BASE_URL=https://proxy.example.com
export LITELLM_API_KEY=...                                 # never commit this
meterline-litellm backfill --proxy-name my-proxy --since 2026-01-01
```

## Read this first: two views of the same traffic

`proxy_user` and `end_user` records describe the **same traffic** and must never be added together.

- `proxy_user`: the person the proxy charged the call to (the owner of the key, or the signed-in user under JWT). Always written.
- `unassigned`: usage the proxy attributes to no person (keys with no owner, JWT calls without a user claim). Always written when there is any. `proxy_user` plus `unassigned` equals the proxy's total.
- `end_user`: the person named by the calling application. Optional, off by default (`--end-users`). It repeats traffic already in the `proxy_user` records.

Also: when the proxy routes calls to a provider that has its own connector, the same calls appear in both sources. `provider` says which source reported a record; do not add a proxy's records to the serving provider's records for the same traffic.

## Before you run it

Check these five things first:

- **Version.** The proxy must be **v1.67.0-stable or later**. v1.66.0-stable lacks the daily cache-token columns (migration `20250415151647_add_cache_read_write_tokens_daily_spend_transactions`); v1.67.0-stable has them, the cache columns of the daily metrics, `breakdown.entities` and the key metadata's `team_id` (PR #10152). The exact tag was resolved from the repository's tags. The shapes were transcribed from v1.104.2 (read on 2026-10-11), and on 2026-10-29 the reply shapes were checked again against v1.106.6 (see Response shapes). An older proxy answers 404 on the daily activity route; the run then stops with "path not found: wrong base URL, or a proxy older than v1.67.0-stable".
- **Key.** A key of a user with role `proxy_admin_viewer` (read-only). It may call every route used here and nothing that writes. `--end-users` needs `proxy_admin` or `proxy_admin_viewer`; other roles get 401 from that route.
- **Environment.** `LITELLM_BASE_URL` (for example `https://proxy.example.com`) and `LITELLM_API_KEY`. They are read in `main` and nowhere else; the key is sent only as `Authorization: Bearer <key>` and never written to disk, a log or a manifest.
- **Proxy name.** `--proxy-name` is required for `backfill` and `daily`: any non-empty label you choose. It becomes `provider_account_id`.
- **Database.** The daily routes need the proxy to run with its database; without one they answer 500.

## Commands

All commands take `--data-dir` (default `exports/meterline-litellm`, gitignored) and `--parquet`.

| Command | What it does |
|---|---|
| `backfill --since YYYY-MM-DD` | Walks 31-day windows back from yesterday to the start date. Resumes after a failure by re-running the same command. |
| `daily` | Reads every closed UTC day since the last day loaded, and always re-reads the two days before today (rows arrive late). With no `state.json` it stops (exit 2) and asks for `--since YYYY-MM-DD`, the first day to load, rather than quietly fetching the month again; `--since` is ignored once a state file exists. Safe to run twice a day: unchanged data publishes nothing. |
| `rebuild` | Re-creates every record from the raw store and the published run manifests. No API calls, no key. |

Options of `backfill` and `daily`: `--proxy-name`, `--rate-per-minute` (default 120; the routes document no limit), `--end-users`.

The state file `state.json` is checked on every `backfill` and `daily`. If it is cut short, empty, not the shape the program writes or points outside the data directory, the run stops with exit 2 before any request and names the file. Restore it from a backup, or move it aside and run `backfill --since`: published records are kept, and unchanged rows are not written twice. `rebuild` does not read it.

Exit codes, which a run also reports as `failure_kind` (`RunResult.failure_kind` from Python; a manifest written by a run that failed a check carries it too):

| Code | `failure_kind` | Meaning | What to do |
|---|---|---|---|
| `0` | `none` | The run finished. | Nothing. |
| `1` | `transient` | A 5xx, a 429 after the retries, a timeout or a connection error. | Run the same command again later; progress is kept. |
| `2` | `input` | A failed check, bad arguments or a configuration error such as a missing key or address. | Fix the input; running the same command again fails the same way. |
| `3` | `permanent` | The provider refused the request: 400, 401, 403, 404 or 422. The message starts "permanent:" and `RunResult.http_status` holds the status. | Fix the key, its scope or the identifier the message names; do not retry unchanged. |

To run this connector every day, with a retry rule and a gold build after it, see [the daily job example](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/README.md).

## How it works

1. **Fetcher** (`client.py`, `fetch.py`). Per window: one call to `GET /user/daily/activity` without `user_id` (the global view), then one call per user found in the global view's `breakdown.entities`. Entities with an empty key or the key `Unassigned` are not called. With `--end-users`: one call to `GET /customer/daily/activity` for all end users, then one call per end-user id. `GET /user/list` once per run, only to read `user_id` → `user_email`. Every response page is stored as received under `raw/<endpoint>/<window>/<view>/<pulled_at>.json.gz`; per-user file names carry a hash of the id, never the id.
2. **Normaliser** (`normalise.py`). Reads raw files only. Pages are merged by date (a day can be cut across two pages with partial numbers): metrics are summed, breakdown maps merged key by key.

At 1,000 active people this is about 1,000 calls per window for each view.

## Files under the data directory

A run reads and writes these four things under `--data-dir`:

| Path | Contents |
|---|---|
| `raw/<endpoint>/<window>/<view>/<pulled_at>.json.gz` | One file per view and window pull: the global view, or one user or end user (the folder name is a hash of the id, never the id), with every page's status and body text. |
| `runs/<batch_id>.json` | The run manifest: `command`, `batch_id`, `status`, `windows`, `pages`, `row_counts`, `totals`, `unknown_products`, `unknown_models`, `token_anomalies`, `modelless_days`, `skipped_entities`, `raw_files`, `published_counts`, `failures` and `unknown_fields` (the fields of the provider that the connector does not know, as endpoint, key path and count, never a value). |
| `records/<kind>/<batch_id>.jsonl` | Published records (v2); kinds are `usage` and `org_totals`. A batch with no changed records writes no file. |
| `state.json` | `days_loaded`, and a `pending` block that lets an interrupted run resume. |

## Record mapping

Four kinds of record come from the proxy:

| Record | From | Notes |
|---|---|---|
| `UsageRecord`, `proxy_user` | each per-user view, per date and model | `actor_email` from `/user/list` (null if absent), `model_provider` null (the daily view cannot tie a model to a provider) |
| `UsageRecord`, `unassigned` | global view minus the sum of the per-user views, per date and model | only when any field is non-zero; a negative difference fails the run |
| `UsageRecord`, `end_user` | each per-end-user view (`--end-users`) | `actor_login` is the end user's alias; no unassigned record is derived |
| `OrgTotal` | the global view's `breakdown.models`, per date and model | `product` `""` and `model_provider` null, as for usage; no all-models row |

Fields: `tokens_input_total` = `prompt_tokens`; `tokens_cache_read` = `cache_read_input_tokens`; `tokens_cache_write` = `cache_creation_input_tokens`; `tokens_input_uncached` = total − read − write; `tokens_output` = `completion_tokens`; `requests` = `api_requests`; `cost` = `spend`, `cost_list` null, `currency` `USD`, `cost_basis` `estimate` (LiteLLM prices calls from its own price list). LiteLLM sends `spend` and `flat_cost` as floating-point numbers with up to 19 decimals, and the connector rounds them to the 12 decimals of the record format (half to even). `extras`: `successful_requests`, `failed_requests`, and `flat_cost` when non-zero. The 5m/1h cache split and `tokens_reasoning` are null. **Unverified:** whether LiteLLM's `prompt_tokens` includes the cache figures. It may differ by provider, and the sources read on 2026-10-29 do not settle it. The connector assumes it does, because `tokens_input_uncached` is total − read − write. A live run should confirm this before `tokens_input_uncached` is trusted. If it is wrong, `tokens_input_uncached` is too small (or null) and `tokens_input_total` is too small by the cache figures. If total − read − write is negative, `tokens_input_uncached` is null and the key is listed in the manifest under `token_anomalies` (`<date>|<model>|<actor_kind>|<12 hex of sha256(actor id)>`).

**Where `requests` comes from.** `api_requests` counts rows of LiteLLM's spend logs, one row per logged request (LiteLLM's cost-tracking documentation and route code, read on 2026-10-29). It is not a count of answers the proxy gave. A request that was not logged is not counted, and each upstream attempt, a retry for example, counts as its own request. `successful_requests` and `failed_requests` come from the status in the same log rows.

The key and the constant fields are the same in every LiteLLM record:

- `provider` `litellm`; `provider_account_id` the `--proxy-name` label; `scope` `""`; `grain` `day`, with `period_start` = `period_end` = the row's date;
- `actor_id`: the proxy's `user_id` for `proxy_user`, `(unassigned)` for `unassigned`, the end user's id for `end_user`; `actor_email` from `/user/list` for `proxy_user` only; `actor_login` the end user's alias, else null;
- `product` `""` (the daily view names no product); `actor_deleted` null (the proxy does not say); `cost_only` false;
- `cost_list` null and `data_refreshed_at` null (the proxy reports neither).

`is_final` is true when the day ended at least 2 full UTC days before the pull. No `ActivityRecord`, `SeatSnapshot` or `SeatAssignment` comes from LiteLLM.

Records are built and checked with `meterline.records` (`UsageRecord`, `OrgTotal`, `batch_failures`, `latest_per_key`) and written and read with `meterline.writers` (`write_jsonl`, `write_parquet`, `read_jsonl`), the two submodule routes the core package `meterline` keeps stable. A script that reads this connector's output can import the same names from those routes or from the package root.

## Response shapes

[`docs/api-shapes/litellm/`](https://github.com/meyerx/meterline/blob/main/docs/api-shapes/litellm) holds one file per endpoint with synthetic values, and a README naming the source file and line of each in the LiteLLM source at tag `v1.104.2` (read on 2026-10-11). On 2026-10-29 the route functions of LiteLLM v1.106.6 were run with a fake database and their reply shapes compared with these files; that check covers reply shapes, not the SQL, the sign-in or the timing, and the folder's README lists what changed. Done-check L01 compares what the fake proxy returns with these files, by keys and nesting. It cannot show that a live proxy answers the same way.

**When a live response differs.** A live run is the first real test of these shapes. If a response does not match its shape file:

1. Save the live response's structure as the new shape file, with every value replaced by an invented one: synthetic values only, and never commit a real response.
2. Run the shape check, `python -m pytest -q packages/meterline-litellm -k "L01"`. L01 fails and names each key that differs between the fake API and the file.
3. Change the fake API to match, then `fetch.py` and `normalise.py`, until the shape check and the other acceptance tests pass.
4. Update the source and the date read in the shape folder's README, and add an entry to the change list of the spec.

**When the proxy is upgraded.** The routes are versioned by the proxy's own release, not by a header: the shapes were transcribed from `v1.104.2` (read on 2026-10-11) and re-checked against `v1.106.6` on 2026-10-29, and the connector needs `v1.67.0-stable` or later. After an upgrade, compare the release notes of the routes used here with the shape files.

## Known limits

The limits of this connector, grouped by kind.

**How the connector reads the proxy.** These follow from the proxy's API and the connector's rules:

- **Raw files are kept and never replaced.** Every pull writes its own `raw/.../<pulled_at>.json.gz`; nothing in the connector deletes or rewrites one, so the evidence a record was built from stays. A second pull in the same UTC second would need the same file name. Such a run is refused (exit 2) before it asks the proxy anything, or, if it had started, stops without replacing the file; a run in the second of a finished run is refused too. Wait a second and run again. Raw files grow with every run; archive or delete old ones by hand once the records they produced are no longer needed for a `rebuild`.
- **Page loops end.** One query follows at most 10,000 pages (`MAX_PAGES` in `fetch.py`). A page that repeats the one before, a page number other than the one asked for, or more pages than that, stops the run with exit 2 and a failed manifest.
- **Waits are capped.** A `Retry-After` header is honoured up to 300 seconds (`--max-retry-after`); both the delay-seconds and the HTTP-date forms are read, and a value that is not a finite number is ignored in favour of the back-off.
- **End-user ids with a comma** cannot be asked for one at a time (the route splits on commas); they are skipped and counted under `skipped_entities.ids_with_comma` in the manifest.
- **Re-reads and removals.** A re-read day writes a record only when a key's values changed. A key that vanishes (for example an `unassigned` difference that falls to 0 after late rows were re-attributed) is not replaced, so its earlier record stays the latest.
- **A resumed run reuses the pages it already has.** After a failure, the same command picks up the pages the failed run stored (the `pending` block of `state.json`), however old they are, and publishes them under the new run's `pulled_at`. Running a different command in between drops them, and a run whose checks fail clears them, so a failed long `backfill` asks for everything again.
- **Finality.** `daily` re-reads the two days before today, and a day is final only from the third day after it, so records written only by `daily` stay `is_final = false`; `backfill` over the period writes the final ones.

**What the proxy reports.** The proxy's own figures carry these quirks, and the records keep them:

- **Usage with no model.** The proxy counts a raw row with a null model in the day's metrics but leaves it out of `breakdown.models`. The connector keeps that usage under `model` `""` (in the per-user records and, for ownerless usage, the `unassigned` record), so that the totals still add up. The `OrgTotal` has per-model rows only; the manifest lists such dates under `modelless_days`.
- **Failed requests** are counted inside the proxy's request and token counters as the proxy reports them; meterline does not split them. LiteLLM's release notes for v1.106.2 (2026-10-21) say that timeouts, cancellations and billed retries are now priced instead of logged at $0. **Unverified:** how far this changes `spend` in the daily rows; the code behind it was not traced. Cost for days with failed requests can differ between proxy versions.
- **Rounding noise.** The proxy sums `spend` as binary floating point, so sums differ by about 1e-16. A money difference below 0.000001 counts as zero when records are checked against totals and when the `unassigned` difference is derived. Costs keep the digits the proxy sent.

**What it does not read.** The connector reads the daily activity routes and the user list only.

- **Not read:** the database path, per-request spend logs, the licence-gated `*/spend/report` routes, team and tag views, any per-request field (`messages`, `response`, `metadata`).

**Personal data.** A user id and an email appear in raw files and records (they are data); logs and manifests carry counts, statuses and hashes only.

## Public interface

What later releases keep working, under the rules of the compatibility policy in [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md#compatibility):

- the `meterline-litellm` command line: its commands, options and exit codes, as listed under Commands;
- the records it publishes under `records/`, in the v2 format of [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md);
- from Python, the names in `meterline_litellm.__all__`: `run` (see [From Python](#from-python)), `RunResult`, the error classes `ApiError`, `PaginationError` and `PathNotFound`, and `__version__`;
- the module `meterline_litellm.testing` (`FakeAPI`, `sample_dataset`, see [Testing your integration without a key](#testing-your-integration-without-a-key)).

Everything else is internal and may change in any release: every other module of the `meterline_litellm` package, and the layout of the raw files, the run manifests and `state.json` (they are documented so you can read them, not so you can build on them).

## From Python

```python
from datetime import date
from meterline_litellm import run

result = run("backfill", since=date(2026, 9, 1), proxy_name="my-proxy", data_dir="exports/meterline-litellm",
             credentials={"LITELLM_API_KEY": "...", "LITELLM_BASE_URL": "https://proxy.example.com"})
print(result.exit_code, result.batch_id, result.records)
```

`run(command, *, data_dir, credentials=None, transport=None, **options)` does what the command line does; `command` is `"backfill"`, `"daily"` or `"rebuild"` and the options are the command-line options under their Python names (`since`, `proxy_name`, `end_users`, `rate_per_minute`, `max_retry_after`, `parquet`), plus `base_url`, which overrides `LITELLM_BASE_URL`. `credentials` is a mapping from `LITELLM_API_KEY` and `LITELLM_BASE_URL` to their values, or a function without arguments that returns one; when it is given the environment is not read, and a missing name counts as unset. With `None` the environment is read as the command line does. The key goes only to the HTTP client. `run` returns a `RunResult` (`exit_code`, `batch_id`, `manifest_path`, `records`, `windows`, `skipped`, `unknown_fields`, `ok`). A failed run does not raise: it returns the exit code the command line would exit with, after printing the same message.

## Testing your integration without a key

```python
from datetime import date
from meterline_litellm import run
from meterline_litellm.testing import FakeAPI

fake = FakeAPI()                      # a small synthetic dataset, built in code
result = run("backfill", since=date(2026, 9, 1), proxy_name="my-proxy", data_dir="out",
             credentials={"LITELLM_API_KEY": fake.api_key, "LITELLM_BASE_URL": "https://proxy.example.com"},
             transport=fake.transport)
assert result.ok
```

`FakeAPI` answers the three routes in the documented shapes from synthetic data (four users at `example.com` addresses, two models, 2026-09-01 to 2026-09-14; pass your own rows to change that) and can script faults. It does not prove that a live proxy accepts your key, that a live proxy answers in these shapes, or how a live proxy rate-limits.

## Tests

```sh
python -m pytest -q packages/meterline-litellm                 # everything
python -m pytest -q packages/meterline-litellm -k done_check   # the acceptance checks L01 to L09
```

Tests run against a fake API built on `httpx.MockTransport` that serves synthetic data in the documented shapes and can script faults. The clock and sleep are injected, so nothing really waits.
