# Connector contract

This page lists everything a meterline connector must do. A connector is a package under `packages/` that reads one provider's usage API and writes the [five record kinds](../records.md). Every rule below has an identifier, `R01` to `R19`, and a check of the same name in the conformance kit, `meterline.conformance`. A connector that passes the kit keeps the contract; a rule the kit cannot test is listed at the end with who checks it. Some sections after the rules state a rule in prose: the account label, windows, raw file names, shape files and the shape check. Each of them ends with a line that starts `Held by:` and names the kit check or the test that holds the rule, or says that the pull request does.

Read this page with the [add-a-provider guide](../how-to/add-a-provider.md), which says in what order to build the parts. The four existing connectors show the rules working: `meterline-litellm` is the smallest.

## Run the kit

A connector describes itself once, as a `Connector`, and runs the kit in one test file. The file is the same for every connector except the facts in `Connector`:

```python
from datetime import date

import pytest

from meterline.conformance import RULES, Connector, check
from meterline_litellm import run
from meterline_litellm.testing import PATH_A, FakeAPI

def _duplicate(fake):
    fake.page_cap = 20  # a report of two pages ...
    fake.replay(PATH_A)  # ... whose second page repeats the rows of the first


CONNECTOR = Connector(
    name="meterline-litellm",
    run=run,
    make_fake=FakeAPI,
    credentials=lambda fake: {"LITELLM_API_KEY": fake.api_key, "LITELLM_BASE_URL": "https://proxy.example.com"},
    backfill_options={"since": date(2026, 9, 1), "proxy_name": "proxy-one"},
    daily_options={"proxy_name": "proxy-one"},
    fail=lambda fake, status, times: fake.fail(PATH_A, status, times=times),
    bad_options={"rate_per_minute": 0},
    duplicate=_duplicate,
)


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.id)
def test_the_connector_keeps_the_contract(rule, tmp_path):
    check(rule, CONNECTOR, tmp_path)
```

The fields of `Connector` are:

- `run`: the connector's `run(command, *, data_dir, credentials, transport, ...)`, described under [the `run()` signature](#the-run-signature-and-the-credentials-rule).
- `make_fake`: builds a fresh public fake API with no fault scripted.
- `credentials`: the key variables and the values the fake accepts, or a function of the fake that returns them. A name that contains `KEY`, `TOKEN`, `SECRET` or `PASSWORD` is a secret for R13.
- `backfill_options` and `daily_options`: the options of those commands, such as `since`.
- `fail(fake, status, times)`: scripts the fake to answer `status` to the next `times` requests, whatever they ask for.
- `bad_options`: options the connector must reject as input (exit 2).
- `duplicate(fake)`: scripts the fake so that one answer holds a row, a page or a day twice, so that two records of the run would share a key (R16). Leave it out and R16 fails.
- `complete_through`: the connector's `complete_through` function, and `is_final(day, pulled_at)`: the finality rule its fake's days follow, which is the connector's own `is_final` (R19). Leave either out and R19 fails.

A failing check raises `ConformanceError`. Its message starts with the rule, its title and the anchor on this page, then says what was seen: `contract rule R10 (failure and resume): state.json lists loaded days under ['days_loaded'] although the run published nothing`. The kit needs `httpx`, which every connector already depends on (the core package lists it under the `conformance` extra). It uses no network, no key and no real clock: a fake clock whose `sleep` advances it stands in for time.

## The rules

The table lists each rule and what the kit does to check it. The sections after it give the reason and the details.

