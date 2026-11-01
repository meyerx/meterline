# Operate the daily job

This page is for the person who runs meterline once a day on a real account. It says in what order the steps run, which command to use, how to keep two runs apart, how long a first backfill takes, and where to look when a run fails. The job itself is [example 03](../../examples/03-daily-job/README.md); the rules each connector keeps are in the [connector contract](../reference/connector-contract.md).

## The order of a run

A daily run has two parts, always in this order:

1. **The connectors.** Each configured connector pulls its provider's API into its own folder under the data root and publishes records. The connectors do not depend on each other, and a failed one does not stop the next.
2. **Gold.** The job copies every connector's published records into one silver folder and builds the gold datasets from it, into a folder named for the day. Gold runs on whatever silver exists, so a provider that failed today shows its last good data.

Gold needs the organisation files, a policy file, a price list and the hashing key in `METERLINE_GOLD_KEY`. The connectors need only their own keys, read from the environment and never from the job file.

## Which command to run

A connector run is a `backfill` until a backfill has published, and a `daily` afterwards. Do not take the presence of `state.json` as proof that a backfill finished. A backfill that stops on a 503 writes a `state.json` too, and a `daily` started from it loads only the current month and leaves the rest of the history out.

The job decides from the run manifests. A backfill has published when some file under `runs/` says `"command": "backfill"` and `"status": "published"`. To check by hand:

```sh
grep -l '"command": "backfill"' job-data/claude/runs/*.json | xargs grep -l '"status": "published"'
```

No output means the next run must be a backfill. A `daily` run with no `state.json` stops with exit 2 and asks for `--since`; it never loads a month quietly.

## One run at a time

Two runs on the same data folder at once can write the same raw file or the same state file, so run one at a time. A second run that starts in the same UTC second as a finished one is refused with exit 2, but two overlapping runs are not caught by that rule. Take a lock around the whole job.

With cron, `flock` does it; `-n` makes the second run give up at once instead of queueing:

```
30 6 * * * cd /srv/meterline && flock -n /var/lock/meterline.lock .venv/bin/python examples/03-daily-job/job.py job.toml >> job.log 2>&1
```

With systemd, a timer never starts a service unit that is still running, so a long backfill is not overlapped by the next day's run. The units are:

```
# meterline-job.service
[Service]
Type=oneshot
WorkingDirectory=/srv/meterline
ExecStart=/srv/meterline/.venv/bin/python examples/03-daily-job/job.py job.toml

# meterline-job.timer
[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

Run the first backfill by hand, outside the timer, and read its output before you let the schedule take over.

## What the job does with a failure

Every connector exits with one of four codes. The job retries one of them and stops on the others:

| Exit | Meaning | The job |
|---|---|---|
| `0` | The run finished. | Goes on. |
| `1` | Transient: a 5xx, a 429 after retries, a timeout. | Waits `retry_wait_seconds` (300) and runs the same command once more; `retries` sets how many times. |
| `2` | Input: bad options, no key, a damaged state file, a failed check. | Does not retry. The status line carries an `alert`. |
| `3` | Permanent: the provider refused the request (400, 401, 403, 404, 422). | Does not retry. The status line carries an `alert`. |

The job prints one JSON line per step, with `command`, `ok`, `exit_code`, `failure_kind`, `http_status` and `attempts`, and exits with the worst code of its steps. A scheduler that alerts on a non-zero exit therefore alerts on every failure, and a person acts on 2 and 3: a retry cannot fix a wrong key.

## How long a first backfill takes

A first backfill reads the whole history in one run, and the time is set by the provider's rate limit. These are estimates computed from each connector's documented limits and window sizes at its default rate. They are not measurements: no connector has run against a live account, so check the real figure on your own account.

| Connector | Default rate | Requests for a year of history | At the default rate |
|---|---|---|---|
| `meterline-anthropic-admin` | 20 a minute | About 2 per 31-day window and report page: roughly 25 | A few minutes at most |
| `meterline-anthropic` | 50 a minute | About 8 per window, one `users` request per day and one `summaries` request: roughly 470 | About 10 minutes |
| `meterline-litellm` | 120 a minute | One request per window, then one per active user per window: 12 × (1 + U) for U users | 400 users: about 4,800 requests, 40 minutes |
| `meterline-copilot` | 600 an hour | 3 per day, one per person per month, a few for seats and billing: 3 × 365 + 13 × P for P people | 100 people: about 2,400 requests, 4 hours; 400 people: about 6,300, 10.5 hours |

The Copilot figure counts API requests only; the report downloads go to another host and are not throttled. GitHub allows 5,000 requests an hour for a user token, so `--rate-per-hour 3000` brings the 400-person case to about two hours. Usage reports go back at most a year, so a longer `--since` is clamped and the manifest says so.

A page the provider cuts into several pages adds one request per extra page, and a retry adds one request. A run that stops part way keeps its raw pages and its progress in `state.json`, and running the same command again resumes.

## Where to look when a run fails

Look in this order:

1. **The status line of the step.** `failure_kind` says whether to wait (`transient`), to fix the input (`input`) or to fix the key, scope or identifier (`permanent`). `http_status` is the status that stopped the run, when one did, and `alert` says what a person has to do.
2. **The message on stderr.** It starts with `transient:` or `permanent:`, or names the file or option that is wrong. In `job.log` it sits just above the step line.
3. **The run manifest.** `runs/<batch_id>.json` of the connector. A run that failed a check has `"status": "failed"`, `"failure_kind": "input"` and the reasons under `failures`. A run that stopped on an API error wrote no manifest: its progress is in `state.json`, in the `pending` block.
4. **The raw pages.** `raw/<endpoint>/<window>/...json.gz` holds each response as received. `gzip -dc` prints it, so you can see what the provider really sent. The key is never in there.

## What this does not prove

The tests run the job against the connectors' public fake APIs, with invented data. A green test shows that the job chooses the right command, retries only what it should and stops on a refusal. It does not show that your key is accepted, that a live response has the documented shape, that the rate limits behave as written above, or how long your backfill takes. Run each connector once on your account, read its README's "Response shapes" section, and check the first backfill's duration against the table before you rely on the schedule.
