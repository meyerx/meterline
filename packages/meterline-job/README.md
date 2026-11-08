# meterline-job

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Runs the meterline connectors you configure, copies their records into one silver folder, builds gold from it and publishes the result, as one command that a scheduler can run every day. It prints one JSON status line per step and exits with a code that says what kind of failure came first. Project terms such as *silver* and *gold* are defined in the [glossary](https://github.com/meyerx/meterline/blob/main/docs/glossary.md).

It keeps no key of its own: each connector reads its key from its documented environment variable, and gold reads its hashing key from `METERLINE_GOLD_KEY`. Python 3.10 or later; it needs `meterline` and `meterline-gold`, and each connector is an extra. **POSIX only** (Linux and macOS): the job takes file locks and swaps a symbolic link, and on a platform without `fcntl`, or where the lock file cannot be made, it stops with exit 2 before any step.

**Status:** tested against the connectors' fake APIs only. It is not a connector and does not run through the connector kit. See [Known limits](#known-limits).

```sh
pip install "meterline-job[all]"
meterline-job check job.toml
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline -e "packages/meterline-job[dev]"
```

## The command

```
meterline-job run CONFIG [--step NAME] [--no-retry]
meterline-job check CONFIG
```

`run` runs every connector in the order of the file, then `silver`, then `gold`, then `publish`. With `--step` it runs one step: a connector's name, `silver`, `gold` or `publish`. With `--no-retry` a connector that fails transiently is not run again. `check` validates the file, the connector packages and the paths the file names, makes no network call and exits 0 or 2.

| Extra | Installs |
|---|---|
| `anthropic` | `meterline-anthropic` |
| `anthropic-admin` | `meterline-anthropic-admin` |
| `litellm` | `meterline-litellm` |
| `copilot` | `meterline-copilot` |
| `all` | every connector in this table |

A configured connector whose package is missing is an input error (exit 2) that names the extra to install.

## The configuration

The file is TOML; [`job.example.toml`](https://github.com/meyerx/meterline/blob/main/examples/03-daily-job/job.example.toml) shows every key. An unknown key is an input error. The keys of a `[[connector]]` table other than the ones below are the connector's own options and go to its `run` as they are.

| Key | Meaning |
|---|---|
| `data_root`, `gold_root` | where each connector keeps its folder (`<data_root>/<name>`), and where the gold folders go: `<gold_root>/builds/<YYYY-MM-DD>` (UTC) and `-2`, `-3` for a rerun, `<gold_root>/frozen/` for closed months and `<gold_root>/published` for the folder to serve |
| `[publish]` `freeze_after_days`, `freeze_cap_extra_days` | days after the end of a month before it is frozen (4) and extra days after which it is frozen whatever the sources say (10) |
| `[gold]` `org`, `policy`, `prices` | the organisation files, the policy and the price list that gold needs |
| `[gold]` `parquet` | write the Parquet twins of the CSV files; true by default |
| `[gold]` `invoice` | the provider invoice to reconcile with; optional |
| `[[connector]]` `name`, `kind`, `since` | a name (not `silver`, `gold` or `publish`), `anthropic`, `anthropic_admin`, `litellm` or `copilot`, and the first day of a backfill |
| `[[connector]]` `retries`, `retry_wait_seconds` | how many times a transient failure is retried (1) and the wait in seconds (300) |

**What to serve.** Each reader receives only their own sub-folder of `<gold_root>/published/`: `central/` and `manifest.json` to central analysts, `exec/` to executives, `manager/<key>/` and `person/<key>/` only to the person that key belongs to; never `builds/`, `frozen/`, `frozen.json` or a `published-*` folder. `meterline_gold.servable(path, audience)` is the check, and [Who gets which folder](https://github.com/meyerx/meterline/blob/main/packages/meterline-gold/README.md#who-gets-which-folder) is its table. The publish step freezes a closed month and delivers it once, and keeps the rest of the month in progress fresh. The operations page [`operate-daily.md`](https://github.com/meyerx/meterline/blob/main/docs/how-to/operate-daily.md#the-published-folder) says how and states the delivery rule.

## Exit codes

The job exits with the worst step in the order 0 < 1 < 2 < 3 < 4. A scheduler alerts on a non-zero code, and a person acts on 2, 3 and 4.

| Exit | `failure_kind` | Meaning |
|---|---|---|
| `0` | `none` | Every step is done. |
| `1` | `transient` | A connector failed and may work later, or a folder was locked by another run. |
| `2` | `input` | The input is wrong: the file, a key, the state file, the data or gold's inputs. |
| `3` | `permanent` | The provider refused the request. |
| `4` | `build` or `publish` | Gold's build failed an assertion, or failed in any way that is not a wrong input (`build`); or the publish step failed (`publish`). A publish skipped because gold failed in the same run has `failure_kind` `skipped` and gold's exit code. |

Code 4 exists only on the job's step lines and exit code. The connectors keep exit codes 0 to 3, and gold is never retried by the job.

## Public interface

```python
import meterline_job

code = meterline_job.run_job(meterline_job.load_config("job.toml"), step=None, no_retry=False)
```

`run_job` takes the parsed configuration and returns the exit code. For a test it also takes `transports` (a map from a connector's name to an `httpx` transport), `now`, `sleep` and `out`.

## Known limits

**Not proven.**

- It was built and tested against the connectors' fake APIs. Nothing here proves live authentication, live response shapes or rate limits.
- A folder on a network file system is not tested.

**Left open.**

- The silver step skips a connector whose folder is locked and exits 1, so a gold build after it lacks that connector's records.
- Nothing deletes an old build: `builds/` grows by one build per run, and `frozen/` is kept. Delete old builds by hand (the build a `frozen.json` entry names may go too).
- The erase tool is a later change; the manual procedure is in [PRIVACY.md](https://github.com/meyerx/meterline/blob/main/docs/PRIVACY.md) and the layout makes it possible without rotating the hashing key.
- The job needs a POSIX system (Linux or macOS): its locks and the `published` link need it, and it exits 2 elsewhere.

## Tests

```sh
python -m pytest -q packages/meterline-job/tests
```

The tests run the whole job against the connectors' public fake APIs and the simulator, so they need no key and no network.
