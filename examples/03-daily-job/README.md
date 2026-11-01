# 03 - the daily job

**Goal.** Run every configured connector once a day, build gold from what they stored, say what happened as one JSON line per step, and exit non-zero if any step failed. A failed provider does not stop the others. The job picks `backfill` until a backfill has published, retries only a transient failure (exit 1), and stops and alerts on an input error (2) or a permanent refusal (3). [Operate the daily job](https://github.com/meyerx/meterline/blob/main/docs/how-to/operate-daily.md) covers locking, durations and where to look.

**Reads.** `job.example.toml` (which connectors, where to keep data), the environment variables each connector documents for its key, and the organisation files, policy and price list that gold needs. It calls each connector through its Python entry point (`run` in `meterline_anthropic`, `meterline_anthropic_admin`, `meterline_litellm`, `meterline_copilot`), then `meterline_gold.build`. Keys never appear in the configuration.

## Run it

The test runs the job end to end against the connectors' public fake APIs, so it needs no key and no network. Run it from the repository root, after the install in the README quick start (on Python 3.10 also `pip install tomli`).

```sh
.venv/bin/python -m pytest -q examples/03-daily-job/test_job.py -p no:cacheprovider
```

**Expected output.** `12 passed`. The tests check that each connector the job runs writes silver and gold is built, that a provider answering HTTP 500 gives a non-zero exit while the other connectors still write silver, that a run after a published backfill is a `daily`, that a failed first backfill is followed by a backfill and not a `daily`, that a 401 is not retried and raises an alert, that a 503 is retried once, that an input error is not retried, and that no status line holds a key. Two more check, under umask 022 and 000, that the silver copy the job stages is readable by its owner only.

## Use it on a real account

Copy `job.example.toml`, set the paths, export the keys (see each connector's README) and `METERLINE_GOLD_KEY`, and run:

```sh
python examples/03-daily-job/job.py job.toml
```

It prints lines such as `{"attempts": 1, "batch_id": "...", "command": "backfill", "exit_code": 0, "failure_kind": "none", "http_status": null, "ok": true, "records": {"usage": 57}, "skipped": [], "step": "claude"}` and a final `gold` line. A connector runs a `backfill` from its `since` date until a backfill has published, then `daily`. The step line of a failure also holds `failure_kind` and, for an input error or a permanent refusal, an `alert`.

The job exits with the worst code of its steps. The table says what each code means and what the job does:

| Exit | `failure_kind` | The job |
|---|---|---|
| `0` | `none` | Goes on. |
| `1` | `transient` | Waits `retry_wait_seconds` (300) and runs the same command once more (`retries`, default 1). |
| `2` | `input` | Does not retry; the line carries an `alert`. |
| `3` | `permanent` | Does not retry; the line carries an `alert`. |

A cron line for 06:30 each day:

```
30 6 * * * cd /srv/meterline && .venv/bin/python examples/03-daily-job/job.py job.toml >> job.log 2>&1
```

A systemd timer, `meterline-job.timer`, with `meterline-job.service` running the same command as `ExecStart`:

```
[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

## How long the first Copilot backfill takes

The job runs `backfill` for a connector until one has published, so the first run of the Copilot step reads the whole history from `since`. The connector's own request rate sets the time. These figures are estimates, not measured on a live account.

They come from the defaults in `packages/meterline-copilot/src`: 600 requests an hour (`--rate-per-hour`), a history of at most 365 days (the usage reports go back one year), and the requests below.

- **Per day.** One request for the users report, one for the aggregate report and one for the billing of the day: 3 a day, so 3 × 365 = 1,095 for a full year.
- **Per person and month.** One billing request for each person seen in a month. A full year touches up to 13 calendar months, so at most 13 × P requests for P people.
- **Once.** The seat list, one request per 100 seats, and the seat summary.
- **Not counted.** The report files come from another host, which the connector does not throttle. A retry or an extra page adds a request. A rate-limit reply makes the connector wait, up to two hours for a reset time.

So requests = 3 × 365 + 13 × P + the seat requests, and hours = requests ÷ 600.

| People (P) | Requests | At 600 an hour |
|---|---|---|
| 100 | 1,095 + 1,300 + 2 = about 2,400 | about 4 hours |
| 400 | 1,095 + 5,200 + 5 = about 6,300 | about 10.5 hours |

The result depends on four things: the days of history (`since`, at most 365), the number of people seen in each month, the rate, and the waits the provider asks for. A later `since` costs less: use the number of days and of months you really read. Raising the rate shortens the time in proportion: set `rate_per_hour = 3000` in the connector's table of the job file, and the 400-person case takes about 2 hours. The Copilot README says the API allows 5,000 requests an hour for a user token.

If the run stops part way, the job retries it once after `retry_wait_seconds` when the failure is transient. The next scheduled run runs `backfill` again until one has published. The connector keeps its raw pages and its progress, so the new run resumes. A long backfill can outlast the interval between two scheduled runs; [Operate the daily job](https://github.com/meyerx/meterline/blob/main/docs/how-to/operate-daily.md) covers locking and the other connectors' durations.

## Two cost figures to read with care

The test builds gold with gold's example price list, which holds invented prices. If you copy that list for a real job, read both points first. They are also in the [cost cookbook](https://github.com/meyerx/meterline/blob/main/docs/how-to/finops-kpis.md#billed-consumption-and-reference-cost).

- **A seat line can create a seat cost where there are no seats.** The example list has a seat price for `anthropic` of 27.00 a month with no `provider_account_id`. A line with no `provider_account_id` matches every organisation of that provider. When a provider sends no seat assignments, gold counts its seats from the rows of `identities.csv` for that provider, for the persons employed that day, whatever the actor kind. The Admin connector sends no seat assignments, and a Console organisation has no seats. So an Admin-fed build shows a seat count and a `seat_cost` for it, and `total_cost_ref` includes that cost. On the invented organisation of the tests, 20 identity rows give 540.00. Remove the Anthropic seat line from the price list you use, unless the organisation pays per seat. Scoping the line with `provider_account_id` is not enough when the silver holds more than one Anthropic organisation: the seat row then has an empty `provider_account_id`, and the line still matches.
- **`usage_cost_ref` is a lower bound when a model has no price.** Gold sets the reference cost of a row from the price list. A row it cannot price has none: a model missing from the list, a model with no price for that day, or a row with no model. `costs_month` adds up the rows that have one, and shows `0.00` when none has. The row carries no flag, and `total_cost_ref` has the same gap. `central/data_quality.csv` counts the rows, as the metrics `rows_unpriced` and `rows_unknown_model`. Check them before you use `usage_cost_ref` as a total. The exec files carry no count.

## What this does not prove

The fakes serve invented data in the documented response shapes. A green test does not show that live authentication works, that a live response has the documented shape, or that the rate limits hold. Run each connector once against a real account and read its README's "Response shapes" section before you rely on the job.
