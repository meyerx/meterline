# meterline-copilot

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Reads GitHub Copilot usage reports, billing and seats, at the organization or the enterprise level, and emits [`meterline`](https://github.com/meyerx/meterline/blob/main/packages/meterline/README.md) records. Python 3.10 or later; runtime dependencies `meterline` and `httpx`.

**Status:** built against documented shapes ([evidence level](https://github.com/meyerx/meterline/blob/main/docs/glossary.md#evidence-levels)) (spec v2 sections 6, 7 and 8, [`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md)), with acceptance checks and unit tests on a fake GitHub API. Not yet run against a live account. See [Known limits](#known-limits).

```sh
pip install meterline-copilot
pip install "meterline-copilot[parquet]"      # optional: Parquet output
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline -e "packages/meterline-copilot[dev]"
pip install -e "packages/meterline-copilot[parquet]"      # optional: Parquet output
```

Then, with a token in the environment (see "Before you run it"):

```sh
export COPILOT_TOKEN=...                                   # never commit this
meterline-copilot backfill --level organization --slug my-org --since 2026-01-01
```

## Read this first: consumption and billed

Two kinds of cost figure come out of this connector, and they must not be mixed:

- **Per-person figures are consumption.** A person's daily credits (from the usage report) and monthly amounts per model (from billing) are what GitHub reports for that person. Included credits are pooled across the billing entity, so a person's discount is not their invoice. These records have `cost_basis` `consumption`.
- **Organization figures are billed.** Only the organization's billing per day and model (`OrgTotal`) has `cost_basis` `billed`.
- Because the two bases differ, the "an actor's cost never exceeds the organization's" check is not made for Copilot (spec 6.4 rule 11).
- Credits sit on the `copilot` usage record only. CLI and app token records (`copilot_cli`, `copilot_app`) carry tokens and requests but no credits, so credits and tokens add without counting twice.
- meterline does no identity matching: a person is a GitHub login and numeric id. Joining logins to people is the reader's job.

## Before you run it

Set three things before the first run:

- **Level.** `--level organization|enterprise` and `--slug <organization login or enterprise slug>`, required for `backfill` and `daily`. `provider_account_id` is `org:<slug>` or `enterprise:<slug>`, so the two levels never share a key.
- **Environment.** `COPILOT_TOKEN` (usage reports and seats) and `COPILOT_BILLING_TOKEN` (billing; defaults to `COPILOT_TOKEN`). Tokens are read in `main` and nowhere else; they are sent only as `Authorization: Bearer <token>` to `api.github.com`, never to a download host, and never written to disk, a log or a manifest.
- **What each level needs** (documented; not checked against a live account):

| Level | Usage reports | Seats | Billing |
|---|---|---|---|
| organization | fine-grained token or GitHub App with "Organization Copilot metrics" read; classic `read:org` | fine-grained with "GitHub Copilot Business" or "Administration" read; classic `manage_billing:copilot` or `read:org` | fine-grained or GitHub App with "Administration" read |
| enterprise | classic `manage_billing:copilot` or `read:enterprise`, or a GitHub App with "Enterprise Copilot metrics" read; not fine-grained tokens | **classic token only** (`manage_billing:copilot` or `read:enterprise`) | GitHub App token with "Enterprise billing" read (classic scopes not documented) |

  A GitHub App installation token expires after an hour; obtaining one is the operator's job, not meterline's.
- **Policy, role and plan** (documented by GitHub; not checked against a live account):
  - **Usage reports.** The "Copilot usage metrics" policy must be set to **Enabled everywhere** for the enterprise; an organisation-level policy also exists. At organization level the caller is an organization owner or has the custom-role permission "View Organization Copilot Metrics". At enterprise level the caller is an enterprise owner or a billing manager, or has "View Enterprise Copilot Metrics". GitHub's page for the reports names no Copilot plan; whether a plan is needed is unverified.
  - **Seats and the seat summary.** Only organization owners can read them, and the organization needs a Copilot Business or Copilot Enterprise subscription. What happens without one is unverified.
  - **Billing.** The role and the plan the AI-credit billing endpoints need are unverified.
  - A `403` or `404` can mean a policy that is off, a missing role or a wrong slug; GitHub's answer does not say which, and the connector cannot tell. A `404`, or a `403` that names no rate limit, stops the run with exit 3.

## Commands

All commands take `--data-dir` (default `exports/meterline-copilot`, gitignored) and `--parquet`.

| Command | What it does |
|---|---|
| `backfill --since YYYY-MM-DD` | Reads the usage reports and the organization's billing day by day from the given day to yesterday, the per-person billing of every month in the range, and the seat list once. Resumes after a failure by re-running the same command. The start is raised to 2025-10-10 and to 365 days before today if needed (a rolling year, for the usage reports); per-day and per-person billing goes back 24 calendar months, to the first day of the month 24 months before this one. |
| `daily` | Reads every closed UTC day since the last day loaded, and always re-reads the four days before today (reports finalise in about three UTC days). With no `state.json` it stops (exit 2) and asks for `--since YYYY-MM-DD`, the first day to load, rather than quietly fetching the month again; `--since` is ignored once a state file exists. Per-person billing is read for the current month and, on days 1 to 8, the previous month. The seat list (and, at organization level, the seat summary) is read once per run. Safe to run twice a day: unchanged data publishes nothing. |
| `rebuild` | Re-creates every record from the raw store and the published run manifests. No API calls, no token. |

Options of `backfill` and `daily`: `--level`, `--slug`, `--rate-per-hour` (default 600; the API allows 5,000 an hour for a user token).

The state file `state.json` is checked on every `backfill` and `daily`. If it is cut short, empty, not the shape the program writes or points outside the data directory, the run stops with exit 2 before any request and names the file. Restore it from a backup, or move it aside and run `backfill --since`: published records are kept, and unchanged rows are not written twice. `rebuild` does not read it.

Exit codes, which a run also reports as `failure_kind` (`RunResult.failure_kind` from Python; a manifest written by a run that failed a check carries it too):

| Code | `failure_kind` | Meaning | What to do |
|---|---|---|---|
| `0` | `none` | The run finished. | Nothing. |
| `1` | `transient` | A 5xx, a 429 after the retries, a timeout or a connection error. | Run the same command again later; progress is kept. |
| `2` | `input` | A failed check, a response that breaks the documented shape, bad arguments or a configuration error such as a missing token. | Fix the input; running the same command again fails the same way. |
| `3` | `permanent` | The provider refused the request: 400, 401, 403, 404 or 422. The message starts "permanent:" and `RunResult.http_status` holds the status. | Fix the key, its scope or the identifier the message names; do not retry unchanged. |

To run this connector every day, with a retry rule and a gold build after it, see [the daily job example](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/README.md). A first backfill of a large organisation takes hours at the default rate; the [estimate](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/README.md#how-long-the-first-copilot-backfill-takes) shows the arithmetic.

## How it works

1. **Fetcher** (`client.py`, `fetch.py`). Per day: the per-user report, the aggregate report and one billing call for the organization's day. Each report is an envelope of signed download links, fetched at once without any credential. A 403 or 404 from the download host restarts that day from the report call once, then fails the run; a 204 means no report. Once per run: the seat list (paged by `Link` header) and, at organization level, the seat summary. Per person and month: one billing call with `user`, for each login that appears in a stored per-user report row of that month or in a seat list pulled that month. Every response is stored as received under `raw/<endpoint>/<label>/<stamp>.json.gz`; download links are kept without their signed query string, and per-person file names carry a hash of the login.
2. **Normaliser** (`normalise.py`). Reads raw files only. Besides the run's own files it reads earlier raw files of the data directory for two rules, never a file stamped after the run. The seat snapshot of a day combines every raw file of that day, and a person's id for the monthly billing records comes from the reports and seat lists of that month. A rebuild therefore sees what the original run saw.

At 1,000 people the per-person billing is about 1,000 calls per month per pass, and each `daily` run repeats it for the current month.

## Files under the data directory

A run reads and writes these four things under `--data-dir`:

| Path | Contents |
|---|---|
| `raw/<endpoint>/<label>/<stamp>.json.gz` | One file per call or download: the per-user and aggregate reports per day, billing per day and per person and month (the folder name is a hash of the login), the seat list and the seat summary. Download links are kept without their signed query string. |
| `runs/<batch_id>.json` | The run manifest: `command`, `batch_id`, `status`, `level`, `provider_account_id`, `windows`, `pages`, `row_counts`, `totals`, `unknown_products`, `unknown_models`, `unknown_units`, `feature_sum_mismatch`, `aggregate_missing`, `notes`, `raw_files`, `published_counts`, `failures` and `unknown_fields` (the fields of the provider that the connector does not know, as endpoint, key path and count, never a value). |
| `records/<kind>/<batch_id>.jsonl` | Published records (v2); kinds are `usage`, `activity`, `seats`, `seat_assignments` and `org_totals`. A batch with no changed records writes no file. |
| `state.json` | `days_loaded`, and a `pending` block that lets an interrupted run resume. |

## Record mapping

See [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md#github-copilot-sources) for every field. In short:

| Record | From |
|---|---|
| `UsageRecord` `copilot`, per user and day | report `ai_credits_used`: `quantities` `{"ai_credit": n}`, `cost` = `cost_list` = n x 0.01 USD, `consumption`, tokens null |
| `UsageRecord` `copilot_cli`, `copilot_app` | report `totals_by_cli`, `totals_by_copilot_app`: `tokens_input_total`, `tokens_output`, `requests`; no cost |
| `UsageRecord`, `grain` `month`, per person, product and model | billing with `user`; `consumption`; `net_amount` and the discount in `extras` |
| `OrgTotal`, per day, product and model | billing without `user`; `billed`, `cost` = `netAmount` |
| `ActivityRecord`, per feature | report `totals_by_feature`; plus a `copilot` record with the distinct-use counters |
| `SeatSnapshot` | seats, seat summary (organization level) and the aggregate report's active-user counts |
| `SeatAssignment` | seats, one per person, with earliest assignment, latest activity and granting teams |

Billing `unitType` is mapped to a unit name: `credits`, `ai-credits` and `ai_credits` become `ai_credit`, `requests` becomes `premium_request`, and anything else is lower-cased and listed in the run manifest under `unknown_units`. The product is lower-cased (`Copilot` becomes `copilot`).

Records are built and checked with `meterline.records` (the five record classes, `batch_failures`, `latest_per_key`) and written and read with `meterline.writers` (`write_jsonl`, `write_parquet`, `read_jsonl`), the two submodule routes the core package `meterline` keeps stable. A script that reads this connector's output can import the same names from those routes or from the package root.

## Response shapes

[`docs/api-shapes/copilot/`](https://github.com/meyerx/meterline/blob/main/docs/api-shapes/copilot) holds one file per endpoint with synthetic values, and a README naming the documentation page of each. Done-check C01 compares what the fake API returns, through this connector's own client, with these files, by keys and nesting. It cannot show that GitHub's live API answers the same way.

**When a live response differs.** A live run is the first real test of these shapes. If a response does not match its shape file:

1. Save the live response's structure as the new shape file, with every value replaced by an invented one: synthetic values only, and never commit a real response.
2. Run the shape check, `python -m pytest -q packages/meterline-copilot -k "C01"`. C01 fails and names each key that differs between the fake API and the file.
3. Change the fake API to match, then `fetch.py` and `normalise.py`, until the shape check and the other acceptance tests pass.
4. Update the source and the date read in the shape folder's README, and add an entry to the change list of the spec.

**When GitHub releases a new API version.** Every request to `api.github.com` sends `X-GitHub-Api-Version: 2026-03-10` (`API_VERSION` in `client.py`), and a test pins it. GitHub keeps an older version available for a time after a new one ships. To move, read the version's breaking changes, update the shape files as above, then change the constant and the test together.

## Known limits

The limits of this connector, grouped by kind.

**How the connector reads GitHub.** These follow from GitHub's API and the connector's rules:

- **Raw files are kept and never replaced.** Every pull writes its own `raw/.../<pulled_at>.json.gz`; nothing in the connector deletes or rewrites one, so the evidence a record was built from stays. A second pull in the same UTC second would need the same file name. Such a run is refused (exit 2) before it asks GitHub anything, or, if it had started, stops without replacing the file; a run in the second of a finished run is refused too. Wait a second and run again. Raw files grow with every run; archive or delete old ones by hand once the records they produced are no longer needed for a `rebuild`.
- **Page loops end and lists are checked.** The seat list follows at most 10,000 pages (`MAX_PAGES` in `fetch.py`); a page that repeats the one before, or pages that hold fewer distinct people than `total_seats` says, stop the run with exit 2 and a failed manifest. A download link listed twice, or two links that give the same file, do the same. A report whose download link is simply left out cannot be seen from the client; compare the day's `aggregate_1_day` active-user count with the people in the usage report if a day looks short.
- **Waits are capped.** A `Retry-After` header is honoured up to 300 seconds (`--max-retry-after`); both the delay-seconds and the HTTP-date forms are read, and a value that is not a finite number is ignored. A wait named by `x-ratelimit-reset` is still honoured up to two hours.
- **History windows.** Usage reports start on 2025-10-10 and go back at most a year; billing goes back 24 months. `backfill` clamps and says so (manifest `notes.since_clamped_to`).
- **Rate limits.** The client throttles to `--rate-per-hour` (at most that many requests in any hour), waits out `retry-after` and `x-ratelimit-reset` on a 403 or 429, and backs off on 5xx, honouring a `retry-after` there too. Every wait before a retry is the larger of the back-off and the wait the server names, the same rule as the other connectors (`meterline.http`). At most 100 concurrent requests are allowed by GitHub; the client makes one at a time.
- **Days without a report.** A 204 is stored and writes no records for that day; a later run re-reads the day if it is inside the four-day window.
- **Finality.** Usage reports finalise in about three full UTC days, and GitHub has raised past credit figures; a day's records are `is_final` four full days after the day, and a month's from eight days after month end. `daily` re-reads four days, so a day read last on the fourth day after it is never re-read by `daily` alone; `backfill` over the period writes the final ones.
- **Per-person billing scales with people.** One call per person per month, repeated by every `daily` run for the current month.
- **A resumed run reuses the pages it already has.** After a failure, the same command picks up the pages the failed run stored (the `pending` block of `state.json`), however old they are, and publishes them under the new run's `pulled_at`. Running a different command in between drops them, and a run whose checks fail clears them, so a failed long `backfill` asks for everything again.

**What the documentation leaves open.** The connector follows GitHub's documentation here, and a live run may show otherwise:

- **Aggregate report layout.** The connector takes a `*-1-day` aggregate record to be flat: the three active-user counts sit at the top of the row (the 28-day report wraps its days in a `day_totals` array, which the normaliser also accepts). The normaliser reads the counts from the row, else from the `day_totals` entry of the requested day, else leaves them null and lists the day under `aggregate_missing` in the manifest. The fake API, the shape file `aggregate_1_day_row.ndjson` and the test `test_one_day_flat_layout_copilot` all use the flat layout. That layout is the connector's reading of GitHub's example for the 28-day report, not a one-day report seen from GitHub: no live report has been seen.
- **A price per unit** is kept in `extras` only when every merged billing item of a person, product and model has the same one.
- **One AI credit is taken as USD 0.01**, GitHub's documented fixed price. If it changes, the mapping must read `pricePerUnit` instead.
- **Availability.** GitHub announced on 2026-02-27 that Copilot metrics are generally available, and the usage-report endpoints are covered by that. The seats endpoints (`GET /orgs/{org}/copilot/billing/seats`, and the enterprise one) and the organization seat summary (`GET /orgs/{org}/copilot/billing`) are still marked preview by GitHub and may change. Whether the AI-credit billing endpoints are marked preview is unverified.

**What it does not read.** The connector reads the usage reports, AI-credit billing and seats only.

- **Not read:** premium-request billing, the general billing usage and summary endpoints, 28-day reports, repository and team reports, the AI usage report download, breakdowns by IDE, language and model (kept in `raw/`, not mapped), and the legacy `/copilot/metrics` API. The token families `totals_by_client` (per client and per model), `totals_by_vscode_agent` and `totals_by_3rd_party_agent` are not mapped either: the tokens in them are not in the records, and the run manifest sums their prompt and output tokens under `unread_token_families` so the gap is visible.

**Personal data.** Logins and numeric ids appear in raw files and records (they are data); logs and manifests carry counts, statuses and hashes only.

## From Python

```python
from datetime import date
from meterline_copilot import run

result = run("backfill", since=date(2026, 9, 1), level="organization", slug="my-org", data_dir="data",
             credentials={"COPILOT_TOKEN": "..."})  # or credentials=a_function_returning_that_mapping
print(result.exit_code, result.batch_id, result.records)
```

`run(command, *, data_dir, credentials=None, transport=None, **options)` does what the command line does. `command` is `backfill`, `daily` or `rebuild`; the options are those of the command under their Python names (`since`, `level`, `slug`, `rate_per_hour`, `max_retry_after`, `parquet`), with the same defaults, and an unknown one raises `TypeError`. `credentials` is a mapping from `COPILOT_TOKEN` (and, if it differs, `COPILOT_BILLING_TOKEN`) to its value, or a function returning one that is called once per run. When it is given, the environment is not read; when it is `None`, the environment variables are read as for the command. The `RunResult` it returns holds `exit_code`, `batch_id`, `manifest_path`, `records` (the count published per kind), `windows`, `skipped` and `unknown_fields` (the same list as the manifest's), and `ok`. A failed run does not raise: `exit_code` is the one the command line would exit with (0 ok, 1 API or transport failure, 2 checks failed or a configuration error), and the message goes to stderr as for the command.

## Testing your integration without a key

```python
from datetime import datetime, timezone
from meterline_copilot import run
from meterline_copilot.testing import SAMPLE_TOKEN, FakeAPI

fake = FakeAPI()  # four invented logins, the organization my-org, 2026-09-01 to 2026-09-14
result = run("backfill", since="2026-09-01", level="organization", slug="my-org", data_dir="data",
             credentials={"COPILOT_TOKEN": SAMPLE_TOKEN}, transport=fake.transport,
             now=lambda: datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc))
assert result.ok
```

`FakeAPI` answers on an `httpx.MockTransport`: no network, no key. Everything it serves is synthetic. It shows that your code calls `run` and reads the result correctly; it does not prove that your token is accepted by GitHub, that the live responses have these shapes, or how the live rate limits behave. See [Response shapes](#response-shapes).

## Public interface

What later releases keep working, under the rules of the compatibility policy in [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md#compatibility):

- the `meterline-copilot` command line: its commands, options and exit codes, as listed under Commands;
- the records it publishes under `records/`, in the v2 format of [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md);
- from Python, the names in `meterline_copilot.__all__`: `run`, `RunResult`, the error classes `ApiError`, `UnusableBody`, `BadShape`, `PaginationError`, `RawFileExists` and `StateError`, and `__version__`;
- the `meterline_copilot.testing` module: `FakeAPI`, `sample_dataset` and the constants `SAMPLE_TOKEN` and `SAMPLE_SLUG` (synthetic data, as described under Testing your integration without a key).

Everything else is internal and may change in any release: every other module of the `meterline_copilot` package, and the layout of the raw files, the run manifests and `state.json` (they are documented so you can read them, not so you can build on them).

## Tests

```sh
python -m pytest -q packages/meterline-copilot                 # everything
python -m pytest -q packages/meterline-copilot -k done_check   # the acceptance checks C01 to C12, each at both account levels
```

Tests run against the fake API of `meterline_copilot.testing`, built on `httpx.MockTransport` that serves synthetic data in the documented shapes and can script faults. The clock and sleep are injected, so nothing really waits.