| Rule | What it requires | What the kit does |
|---|---|---|
| [R01](#r01) | The data directory layout and the run manifest keys. | Runs a backfill and lists the directory, the records folders and the manifest. |
| [R02](#r02) | Records validate; money and quantities are strings. | Validates every published file and refuses any JSON float. |
| [R03](#r03) | The `RunResult` is the run manifest. | Compares the result with the manifest and the record files. |
| [R04](#r04) | `state.json` has a shape, and a damaged one stops the run before any request. | Truncates the file, runs `daily` and counts requests. |
| [R05](#r05) | Files are `0600` and folders `0700` under any umask. | Runs under umask `022` and reads every mode. |
| [R06](#r06) | Raw files are kept and never overwritten. | Hashes the raw files before and after a later run. |
| [R07](#r07) | A run in the second of a finished run is refused. | Runs twice with the same clock. |
| [R08](#r08) | The revision rule: publish only what changed. | Runs `daily` twice on unchanged data and looks for repeats. |
| [R09](#r09) | `rebuild` needs no key and no network and reproduces the records. | Deletes `records/`, rebuilds with no key and a failing transport, and compares bytes. |
| [R10](#r10) | Failure and resume. | Cuts the API off half way, checks state, then re-runs and compares records. |
| [R11](#r11) | Exit codes 0, 1, 2, 3 and `failure_kind`. | Scripts 400, 401, 403, 404, 422, 500 and 503, a timeout, bad options and no key. |
| [R12](#r12) | The injected clock and sleep are the only waiting. | Scripts one 429 and checks the wait went through the injected `sleep`. |
| [R13](#r13) | The key never reaches a file or a message. | Searches every file, raw files decompressed, and the messages. |
| [R14](#r14) | The public fake API offers a transport, a request log and fault scripting. | Reads the log after a run and after a scripted fault. |
| [R15](#r15) | Raw and API JSON is read with `parse_float=Decimal`, so `rebuild` gives the live run's records byte for byte. | Backfills, rebuilds, and fails on any float that `json.loads` or `json.load` returns. |
| [R16](#r16) | The batch checks run before anything is published. | Scripts a repeated row, page or day with `Connector.duplicate` and requires exit 2 and no record file. |
| [R17](#r17) | One run per data folder: a run that finds the folder's lock held stops at once. | Holds the lock from a second open file, runs `daily`, and requires exit 1, no request and a folder tree that is unchanged. |
| [R18](#r18) | `auto` runs `backfill` until a backfill has published, `daily` afterwards. | Runs `auto` in an empty folder, after a published backfill, after a backfill cut off half way and after a published `daily` alone, and without `since`; requires `backfill`, `daily`, `backfill`, `backfill` and exit 2, with `requested` `auto` in each manifest. |
| [R19](#r19) | `complete_through` is the last day loaded as final, `None` before a first published backfill. | Calls it before any run and after a backfill cut off half way (`None`), then after a backfill and after a `daily` 40 days later; requires the last day the fake marks final, never a day after the last one served. |

### R01

**Data directory layout and run manifest keys.** A run writes under `--data-dir` and nowhere else. The directory holds four things:

- `raw/<endpoint>/<window>/<batch_id>.json.gz`: each response as received (the exact path below `raw/` is the connector's own).
- `records/<kind>/<batch_id>.jsonl`: the records a run published, one folder per record kind that run published, where the kind is one of `usage`, `activity`, `seats`, `seat_assignments` and `org_totals`.
- `runs/<batch_id>.json`: the run manifest. The folder also holds `.lock`, the lock file of R17, which is not a manifest.
- `state.json`: what is loaded and what is in progress (R04).

The batch id is the run's `pulled_at` in UTC as `YYYYMMDDTHHMMSSZ`. The run manifest is a JSON object with at least these keys, which `RunResult` reads (R03):

- `batch_id`, `command`, `status` (`published` or `failed`), `pulled_at` and `windows`;
- `published_counts` (records published per kind), `raw_files` and `failures`;
- `failure_kind`: `none` for a published run and `input` for a failed one (R11);
- `requested`, optional: `auto` when `auto` chose the command, else `null`. `command` is always the command that ran (`backfill` or `daily`). A manifest written before `auto` existed has no `requested`, and `rebuild` reads it as `null`.

A run that fails on an API error writes no manifest: its progress is kept in `state.json` and the raw files already fetched.

### R02

**Records validate; money and quantities are strings.** Every published file passes `meterline.validate_batch` for its kind. A cost, a price and any other quantity that carries a fraction is a decimal string, never a JSON float: a float cannot represent `0.1` and a sum of floats drifts. The kit parses every line with a hook that refuses any number written with a fraction or an exponent. Build a record from `Decimal`, and write it with `meterline.write_jsonl`, which sorts lines by record key and keys alphabetically. A measure the provider does not report is `null`, never `0` ([decision 0004](../decisions/0004-null-means-not-reported.md)).

### R03

**The `RunResult` is the run manifest.** `RunResult.batch_id`, `manifest_path`, `records` (from `published_counts`), `windows`, `skipped` and `unknown_fields` come from the manifest the run wrote, found by comparing the folder before and after the run (`meterline.run_manifests` and `meterline.result_of_run`). The kit checks that the result, the manifest and the record files agree, line for line. A run that wrote no manifest reports none; it never reports the manifest of an earlier run. A field of the provider that the normaliser does not know is not an error: it is counted with `meterline.run.note_unknown_fields`, listed in the manifest under `unknown_fields` (endpoint, key path and count, never a value), warned about once on stderr with `warn_unknown_fields`, and the run publishes as usual; a required field that is missing or of the wrong type still fails the run.

### R04

**`state.json`: shape, and a damaged one is exit 2 before any request.** The state file is a JSON object with a `pending` key: `null` after a finished run, and a block naming the run in progress after a failed one. Keys whose name ends in `loaded` (such as `days_loaded`) list what is loaded; the rest of the shape is the connector's own. The semantics are:

- State is written after the records, never before. A key ending in `loaded` is empty after a run that published nothing.
- `daily` with no state file stops with exit 2 and asks for `--since`; it never fetches a month again quietly. With a state file, `--since` is ignored and the run says so.
- A state file that is cut short, empty, not the shape the program writes or points outside the data directory stops the run with exit 2 before any request. The message names the file, and the file is left as it was.

### R05

**Files `0600` and folders `0700` under any umask.** The data holds per-person usage and emails. `run()` sets the process umask to `0o077` for the duration of the run and puts the old one back, so the result does not depend on the caller's umask. The kit runs under `022` and reads every mode.

### R06

**Raw files are kept and never overwritten.** A raw file is the evidence a record was built from, and `rebuild` reads nothing else. Name each raw file after the batch id and create it exclusively (mode `x`), so a collision is an error and not a silent replacement. Nothing in the connector deletes or rewrites a raw file. A later run adds raw files of its own.

### R07

**A run in the second of a finished run is refused.** Two runs in one UTC second would share a batch id, so their manifests, records and raw files would overwrite or mix. A run whose batch id already has a manifest stops with exit 2 before any request and changes nothing. Waiting a second and running again works.

### R08

**The revision rule: publish only records whose content differs.** A provider revises recent days, so a connector reads the last few closed days again on every `daily`. It publishes a record only when its content differs from the latest published record with the same key, ignoring `pulled_at`, `data_refreshed_at` and `batch_id`. A re-read day that did not change adds nothing. The kit runs `daily` twice on unchanged data and fails on any record published twice with the same content. The key of a record is `record.key()`; `meterline.latest_per_key` finds the latest published record per key.

### R09

**`rebuild` needs no key and no network and reproduces the records.** `rebuild` re-creates `records/` from the raw files and the published run manifests, in manifest order, applying the revision rule as it goes. It reads no environment variable and makes no request. The rebuilt files are byte-identical to the ones the runs wrote: `pulled_at` comes from the manifest, whole seconds only.

### R10

**Failure and resume.** An API failure half way through a run stops it with exit 1 (or 3, R11), keeps the raw files fetched so far, writes `state.json` with a `pending` block and publishes nothing. Running the same command again finishes the job, and the records are the same as an uninterrupted run's. The kit cuts the API off after half the requests of a clean backfill, checks the exit code and the state, runs again, and compares the records with a clean run's.

### R11

**Exit codes 0, 1, 2, 3 and `failure_kind`.** Every connector exits with one of four codes, and `RunResult.failure_kind` names the same fact:

| Code | `failure_kind` | Meaning | What to do |
|---|---|---|---|
| `0` | `none` | The run finished. | Nothing. |
| `1` | `transient` | A 5xx, a 429 after the retries, a timeout or a connection error. | Run the same command again later. |
| `2` | `input` | Bad arguments or options, no key, a damaged state file, a record check that failed, a response that breaks the documented shape. | Fix the input; running the same command again fails the same way. |
| `3` | `permanent` | The provider refused the request: 400, 401, 403, 404, 422 (any non-retryable 4xx). | Fix the key, its scope or the identifier the message names; do not retry unchanged. |

`RunResult.http_status` holds the status that stopped the run, when one did. The message on stderr starts with `permanent:` or `transient:` and says what to fix. Use `meterline.run.exit_code_of_status(status)` to choose the code, and call `meterline.note_failure(status)` so the result can name the status: an API failure writes no manifest, so that call is the only way the status reaches `RunResult`. A permanent refusal is not retried; a transient failure is retried by `meterline.http` first (see [the HTTP rules](#the-http-rules)).

### R12

**The injected clock and sleep are the only waiting.** `run()` takes `clock`, `sleep`, `wall`, `jitter` and `now`, and every wait goes through them: the request throttle, the back-off before a retry and the wait a `Retry-After` header asks for. A test then never waits on the real time. The kit scripts one 429 and requires that the retry slept through the injected `sleep` and that no time passed that `sleep` did not account for.

### R13

**The key never reaches a file or a message.** The key goes in the `Authorization` header and nowhere else. It is not written to a raw file, a record, a manifest, the state file, a log line or an error message; an error body that echoes it is masked with `meterline.http.mask`. The kit searches every file under the data directory, raw files decompressed, and the messages of a good run and a refused one.

### R14

**The public fake API.** Each connector ships `<package>.testing.FakeAPI`, so an integrator can test without a key. The fake serves synthetic data in the documented response shapes ([`docs/api-shapes/`](../api-shapes/README.md)) on an `httpx.MockTransport`. It must offer:

- a `transport` to pass to `run()`, and the key it accepts (a request with another key gets a 401);
- a `log` list with one entry per request: the injected clock's `time`, `status`, `params` and the `path` (or `endpoint`);
- fault scripting per request: a status, how many times, and the `Retry-After` header; the kit calls it through `Connector.fail`;
- a way to cap the page size, so a test can force paging (`page_cap`, `page_limit` or the like);
- a `clock` argument that stamps the log, so the log is reproducible.

### R15

**Rebuild keeps Decimal.** A provider may send an amount as a JSON number, such as `"cost": 0.1`. Python reads that as a float, and a float cannot hold `0.1` or a sum of amounts without losing digits. So raw files and API answers are read with `json.loads(text, parse_float=Decimal)`, in one helper that both the live run and `rebuild` use. The format has one decimal scale, `meterline.MAX_DECIMALS` (12), and `validate()` refuses a figure with more digits. A connector does not add a second rounding rule: a provider that sends more digits than the scale rounds once, in its normaliser, at that scale, as `meterline-litellm` does. The kit runs a backfill and then a rebuild (with `records/` removed first, no key and a transport that times out) while `json.loads` and `json.load` are wrapped. If either returns a Python `float` anywhere in its result, the rule fails and says whether it happened in the backfill or the rebuild. Then the records after the rebuild must be the same files with the same bytes as the live run's.

### R16

**Batch checks run before publish.** The connector calls `meterline.batch_failures` on the records of every kind before it writes any file. Any message stops the run with exit 2 (`failure_kind` `input`), writes a failed run manifest that lists the messages under `failures`, prints them on stderr and publishes nothing. One of the messages is `N duplicate record keys in the batch`, which is how the format keeps one record per key. An earlier check of the connector's own may refuse the answer first, such as one for a replayed page, report or date. R16 does not replace those checks and accepts either: what it requires is exit 2 and nothing published. The kit needs a way to make a key repeat, so a connector sets `Connector.duplicate`: a function that scripts the fake so that one answer holds a row, a page or a day twice. A connector whose records are built by summing rows per key cannot repeat a key from a repeated row, so it scripts a replayed page or day instead. The kit runs a backfill on that fake and requires exit 2, `failure_kind` `input`, and no record file under `records/`. If `duplicate` is not set, R16 fails and says so.

### R17

**One run per data folder.** Two runs on one data folder at the same time would write the same state file and could publish the same days twice. `run()` therefore takes the lock of the folder before it reads or writes anything, with `meterline.run.data_folder_lock(data_dir)`: an exclusive, non-blocking `fcntl.flock` on `<data_dir>/runs/.lock`, made `0600` in a `0700` folder (R05). The lock sits inside `runs/` so the data folder still holds exactly `raw`, `records`, `runs` and `state.json` (R01). The operating system releases it when the process ends, however it ends. A run that finds the lock held stops at once with exit 1, `failure_kind` `transient`, the message `data folder is in use by another run` on stderr, makes no request and writes nothing. `rebuild` takes the lock too; the silver copy of `meterline-job` and the export scan ignore the lock file. Where `fcntl` does not exist the lock is skipped with one warning, and the kit then has nothing to prove. The kit runs a backfill, holds the lock from a second open file in the same process, runs `daily`, and requires exit 1, no request, and the same folder tree and file contents as before (the lock file aside). A network file system is not tested: `flock` there depends on the file system.

Held by: R17 of the kit.

### R18

**`auto`: backfill until a backfill has published, daily afterwards.** A scheduler runs the same line every day, so the choice between `backfill` and `daily` belongs to the connector, not to each job. `run("auto", since=...)` asks `meterline.run.auto_command(data_dir)` under the folder's lock (R17). It runs `backfill` while no file under `runs/` has `"command": "backfill"` and `"status": "published"`, and `daily` afterwards. `state.json` is not the signal: a backfill that stopped half way writes one, and the next `auto` must resume that backfill. A published `daily` is not a backfill either. `since` is required, and without it the run exits 2 (`input`) before any request and writes nothing; on the daily path it is ignored and the run says so on stderr. The manifest's `command` is the command that ran and its `requested` is `auto`, and `rebuild` reads such a manifest as it reads one without `requested` (R09). The kit runs `auto` in an empty folder (`backfill`), again a day later (`daily`, with the note on stderr), after a backfill cut off half way (`backfill`), after a published `daily --since` in an empty folder (`backfill`) and without `since` (exit 2, no request), then `rebuild` over the manifests `auto` wrote. A planted `auto_command` that reads `state.json`, or that counts any published manifest as a backfill, fails it.

Held by: R18 of the kit.

### R19

**`complete_through`: the last day loaded as final.** A job that freezes a month must know which days can no longer change. `complete_through(data_dir)` answers from the state file and the connector's own `is_final` at the pull of the latest published run, as described under [the `run()` signature](#the-run-signature-and-the-credentials-rule). The answer is `None` before a first published backfill, also after a backfill that stopped half way. The kit calls it on an unused folder and after a backfill cut off half way (`None` both times). It then runs a backfill from `since` and a `daily` 40 days later, so that a whole month of the fake's days is final. After each it requires the last day from `since` that `Connector.is_final` marks final at the pull, and never a day after the last one the fake served (the day before the pull). A planted `complete_through` that returns the last day loaded, final or not, fails it.

A day counts as final only when every kind of record the connector publishes holds it as final, so `Connector.is_final` is the strictest of its rules: a connector whose billing totals close with the month, such as `meterline-copilot`, returns a day of that month only once the month is final. After the backfill the kit also requires that no record the backfill published with `is_final` false ends on or before the answer, which does not depend on `Connector.is_final`.

Held by: R19 of the kit.

## The `run()` signature and the credentials rule

Each connector exports `run(command, *, data_dir, credentials=None, transport=None, clock=..., sleep=..., now=None, jitter=..., wall=..., **options) -> RunResult` and lists it in `__all__`. The command line `main()` is a thin wrapper over it.

- `command` is `backfill`, `daily`, `auto` or `rebuild`. An unknown command raises `ValueError`; an unknown option or a missing required one raises `TypeError`.
- `auto` runs `backfill` until a backfill has published in the data folder, and `daily` afterwards. The choice is `meterline.run.auto_command(data_dir)`, made under the folder's lock (R17): `backfill` while no file under `runs/` has `"command": "backfill"` and `"status": "published"`, `daily` afterwards. A `state.json` is not enough, because a backfill that stopped half way leaves one, and the next `auto` resumes that backfill. `auto` takes the options of `backfill`. `since` is required, and without it the run exits 2 (`input`) before anything is done; on the daily path it is ignored and the run says so on stderr. The run manifest's `command` is the command that ran and its `requested` is `auto`. A connector calls `meterline.run.run_auto(data_dir, options, run_one)`, where `run_one(command, options)` runs `backfill` or `daily` as it always does.
- `credentials` maps the key variable names to their values, or is a function without arguments that returns such a mapping. When it is given, the process environment is not read for those names, and a name left out counts as unset. When it is `None`, the environment is read, as the command line does.
- `transport` is an `httpx` transport; `None` uses the network.
- A failed run does not raise: it prints the same message as the command line and returns the exit code in `RunResult`.

Each connector also exports `complete_through(data_dir) -> datetime.date | None` and lists it in `__all__`: the last UTC day it holds as final in that data folder. It reads the days its state file lists as loaded and applies its own `is_final` at the `pulled_at` of the latest published run. The answer is the last day of the unbroken run of loaded days, from the first one, that is final then; `meterline.run.final_through(data_dir, loaded, is_final)` computes it. It is `None` before a first published backfill, and it never names a day the connector has not loaded. A connector that keeps more than one list of loaded days returns the earlier answer: `meterline-anthropic` keeps activity and summaries. It makes no request and takes no lock. A job that freezes a month reads it (R19).

## The account label when a response has none

Every record names the account its figures belong to, in `provider_account_id`. Some providers put that name in each response. Others do not, and a connector must not guess it.

**The rule.** When a response does not name the account, the connector takes a label from a required option, `--provider-account-id` (Python `provider_account_id=`). The label is never empty: an empty or blank one stops the run with exit 2 before any request. Two accounts never share a label, even across the levels of one provider. An organisation and an enterprise with the same slug are two accounts, so the label says which (`org:acme`, `enterprise:acme`). The label is stored with the run, in the run manifest, so `rebuild` needs no option (R09). Whoever runs the connector chooses it. It is a name: not a secret and not a person.

**Default when the provider documents nothing.** Take the option. Do not read the label from a response field you saw once in a sample.

**Where the existing connectors stand.** This was read from the code:

- `meterline-anthropic` takes no option. It reads `organization_id` from the top of an analytics answer. If the user or summary pull needs it before any report has been pulled, one extra request for a single day of `usage_report` reads it, and that answer is not kept. The id is kept in each raw file, and the normaliser refuses a raw file without one. The run manifest does not hold it.
- `meterline-anthropic-admin` takes `--provider-account-id`, required, and a blank one is exit 2. The label is kept in each raw file with the workspace filter, not in the run manifest. `rebuild` reads it from the raw files and refuses raw files of two labels in one run.
- `meterline-litellm` takes `--proxy-name` (Python `proxy_name=`), required, and a blank one is exit 2. It becomes `provider_account_id`. It is kept in each raw file, not in the run manifest. This is the older name of the option; a new connector uses `--provider-account-id`.
- `meterline-copilot` has no free label. `--level` (`organization` or `enterprise`) and `--slug` make it: `org:<slug>` or `enterprise:<slug>`. A slug that is empty, or has characters a login cannot have, is exit 2. The label is kept in the run manifest, in each raw file and in `state.json`, and a data directory of another label is refused.
- The connector template (`tools/new_connector.py`) takes `--provider-account-id`, required and not empty, and writes it into the run manifest. Its `rebuild` reads it from there.

Held by: `R09`, which runs `rebuild` with no option, so the label has to be stored. `tools/test_new_connector.py::test_the_provider_account_is_a_required_option_kept_in_the_manifest` holds the empty label, the missing label and the manifest for the template. `packages/meterline-copilot/tests/test_copilot_units.py::test_provider_account_id_differs_between_levels`, `packages/meterline-copilot/tests/test_copilot_units.py::test_a_data_dir_of_another_level_is_refused` and `packages/meterline-copilot/tests/test_copilot_units.py::test_missing_token_or_bad_slug_is_a_configuration_error` hold the levels and the slug for `meterline-copilot`. No test in `meterline-anthropic`, `meterline-anthropic-admin` or `meterline-litellm` holds the empty label: it is reviewed in the pull request.

## The HTTP rules

Connectors share `meterline.http` and `meterline.run`; do not write your own. Each connector keeps its own HTTP client and hands the shared module status codes, header values and its injected clocks. The behaviour is the same for every connector:

- **Throttle.** At most `rate` requests in any closed window of `window` seconds (`http.Throttle`). A request exactly `window` seconds old still counts.
- **Back-off.** Before retry `n` (0-based) wait 1, 2, 4 seconds and so on, capped at 60, scaled to 50 to 100 percent by the jitter, never below 1.
- **`Retry-After`.** Read as delay-seconds or an HTTP-date on every retryable status. Absent, garbage or not finite means the server named no wait; a past date or a number at or below 0 means 0; never more than the cap, 300 seconds unless configured. The wait before a retry is the larger of the back-off and the wait the server named.
- **Retryable.** 429 and every 5xx; a connector may add its own cases, and says so in its README.
- **Masking.** `http.mask` replaces every secret in a string, a dict key and a nested item with `***`, and `http.without_userinfo` removes a user name and password from a URL.

## Windows

A connector reads closed UTC days only: never today. The window rules are:

- **Last closed day.** `backfill` runs from `--since` to yesterday; `daily` from the day after the last day loaded to yesterday.
- **Re-read days.** `daily` also reads again the last days the provider may still revise: two for `meterline-litellm`, three for `meterline-anthropic-admin`, four for `meterline-copilot`; `meterline-anthropic` reads the current month and the previous one through the 8th. The revision rule (R08) keeps the unchanged ones out of the records.
- **Window size.** A window is as large as the API allows in one query: `meterline-litellm` uses at most 31 days, and the others page by the API's own limit. State the size in the README.
- **`--since` only without state.** With a state file, `--since` is ignored and the run says so (R04).

The three rules below say how to choose the numbers for a new connector.

**Finality.** `is_final` says that a record can no longer change. Take it from what the provider documents. If the provider says a day's figures settle N days after the day ended, a day is final when the pull is at least N days after midnight UTC at the end of that day. If it documents a lag for a month, make the record's period the month and wait that long after the month ended, as `meterline-anthropic` does. A record that is not final is read again by the next `daily` and replaced only if its content changed (R08).

**Re-read depth.** A day becomes final N days after midnight UTC at its end, and `daily` reads from `today - depth` to yesterday. So the oldest day read is final in that run only when the depth is at least N + 1. A depth of N leaves that day one day short: its record is read for the last time while `is_final` is still false. A day with no rows is not marked loaded until it is final, because the provider may still fill it in.

**Window size.** Ask for as many days at once as the API allows in one query, and say the size in the README.

**Default when the provider documents nothing.** Use these numbers and label them conservative in the README: a day is final 4 days after it ended, `daily` re-reads the last 5 closed days, and a query asks for one day. The 4 is the longest wait of any day-based connector below (the others use 2). The extra day is the N + 1 above. A longer wait costs only extra reads, because R08 keeps unchanged records out; a shorter one risks calling a day final that the provider then revises. One day per query is the smallest window, so no limit the provider did not document can cut it. Change a number only from the provider's documentation or from a live run, and say where the number came from.

**Where the existing connectors stand.** The numbers were read from the code. A day is "final after" N days when `is_final` is true from midnight UTC at the end of the day plus N days.

| Connector | Final after | Re-read depth | Window of one query |
|---|---|---|---|
| `meterline-litellm` | a day: 2 days | 2 days | at most 31 days; backfill goes from the newest window to the oldest |
| `meterline-anthropic-admin` | a day: 48 hours (the same 2 days) | 3 days, and any day whose latest record is not final | at most 31 days (`MAX_BUCKETS`) |
| `meterline-copilot` | a day: 4 days; a month of billing: more than 7 days after the month ended | 4 days, and the previous month's figures until the 8th | one day for a report; backfill reaches back at most 365 days |
| `meterline-anthropic` | a period: its month ended more than 7 days ago | the current month, and the previous month through the 8th | backfill: at most 31 days; `--since` less than 365 days back |

Three of the four do not mark an empty day loaded before it is final: `meterline-litellm`, `meterline-anthropic-admin` and `meterline-anthropic`. `meterline-copilot` has no such rule. Of the day-based connectors only `meterline-anthropic-admin` has the depth N + 1 (3 for 2). `meterline-litellm` (2 for 2) and `meterline-copilot` (4 for 4) read a day for the last time one day before it is final. A day that only `daily` has read therefore keeps `is_final` false. The connector template uses the default numbers above, not those of the four, as the constants `FINAL_AFTER_DAYS` (4, in `normalise.py`), `REREAD_DAYS` (5, in `cli.py`) and `WINDOW_DAYS` (1, in `fetch.py`). Change them only from the provider's documentation or from a live run.

Held by: `R08`, which fails on a record published again with the same content. Each connector's own numbers are held by its tests: `packages/meterline-litellm/tests/test_litellm_units.py::test_finality_needs_two_full_days`, `packages/meterline-litellm/tests/test_litellm_units.py::test_windows_are_at_most_31_days_and_cover_the_range`, `packages/meterline-anthropic-admin/tests/test_done_check_A10_A11_key_and_final.py::test_done_check_A11_is_final_turns_true_48_hours_after_the_bucket_ended`, `packages/meterline-copilot/tests/test_copilot_units.py::test_finality_four_days_for_reports_and_a_week_after_month_end`, `packages/meterline-copilot/tests/test_copilot_units.py::test_daily_without_state_starts_at_the_month_and_rereads_four_days` and `packages/meterline-anthropic/tests/test_fetch_normalise_units.py::test_is_final_boundary`. No connector uses the default numbers and no test holds the N + 1 rule: both are reviewed in the pull request.

## Raw page naming for a paged endpoint

A query that the provider pages is kept as one raw file with every page inside it. No existing connector writes one file per page.

**The rule.** The path is `raw/<endpoint>/<window>/<batch_id>.json.gz`:

- `<endpoint>` is a name of the connector's own, such as `usage_report_messages`;
- `<window>` names what the query asked for: `2026-09-01_2026-09-30` for a range of days, `2026-09-15` for one day;
- `<batch_id>` is the run's `pulled_at` in UTC, `YYYYMMDDTHHMMSSZ`.

The file is gzip JSON with deterministic bytes. It holds the request URL, `pulled_at` and a list `pages` of `{"status", "body"}`, where `body` is the response text exactly as received. The page number is not in the file name. A file is never written over another: if the path exists, the run stops (R06), and a second run in the same second is refused (R07). When a run asks one endpoint and window more than once, for instance once per person, add a folder under the window. Never put an id, an e-mail or a login in a file name; use a hash.

**Default when the provider documents nothing.** The same rule. If the answer is a link to a file, keep the file's text in the same raw file and the link without its signature.

**Where the existing connectors stand.** This was read from the `fetch.py` of each:

- `meterline-anthropic` and `meterline-anthropic-admin` follow the rule exactly. The window is `<first>_<last>`, or a day.
- `meterline-litellm` adds a folder: `raw/<endpoint>/<window>/<view>/<batch_id>.json.gz`, where `<view>` is `global` or a 16-character hash of the person or end-user id.
- `meterline-copilot` uses the day, the month or the pull date as the window. A per-person billing file adds a folder with a hash of the login. A usage report keeps the text of its download files under `files`, and the links without their signature, next to `status` instead of `pages`. Billing and seats keep `pages`.
- The connector template (`tools/new_connector.py`) follows the rule exactly: one day per query, so `<window>` is the day, and all the pages of the query in one file with the request URL, `pulled_at` and `pages`.

Held by: `R06` and `R07`. `packages/meterline-litellm/tests/test_raw_not_overwritten_litellm.py::test_raw_not_overwritten_a_second_fetch_in_the_same_second_is_refused`, `packages/meterline-anthropic/tests/test_raw_not_overwritten_anthropic.py::test_raw_not_overwritten_a_second_fetch_in_the_same_second_is_refused` and `packages/meterline-copilot/tests/test_raw_not_overwritten_copilot.py::test_raw_not_overwritten_a_second_fetch_in_the_same_second_is_refused` hold it per connector. `packages/meterline-anthropic-admin/tests/test_runs_admin.py::test_backfill_writes_raw_runs_records_and_state` pins the admin file name. No kit check reads the rest of the name, because R01 only asks that `raw/` holds a file: the name is reviewed in the pull request.

## Required and optional response fields

A field the mapping needs and the response lacks stops the run with exit 2 and a failed manifest; it is never read as zero. A field the mapping does not need may be absent or null. A field the connector does not know is ignored, and it is never written to a record unless it is in `extras` or `counters`. No record, log line or file holds the text of a prompt or a response.

## Shape files

A shape file is one JSON file of an endpoint's answer, with the provider's keys and nesting and invented values. The fake API (R14) answers in these shapes, and the shape check keeps it equal to them.

**The rule.** A provider has a folder `docs/api-shapes/<provider>/` with one file per endpoint and a `README.md`. A `.ndjson` file holds one row on its first line. The README:

- starts with a title; front matter is optional, and if it has a `status`, that is never `current`;
- has a line that starts `Provenance:`: which pages or source files the shapes were read from, and that none was captured from a live account;
- says that every value is synthetic;
- has one table with a row per shape file and the columns File, Endpoint, Source, Read on and Mark (a Note column may follow). Source names the page or the source file (with its tag or version). Read on is the date you read it, as `YYYY-MM-DD`. Mark is **[V]**, read and transcribed as shown, or **[I]**, inferred; a note says what was inferred.

The folder also gets a row in the table of [`docs/api-shapes/README.md`](../api-shapes/README.md), with the source, the date and the mark.

**A shape goes stale.** No shape file is ever "the current shape". It says what one source said on one day. To check it, read the source again; when it differs, update the file and the date in the same change. A connector stays at the evidence level "built against documented shapes" until a live run.

**Default when the provider documents nothing.** If there is no page to cite, the shape comes from a live answer: copy its structure, replace every value, never commit the real answer, and mark the row **[I]** with a note that says so.

**When only a `fit/` file exists.** A file in [`docs/api-shapes/fit/`](../api-shapes/fit/README.md) was written from a field table, not from a provider's page, and its mark is **[I]**. It is not a shape file for a connector: it has no source page and no paging. Read the provider's page, write the shape file in the provider's own folder with its own row, and leave the `fit/` file as it is.

**Where the existing connectors stand.** `meterline-anthropic` has six shape files at the top of `docs/api-shapes/`, with a row each in the top README and no folder of its own; a new provider gets a folder. `meterline-anthropic-admin`, `meterline-litellm` and `meterline-copilot` each have a folder, a README with a `Provenance:` line, and a row per file with source, date and mark. The LiteLLM and Copilot READMEs have front matter.

Held by: `tools/test_shape_readmes.py::test_every_row_of_every_shape_readme_has_a_source_a_date_and_a_mark`, `tools/test_shape_readmes.py::test_no_shape_readme_calls_a_shape_current`, `tools/test_shape_readmes.py::test_every_shape_file_is_named_in_a_row_of_the_readme_beside_it` and `tools/test_review_f25_provider_docs.py::test_every_shape_folder_readme_marks_its_values_synthetic_and_names_its_source`. That the shapes were transcribed from the provider's page and not invented is reviewed in the pull request.

## Checking a fake against the shape files

Each connector's shape test calls one helper, `meterline.conformance.compare_shape(answer, shape_file, *, data_keys=(), optional=())`. It returns the differences in keys and nesting between a parsed answer and a shape file, and never compares a value. `shape_file` is the path of a `.json` file, a `.ndjson` file (its first line is the shape) or a shape already loaded.

```python
from meterline.conformance import compare_shape

assert body["data"], "no row, nothing to compare"
assert compare_shape(body, SHAPES / "usage_report.json", optional=["note"]) == []
```

**The rule.** Each difference is one string with the path and what differs: `$.data[0].usage: missing`, `$.data[0].extra: not in the documented shape` or `$.data: expected array, got object`. Every item of a list is compared with the first item of the shape's list. The options are:

- `data_keys`: names of maps whose keys are data, such as model names. Their values are compared with the first value in the shape's map, whatever the keys.
- `optional`: keys that may be absent. `"note"` is a key of that name at any depth. `"metrics.tokens"` is that key directly inside a `metrics` map. A key that is optional but present is still compared.

**Kinds, not values.** A value is compared by its kind only. There are three kinds: object (a map), array (a list) or scalar (a string, a number, a boolean or null). A null where the shape file has a string is not a difference, and neither is a number. Two kinds that differ are a difference: a map where the shape has a list is one.

An empty map or list in the shape file is not compared. An answer with no row in a list has nothing to compare there, so the test asks for a row first, as above. Each connector's test also has a negative control: a key is removed from the fake's answer, a key is added, a list is turned into an object, and each is reported with its path. Show it failing against the planted defect before you keep it.

**Default when the provider documents nothing.** No key is optional and no map is data-keyed. Add a key to `optional` only when the provider documents it as optional, and name the page in the shape README.

**Where the existing connectors stand.** All four shape tests call `compare_shape` and keep no comparison of their own: `packages/meterline-anthropic/tests/test_done_check_shapes.py` (two optional names), `packages/meterline-anthropic-admin/tests/test_shapes_admin.py` (no option), `packages/meterline-litellm/tests/test_done_check_L01_shapes.py` (eight data-keyed maps and the optional metrics fields) and `packages/meterline-copilot/tests/test_done_check_C01_shapes.py` (the optional fields of a row and of a seat).

Held by: `packages/meterline/tests/test_compare_shape.py::test_a_missing_key_is_reported_with_its_path` and the other tests of that file, and one negative control per connector: `packages/meterline-anthropic/tests/test_shapes_negative_control_anthropic.py::test_a_planted_difference_in_the_fakes_answer_is_reported`, `packages/meterline-anthropic-admin/tests/test_shapes_admin.py::test_a_planted_difference_in_an_answer_is_reported_with_its_path`, `packages/meterline-litellm/tests/test_done_check_L01_shapes.py::test_a_planted_difference_in_the_fakes_answer_is_reported` and `packages/meterline-copilot/tests/test_done_check_C01_shapes.py::test_a_planted_difference_in_the_fakes_answer_is_reported`.

## The README rules

A connector README follows the outline the `tools` tests enforce. The tests are `tools/test_package_readmes.py`, `test_review_f24_prose.py`, `test_review_f25_provider_docs.py` and `test_review_f12_evidence.py`. The rules are:

- every link is absolute, built from the package's `Homepage` in `pyproject.toml`, because PyPI shows the README without the repository around it;
- the `**Status:**` line names the evidence level word for word (`built against documented shapes` until a live run), links the [definition](../glossary.md#evidence-levels) and links `#known-limits`;
- the first 40 lines hold the purpose sentence of the root README, a `pip install` line and a runnable example: a code block with a line that starts with the command (`meterline...` or `python`), not with a variable assignment, so set the key on an earlier line, for example with `export`;
- the sections come in this order: Before you run it, Commands, How it works, Files under the data directory, Record mapping, Response shapes, Known limits, Public interface, Tests;
- Response shapes says what to do when a live response differs, in at least three numbered steps;
- Known limits has at least two bold labels and opens with a sentence, not a list;
- every list and table has a sentence before it, and no sentence has more than 45 words; the limit also binds your section of `docs/records.md` and the learning pages `docs/tutorial.md`, `docs/glossary.md`, `docs/architecture.md`, `docs/how-to/configure-policy.md` and `docs/how-to/add-a-provider.md`;
- the `docs/api-shapes/<provider>/README.md` says the values are synthetic and starts a line with `Provenance:`.

## Where other providers' mappings live

A mapping for a provider with no connector yet already exists for five tools. The mapping and its reasoning are in section 10 of the [v2 spec](../dev/meterline-v2-SPEC-v1.0-2026-10-10.md), the code is `packages/meterline/tests/fit_mapping.py`, and the response shapes are in [`docs/api-shapes/fit/`](../api-shapes/fit/README.md). Start from the one nearest to your provider before you write a new mapping.

The five tools are Cursor, OpenAI, Augment, Kiro and Windsurf, in seven files. A mapping reads one file and makes records. It is a test helper that shows the record format can hold the tool, and it is not a connector. These facts were read from `fit_mapping.py` and the `fit/` README. Each mapping lacks the same three things a connector needs:

- **The account label.** Each mapping writes a fixed label into `provider_account_id`: `team-synthetic` for Cursor, `org-synthetic` for the other four. None takes a label from an option or reads one from a file. Use the account label rule above.
- **Paging.** Each mapping reads one JSON or CSV file, once. There is no page loop, no cursor, no check for a repeated page and no raw file. Use the raw page naming rule above.
- **The cost join.** Cursor and Augment carry the money on the same row as the tokens, so they need no join. OpenAI sends tokens and costs from two endpoints: its tokens become `UsageRecord` lines per person or key, its costs become `OrgTotal` lines per project, and nothing ties the two together. Kiro and Windsurf report credits only and carry no money.

They also leave out finality: every `UsageRecord` and `OrgTotal` they make has `is_final` false. Use the finality rule in Windows.

Held by: `packages/meterline/tests/test_done_check_format.py::test_done_check_F08_five_more_tools_fit_the_format` holds that the five mappings still produce valid records. What each lacks, as listed here, is reviewed in the pull request.

## The wiring checklist

A new package has to be named in a number of places, and a test fails for each one that is missing. One command lists them with the exact line or table row to add. Make the skeleton with `.venv/bin/python tools/new_connector.py NAME`, which ends by printing that list, then run

```sh
.venv/bin/python tools/check_new_connector.py NAME
```

and edit what it lists until it prints `nothing missing`; then run `pytest tools`. Run both tools with the python of the virtual environment: the check imports the new package, and a system `python3` reports that it "cannot be imported". The command is the authority: it reads the same definitions as the tests (`tools/wiring.py`), and a test fails for any check of the package list that the command does not know. For a reader without the command, the places are:

- the package installed in editable mode and its files tracked by git;
- the root `README.md`: the Packages table, the Repo map and the install line of the Quick start;
- `AGENTS.md`, `llms.txt` and `docs/THREAT-MODEL.md`, which name every connector, and `docs/README.md` under "Run a connector on a real account";
- `SECURITY.md` (the supported versions and the keys in scope), `CHANGELOG.md` and the duration table of `docs/how-to/operate-daily.md`;
- `.github/dependabot.yml` and each install block of `.github/workflows/ci.yml` (the test job, the minimum-versions job and the dependency audit);
- the release decision: either the package list of `.github/workflows/release.yml` and the procedure in `docs/dev/RELEASING.md`, or a line there that the package is not released yet;
- in the maintainer repository only, the table of specs in force in the index of `docs/dev/`.

**The key variable.** The check finds the variable that your connector reads in `packages/meterline-<name>/src/*/cli.py`. It looks for a line such as `KEY_ENV = "ACME_API_KEY"`: a name in capitals that ends in `ENV`, with a value that ends in `KEY` or `TOKEN`. If it finds none, it assumes `<NAME>_API_KEY`. That name is the one the list asks for in `SECURITY.md` and in the other places. So `KEY_ENV` must name the variable that the connector really reads, for example an admin key when the provider's usage endpoints need one, and not the template's default.

Two things the command cannot write for you: the section of `docs/records.md` and the shapes in `docs/api-shapes/<provider>/`.

No test reads a package count or a typed package list: the `tools` tests read `packages/*/pyproject.toml` (through `tools/package_list.py`), and no document states a number of connectors or packages, so a new package needs no edit there. The command lists any document that does.

## What is not checked by a test

Some rules have no check in the kit. They are reviewed in the pull request:

- the evidence the mapping rests on: a connector stays "built against documented shapes" until a live run, whatever the kit says;
- that the response shapes in `docs/api-shapes/` are transcribed from the provider's documentation and not invented;
- the privacy of the mapping: which provider field becomes `actor_email` or `actor_login`, and that no prompt content is kept;
- that the wiring checklist is complete for a new package;
- paging: the kit runs the connector's own fake with its defaults, so it never asks whether the connector requested a later page or refused a page that came back. R16 only requires exit 2 for whatever the connector's `duplicate` hook scripts;
- the window of a query: its size, against the largest span that the provider documents;
- finality: the values of `is_final` and the number of days after which a record is final. R19 takes the connector's own rule as given (`Connector.is_final`) and checks only that `complete_through` follows it;
- the re-read depth: how many closed days `daily` reads again. R08 runs `daily` twice, but it does not look at which days are read;
- the summing of provider rows that share a record key, when the provider sends them as separate rows.

The template's own tests hold paging, the window, finality and the re-read depth for the template only. `tools/test_new_connector.py` makes a package from the template and checks them there. They do not travel with a new connector, so add your own tests for each, as [step 7 of the guide](../how-to/add-a-provider.md#steps) says.
