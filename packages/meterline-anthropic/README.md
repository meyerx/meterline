# meterline-anthropic

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Reads the Anthropic Claude Enterprise Analytics API and emits [`meterline`](https://github.com/meyerx/meterline/blob/main/packages/meterline/README.md) records. Python 3.10 or later; runtime dependencies `meterline` and `httpx`.

**Status:** built against documented shapes ([evidence level](https://github.com/meyerx/meterline/blob/main/docs/glossary.md#evidence-levels)): spec v1.1 ([`docs/dev/meterline-anthropic-SPEC-v1.1-2026-10-10.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/meterline-anthropic-SPEC-v1.1-2026-10-10.md)), with its acceptance tests run against a fake API. Since v2 phase 1 it writes the v2 record format and keeps v1's zeros (spec v2 section 4). Not yet run against a live account. See [Known limits](#known-limits).

```sh
pip install meterline-anthropic
pip install "meterline-anthropic[parquet]"      # optional: Parquet output
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline -e "packages/meterline-anthropic[dev]"
pip install -e "packages/meterline-anthropic[parquet]"      # optional: Parquet output
```

Then, with an Analytics key in the environment (the full list of things to check is under "Before you run it"):

```sh
export ANTHROPIC_ANALYTICS_KEY=...                        # never commit this
meterline-anthropic backfill --since 2026-01-01
```

## Provider status

As of 2026-10-30, from Anthropic's documentation read on 2026-10-29.

- **Four of the six endpoints are legacy.** Anthropic declared `user_usage_report`, `user_cost_report`, `usage_report` and `cost_report` legacy on 2026-10-29. They are still supported, and no sunset date is known. `users` and `summaries` are not legacy.
- **The successors.** `GET /v1/organizations/analytics/usage` and `GET /v1/organizations/analytics/cost` (beta; they need the header `anthropic-beta: analytics-usage-cost-2026-08-24`). This connector does not call them: it stays on the legacy endpoints in the current releases. Follow-up: the move to the new endpoints is tracked separately.
- **The plan.** Every endpoint of this API needs a Claude Enterprise plan. `users` and `summaries` work on all Enterprise plans. The report endpoints apply to usage-based Enterprise plans; on a seat-based Enterprise plan they show usage credits only. An organisation without an Enterprise plan cannot use this connector; the other Anthropic connector, [`meterline-anthropic-admin`](https://github.com/meyerx/meterline/blob/main/packages/meterline-anthropic-admin/README.md), reads a Console organisation.
- **The pinned version and two renames.** The connector sends `anthropic-version: 2023-06-01` (`API_VERSION` in `client.py`). From version `2026-10-21` Anthropic removed two old names: `actor.email` in the per-user reports (now `actor.email_address`) and the `summaries` list of the `summaries` answer (now `data`). The connector reads only the new names. It needs `email_address` on every row of the per-user reports and of `users`: a row without it stops the run with exit 2 and names the field. It reads the seat list from `data`. **Unverified:** whether an answer under `2023-06-01` carries the new names. Anthropic's migration text says the new version drops the old names; it does not say that the old version already has the new ones. If it does not, the first live run stops with exit 2 on the missing field, and the fix is to move the pinned version together with the shape files (see Response shapes).
- **The header of the key.** The connector sends the key as `x-api-key`, as Anthropic's guide says. Anthropic's reference examples send `Authorization: Bearer` instead. **Unverified:** which of the two this API accepts for an Analytics key.

## Before you run it

Check these four things first:

- You need an **Analytics key** with scope `read:analytics`, created by the organization's Primary Owner in claude.ai. An Admin API key is rejected by this API.
- Export it as `ANTHROPIC_ANALYTICS_KEY`. That variable is the only place the key is read.
- The limit is 60 requests per minute per organization, shared by everything using the key. The connector throttles to 50 by default (`--rate-per-minute`).
- Data starts at 2026-01-01, and `starting_at` can be at most 365 days back.

## Commands

All commands take `--data-dir` (default `exports/meterline-anthropic`, which is gitignored) and `--parquet` (also write a `.parquet` next to each published `.jsonl`). `--rate-per-minute` applies to `backfill` and `daily`; `rebuild` accepts it for symmetry and ignores it, because it makes no API calls.

| Command | What it does |
|---|---|
| `backfill --since YYYY-MM-DD` | Walks 31-day windows from the start date to today: per-user tokens and cost, organization totals, then daily activity and seat summaries from the start date. Resumes after a failure by re-running the same command. |
| `daily [--since YYYY-MM-DD]` | Tokens, cost and organization totals for the current month, plus the previous month through the 8th; activity for every day since the last one loaded; yesterday's summary. Safe to run twice a day: unchanged data publishes nothing. With no `state.json` it stops (exit 2) and asks for `--since`, the first day of activity to load, rather than quietly fetching the month again; `--since` is ignored once a state file exists. |
| `auto --since YYYY-MM-DD` | Runs `backfill --since` until a backfill has published in the data folder, and `daily` afterwards, so a scheduler can run the same line every day. A backfill that stopped half way is run again. `--since` is required and is ignored, with a note on stderr, once a backfill has published. Takes the options of `backfill`. The run manifest records the command that ran under `command` and `auto` under `requested`. |
| `rebuild` | Re-creates every record from the raw store and the published run manifests. No API calls and no key needed. |

Exit codes, which a run also reports as `failure_kind` (`RunResult.failure_kind` from Python; a manifest written by a run that failed a check carries it too):

| Code | `failure_kind` | Meaning | What to do |
|---|---|---|---|
| `0` | `none` | The run finished. | Nothing. |
| `1` | `transient` | A 5xx, a 429 after the retries, a timeout or a connection error. | Run the same command again later; progress is kept. |
| `2` | `input` | A failed check, a response that lacks a field the connector needs or holds one of the wrong type, bad arguments or a configuration error such as a missing key. | Fix the input; running the same command again fails the same way. |
| `3` | `permanent` | The provider refused the request: 400, 401, 403, 404 or 422. The message starts "permanent:" and `RunResult.http_status` holds the status. | Fix the key, its scope or the identifier the message names; do not retry unchanged. |

An activity day that is not yet available returns HTTP 400 naming the latest available day. The connector ends the activity part of the run cleanly there and picks the day up on the next run.

To run this connector every day, with a retry rule and a gold build after it, see [the daily job example](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/README.md).

## From Python

```python
from meterline_anthropic import run

key = "..."  # from your secret store
result = run("backfill", since="2026-09-01", data_dir="exports/meterline-anthropic",
             credentials={"ANTHROPIC_ANALYTICS_KEY": key})
print(result.exit_code, result.records)
```

`run(command, *, data_dir, credentials=None, transport=None, **options)` does what the command line does. `command` is `"backfill"`, `"daily"`, `"auto"` or `"rebuild"`; the options are the command-line ones under Python names (`since`, `rate_per_minute`, `max_retry_after`, `parquet`) with the same defaults, and an unknown name raises `TypeError`. `credentials` is a mapping from `ANTHROPIC_ANALYTICS_KEY` to the key, or a function that returns such a mapping; when it is given the environment is not read, and the key goes only to the HTTP client. It returns a `RunResult` (from `meterline`): `exit_code`, `ok`, `batch_id`, `manifest_path`, `records` (published count per kind), `windows`, `skipped` and `unknown_fields` (the same list as the manifest's). A failed run does not raise: it returns the exit code the command line would have exited with (`1` an API or transport failure, `2` a failed check or a configuration error) after printing the same messages to stderr.

`complete_through(data_dir)` returns the last UTC day the connector holds as final in `data_dir`, as a `datetime.date`, or `None` before a first published backfill. It reads `activity_loaded` and `summaries_loaded` in the state file. For each, the answer is the last day of the unbroken run of loaded days, from the first one, whose period is final at the pull of the latest published run (its month ended more than 7 days before). It is the earlier of the two, so it is always the last day of a month. It makes no request and takes no lock, and a damaged state file raises `StateError`.

## Testing your integration without a key

```python
from meterline_anthropic import run
from meterline_anthropic.testing import FakeAPI

fake = FakeAPI(api_key="demo")  # serves a small synthetic dataset
result = run("backfill", since="2026-09-01", data_dir="out",
             credentials={"ANTHROPIC_ANALYTICS_KEY": "demo"}, transport=fake.transport)
assert result.ok and result.records
```

`FakeAPI` is a fake of the Analytics API on an `httpx.MockTransport`. With no argument it serves `sample_dataset()`: four invented people, 2026-09-01 to 2026-09-14, in the documented response shapes. Everything in it is synthetic. It can script faults (`fail_page`, `expire_cursor_on_page`, `set_latest_activity_date`). It does not prove that a live key is accepted, that the live API still has these shapes, or how the live rate limits behave.

## How it works

Two layers:

1. **Fetcher** (`client.py`, `fetch.py`). Calls the API and writes every response page as received to `raw/<endpoint>/<window>/<pulled_at>.json.gz`, with the request URL, `data_refreshed_at` and the organization id. The key and request headers are never written.
2. **Normaliser** (`normalise.py`). Reads raw files only, never the network, and emits records. `cli.py` ties them together and holds the run state.

Behaviour worth knowing:

- **Endpoints:** per-user tokens (`user_usage_report`) and per-user cost (`user_cost_report`) are asked 1000 rows per page and grouped by model and product. Organization tokens (`usage_report`) and cost (`cost_report`) are asked 31 day buckets per page, per-user activity (`users`) one day at a time with 1000 users per page, and seats (`summaries`) once per range. Array parameters are sent as repeated `name[]=value` pairs.
- **Pagination:** the report endpoints continue while `has_more` is true and `next_page` is set. `users` has no `has_more`, so it continues while `next_page` is set. Every other parameter stays unchanged. A cursor that expires (HTTP 410) restarts the query from page 1, at most 3 times.
- **Organization id:** taken from the top level of the report responses (`organization_id`). `users` and `summaries` have none, so their records use the id of the run's report responses; when a run has no report window, the fetcher first makes one single-day `usage_report` request to learn it. No record carries an empty or `"None"` organization id.
- **Stable reads (the probe):** before each report window the connector sends one `limit=1` request without `ending_at` and reads its `data_refreshed_at`. The window is then fetched with `ending_at` set to the earlier of the window end and UTC midnight of that day, so only whole days are read. This costs one extra request per report endpoint and window (four per window); the probe is not stored as a raw file.
- **No refresh stamp:** `data_refreshed_at` may be null. A window whose probe has none, or whose cap leaves no whole day (a one-day window whose stamp falls on that same day), is skipped and listed in the run manifest under `skipped_windows` as `<endpoint> <window>: no data yet`. The run still completes. Records from `users` and `summaries` always carry a null `data_refreshed_at`.
- **Rate and retry:** a sliding one-minute window. On 429 and 5xx it backs off exponentially with jitter (1 s to 60 s, 6 tries) and honours `retry-after`. Any other 4xx stops the run.
- **Join:** usage and cost join on (actor, day, product, model). A usage row with no cost row gets `cost = null`, never 0. A cost row with no usage row is kept as a `UsageRecord` with zero tokens, `requests` null and `cost_only = true`, and listed in the manifest under `cost_only` as `<actor_id>|<day>|<product>|<model>`; it never fails the run.
- **Activity:** each `users` row yields one `ActivityRecord` per product block that has at least one non-zero count, plus one record with product `all` for `web_search_count`. The product names and field mapping are in the [record reference](https://github.com/meyerx/meterline/blob/main/docs/records.md#activityrecord).
- **Money:** the API's `amount` and `list_amount` are decimal strings in cents. They are parsed as `Decimal` and divided by 100; no float is used on the cost path.
- **Revisions:** a re-pull writes only records that changed, under a new `batch_id`. Readers take the latest batch per key.
- **Final periods:** `daily` re-pulls the previous month on days 1 to 8 inclusive. A period is `is_final` when its month ended more than 7 days before the pull.
- **v2 mapping:** `tokens_input` is now `tokens_input_uncached`, with `tokens_cache_write` and `tokens_input_total` derived; `web_search_requests` is `extras["web_search_requests"]`; the five activity counters with no core field (`conversations`, `artifacts`, `skills_used`, `connectors_used`, `web_searches`) are `counters` entries; `cost_basis` is `consumption` when a cost is set. The full table is in section 4 of the v2 spec and in the [record reference](https://github.com/meyerx/meterline/blob/main/docs/records.md). Records written before v2 convert with `meterline.convert_v1_batch`.
- **Checks before publishing:** the v2 record rules (grain, token sums, cost basis, no future dates, no negative numbers), no duplicate keys in a batch, record totals equal raw totals (cost-only records included), and per-user cost per day not above the organization total. A failed check publishes nothing, writes a manifest with `status: "failed"` and the failures, and exits `2`.
- **New products and models:** there is no fixed list of known products or models. Every value is kept as received, and each run's manifest lists, under `unknown_products` and `unknown_models`, the values that no earlier published batch contained. On a first run that is every value.
- **Never grouped by `rbac_group_id`:** a user in two groups would be counted twice.
- **Core imports:** records are built and checked with `meterline.records` (`UsageRecord`, `ActivityRecord`, `SeatSnapshot`, `OrgTotal`, `batch_failures`, `latest_per_key`) and written and read with `meterline.writers` (`write_jsonl`, `write_parquet`, `read_jsonl`), the two submodule routes the core package keeps stable. A script that reads this connector's output can import the same names from those routes or from the package root, `meterline`.

## Files under the data directory

A run reads and writes these four things under `--data-dir`:

| Path | Contents |
|---|---|
| `raw/<endpoint>/<window>/<pulled_at>.json.gz` | One file per window pull: endpoint, window, request URL, `pulled_at`, `data_refreshed_at` (null when no page had one), `organization_id`, and every page's status and body text. |
| `runs/<batch_id>.json` | The run manifest: `command`, `batch_id`, `pulled_at`, `status`, `windows`, `pages`, `row_counts`, `totals`, `unknown_products`, `unknown_models`, `skipped_windows`, `cost_only`, `raw_files`, `published_counts`, `failures` and `unknown_fields` (the fields of the provider that the connector does not know, as endpoint, key path and count, never a value). `batch_id` is the run's `pulled_at` as `YYYYMMDDTHHMMSSZ`. |
| `records/<kind>/<batch_id>.jsonl` | Published records (v2); kinds are `usage`, `activity`, `seats`, `org_totals` (this connector writes no `seat_assignments`). A batch with no changed records writes no file. |
| `state.json` | `activity_loaded` and `summaries_loaded` (days already loaded), `refreshed_at` (last `data_refreshed_at` per endpoint), `latest_activity_date`, and a `pending` block that lets an interrupted run resume. |

The state file is checked on every `backfill` and `daily`. If it is cut short, empty, not the shape above or points outside the data directory, the run stops with exit 2 before any request and names the file. Restore it from a backup, or move it aside and run `backfill --since`: published records are kept, and unchanged rows are not written twice. `rebuild` does not read it.

Raw files and published records contain email addresses (records trimmed and lower-cased). Treat the data directory as sensitive and keep it out of git.

## Record mapping

Each endpoint feeds one record kind. The [record reference](https://github.com/meyerx/meterline/blob/main/docs/records.md) gives the Anthropic source of every field, column by column; in short:

| Record | From |
|---|---|
| `UsageRecord`, `actor_kind` `user`, `grain` `day`, per user, product and model | `user_usage_report`, joined to `user_cost_report` on (user, day, product, model) |
| `OrgTotal`, `grain` `day`, one all-models row per day | `usage_report` and `cost_report` day buckets, summed |
| `ActivityRecord`, per user, day and product | `users`, one record per product block with a non-zero count, plus product `all` for web searches |
| `SeatSnapshot`, per day | `summaries` |

This connector writes no `SeatAssignment`. The changes from the v1 format are listed under How it works.

## Response shapes

The six files at the top of [`docs/api-shapes/`](https://github.com/meyerx/meterline/blob/main/docs/api-shapes) are the documented response shapes, one per endpoint, with synthetic values: `user_usage_report.json`, `user_cost_report.json`, `usage_report.json`, `cost_report.json`, `users.json` and `summaries.json`. They were transcribed from Anthropic's API reference (section 2.1 of the spec); [`docs/api-shapes/README.md`](https://github.com/meyerx/meterline/blob/main/docs/api-shapes/README.md) gives the source and the date read. Done-check 12 compares what the fake API returns with these files, structurally and ignoring values, so the fake cannot drift from the transcribed shapes. It cannot show that the shapes match what the live API sends.

**When a live response differs.** A live run is the first real test of these shapes. If a response does not match its shape file:

1. Save the live response's structure as the new shape file, with every value replaced by an invented one: synthetic values only, and never commit a real response.
2. Run the shape check, `python -m pytest -q packages/meterline-anthropic -k "check_12"`. Done-check 12 fails and names each key that differs between the fake API and the file.
3. Change the fake API to match, then `fetch.py` and `normalise.py`, until the shape check and the other acceptance tests pass.
4. Update the source and the date read in the shape folder's README, and add an entry to the change list of the spec.

**When Anthropic releases a new API version.** Every request sends `anthropic-version: 2023-06-01` (`API_VERSION` in `client.py`), and a unit test pins it. To move to a new API version, read its changes, update the shape files as above, then change the constant and the test together.

## Known limits

The limits of this connector, grouped by kind.

**How the connector reads the API.** These follow from the API's own limits and the connector's rules:

- **Raw files are kept and never replaced.** Every pull writes its own `raw/.../<pulled_at>.json.gz`; nothing in the connector deletes or rewrites one, so the evidence a record was built from stays. A second pull in the same UTC second would need the same file name. Such a run is refused (exit 2) before it asks the API anything, or, if it had started, stops without replacing the file; a run in the second of a finished run is refused too. Wait a second and run again. Raw files grow with every run; archive or delete old ones by hand once the records they produced are no longer needed for a `rebuild`.
- **Page loops end.** One query follows at most 10,000 pages (`MAX_PAGES` in `client.py`). A cursor that comes back, or more pages than that, stops the run with exit 2 and a failed manifest instead of requesting for ever. A page the API skips cannot be seen from the client (the cursor is opaque and the answer has no total); the only trace is a rise in the manifest's `cost_only` list.
- **Waits are capped.** A `Retry-After` header is honoured up to 300 seconds (`--max-retry-after`); both the delay-seconds and the HTTP-date forms are read, and a value that is not a finite number is ignored in favour of the back-off.
- **Seat summaries are cut into ranges.** The API accepts at most 366 days per call (spec section 2.1), so a longer range is asked in several calls.
- **A resumed run reuses the pages it already has.** After a failure, the same command picks up the pages the failed run stored (the `pending` block of `state.json`), however old they are, and publishes them under the new run's `pulled_at`. Running a different command in between drops them, and a run whose checks fail clears them, so a failed long `backfill` asks for everything again.

**Not verified.** The connector has not met a live organization; [Response shapes](#response-shapes) says what to do when a live response differs.

**Not in the API.** Claude Code used through Amazon Bedrock, seat fees and the effort level are not reported by this API, so no record holds them.

**Out of scope.** Identity resolution, pricing from tokens, contract discounts, seat-fee allocation, currency conversion, alerting and consumer-specific table layouts are left to other tools; see the root [Known limits](https://github.com/meyerx/meterline/blob/main/README.md#known-limits).

## Public interface

What later releases keep working, under the rules of the compatibility policy in [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md#compatibility):

- the `meterline-anthropic` command line: its commands, options and exit codes, as listed under Commands;
- the records it publishes under `records/`, in the v2 format of [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md);
- from Python, the names in `meterline_anthropic.__all__`: `run`, `complete_through` and the `RunResult` it returns, the error classes `ApiError`, `StateError` and `RawFileExists`, and `__version__`;
- the `meterline_anthropic.testing` module: `FakeAPI` and `sample_dataset()`, as described under Testing your integration without a key.

Everything else is internal and may change in any release: every other module of the `meterline_anthropic` package, and the layout of the raw files, the run manifests and `state.json` (they are documented so you can read them, not so you can build on them).

## Tests

```sh
python -m pytest -q packages/meterline-anthropic                 # everything
python -m pytest -q packages/meterline-anthropic -k done_check   # the 17 v1.1 acceptance tests and A01-A03
```

Tests run against `meterline_anthropic.testing.FakeAPI`, an `httpx.MockTransport` that replays the large synthetic fixtures (`tests/fixtures/data`) in the documented shapes and can script faults (429 with `retry-after`, 410 cursor expiry, a 400 for an unavailable date, 5xx). The clock and sleep are injected, so nothing really waits. Fixture notes are in [`tests/fixtures/README.md`](https://github.com/meyerx/meterline/blob/main/packages/meterline-anthropic/tests/fixtures/README.md).
