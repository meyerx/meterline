# Operate the daily job

This page is for the person who runs meterline once a day on a real account. It says in what order the steps run, which command to use, how to keep two runs apart, how long a first backfill takes, and where to look when a run fails. The job is the [`meterline-job`](../../packages/meterline-job/README.md) package, shown in [example 03](../../examples/03-daily-job/README.md); the rules each connector keeps are in the [connector contract](../reference/connector-contract.md).

## The order of a run

`meterline-job run job.toml` runs four parts, always in this order, and prints one JSON line for each step:

1. **The connectors.** Each configured connector pulls its provider's API into its own folder under the data root and publishes records. The connectors do not depend on each other, and a failed one does not stop the next.
2. **Silver.** The job copies every connector's published records into one silver folder, one connector at a time, each under that connector's lock. A connector whose folder is locked by another run is skipped, and the step exits 1.
3. **Gold.** The job builds the gold datasets from silver into a new folder under `<gold_root>/builds/`: `builds/<YYYY-MM-DD>` (UTC) the first time that day and `builds/<YYYY-MM-DD>-2`, `-3` ... for a rerun. A build is never overwritten, so a rerun on the day a month closes is fine. Parquet twins are written unless `parquet = false`, and the `invoice` file is used when the configuration names one. A leftover `builds/*.tmp` folder from a killed build is removed first. The job does not delete old builds: `builds/` grows by one build per run.
4. **Publish.** The job freezes the months that are due and assembles the `published` folder, described below.

Gold runs on whatever silver exists, so a provider that failed today shows its last good data. `meterline-job run job.toml --step silver` (or `gold`, `publish`, or a connector's name) runs one step alone, and `--no-retry` turns the connector retry off. `meterline-job check job.toml` validates the file, the connector packages and the paths with no network call; run it after every edit.

Gold needs the organisation files, a policy file, a price list and the hashing key in `METERLINE_GOLD_KEY`. The connectors need only their own keys, read from the environment and never from the job file.

## Which command to run

A connector run is a `backfill` until a backfill has published, and a `daily` afterwards. Do not take the presence of `state.json` as proof that a backfill finished. A backfill that stops on a 503 writes a `state.json` too, and a `daily` started from it loads only the current month and leaves the rest of the history out.

The connector decides, from the run manifests. The job runs each connector's `auto` command, which is `backfill` until a backfill has published and `daily` afterwards (`meterline.run.auto_command`, contract rule R18). The job reads the same choice before the run and writes it as the step line's `command`; the run manifest says `"requested": "auto"` and, under `command`, what ran. A backfill has published when some file under `runs/` says `"command": "backfill"` and `"status": "published"`. To check by hand:

```sh
grep -l '"command": "backfill"' job-data/claude/runs/*.json | xargs grep -l '"status": "published"'
```

No output means the next run must be a backfill. A `daily` run with no `state.json` stops with exit 2 and asks for `--since`; it never loads a month quietly.

## One run at a time

Two runs on the same data folder at once can write the same raw file or the same state file. Every connector therefore takes an exclusive lock on `<data folder>/runs/.lock` (a `flock`) before it does anything, and the job takes `<gold_root>/.lock` from the start of the gold step to the end of the publish step (one hold, so no other run's build lands in between). A run that finds a lock held stops at once with exit 1 and the message `data folder is in use by another run`, makes no request and writes nothing. The operating system releases the lock when the process ends, however it ends, so a crash leaves nothing to clean up. A second run that starts in the same UTC second as a finished one is still refused with exit 2.

The lock replaces the `flock` line that this page used to ask for around the whole job: cron needs no wrapper now. The lock was tested on local disks only; on a network file system, `flock` depends on the file system, so keep the data root on a local disk.

With cron:

```
30 6 * * * cd /srv/meterline && .venv/bin/meterline-job run job.toml >> job.log 2>&1
```

If you also want to keep a second copy of the job from starting at all, `flock -n /var/lock/meterline.lock` in front of the command does it; the locks above make that optional.

With systemd, a timer never starts a service unit that is still running, so a long backfill is not overlapped by the next day's run. The units are:

```
# meterline-job.service
[Service]
Type=oneshot
WorkingDirectory=/srv/meterline
ExecStart=/srv/meterline/.venv/bin/meterline-job run job.toml

# meterline-job.timer
[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

Run the first backfill by hand, outside the timer, and read its output before you let the schedule take over.

## What the job does with a failure

Every connector exits with one of four codes, and the job adds a fifth for gold. The job retries one of them and stops on the others:

| Exit | Meaning | The job |
|---|---|---|
| `0` | The run finished. | Goes on. |
| `1` | Transient: a 5xx, a 429 after retries, a timeout, or a folder locked by another run. | For a connector: waits `retry_wait_seconds` (300) and runs the same command once more; `retries` sets how many times. A skipped silver copy or a locked gold folder is not retried. |
| `2` | Input: bad options, no key, a damaged state file, a failed check, a missing connector package, or gold's inputs. | Does not retry. The status line carries an `alert`. |
| `3` | Permanent: the provider refused the request (400, 401, 403, 404, 422). | Does not retry. The status line carries an `alert`. |
| `4` | Build: gold failed an assertion, or failed in any way that is not a wrong input, or the publish step failed (a changed frozen file, a failed check on the new folder). Only the job has this code. | Does not retry; gold is never run again by the job, and a failed publish leaves the previous `published` folder as it was. The status line has `failure_kind` `build` and an `alert`. |

The job prints one JSON line per step, with `command`, `ok`, `exit_code`, `failure_kind`, `http_status` and `attempts`, and exits with the worst code of its steps in the order 0, 1, 2, 3, 4. The gold line also holds `build` (the folder's name under `builds/`), `data_through` and `retry` (false). A scheduler that alerts on a non-zero exit therefore alerts on every failure, and a person acts on 2, 3 and 4: a retry cannot fix a wrong key or a failed check.

## The `published` folder

The job's last step makes `<gold_root>/published`, a link to a folder named `published-<UTC time>` that holds what a reader may be served: `central/`, `exec/`, and a `manager/<key>/` or `person/<key>/` for each person and manager of the latest build, never `delivery/`. Serve this link, for example `published/exec` to the executives with the example dashboard, and nothing else under the gold root: `builds/`, `frozen/`, `frozen.json` and every `published-*` folder are central material.

- **In progress and frozen.** The rows of a month in progress come from the latest build, so every run refreshes them. A month is frozen, by copying its rows into `frozen/<YYYY-MM>/` with a `SHA256SUMS` file and an entry in `frozen.json`, in the first run that ran every connector step successfully, when every connector's `complete_through` is on or after the month's last day, no row of the month can still change (`is_final` is true in `person_day`, `person_month`, `pools_month` and `groups_month`) and `freeze_after_days` (4) have passed. If it is still open `freeze_after_days + freeze_cap_extra_days` (4 + 10) days after its end, the next successful publish freezes it from the latest build anyway: `forced` in `frozen.json`, `forced_freeze YYYY-MM` in the status line's `warnings`, exit 0. Both numbers are in a `[publish]` table of the job file. From then on the month does not change.
- **Swapped in at once.** The new folder is checked first (the checksums of every frozen month, gold's A09 check, file modes 0600 and folder modes 0700) and the link is replaced in one step, so a reader never sees half a folder. The previous folder is kept; older ones are removed. Any failure exits 4 and leaves the link where it was. Windows is not supported.
- **After a key change.** Manager and person folders show only the months frozen under the current hashing key, so their history restarts at the rotation month; `exec/` keeps its frozen months and `central/` keeps all of them, so one person can appear there under two keys. `central/published.json` lists the months in `key_rotations`.
- **People who left.** A person who is not in the latest build, and a manager who no longer manages, have no published folder. Their history stays in `central/` and `frozen/`.
- **Freshness.** `central/published.json` says which build the folder comes from, how far the data is complete, when it was published, which months are frozen, in progress or forced, and the warnings; `freshness.json` in every other audience folder holds only `published_at` and `frozen_months`. Their schemas are `docs/schemas/job/published.schema.json` and `docs/schemas/job/freshness.schema.json`.
- **A changed policy.** If `k` or the executive depth of the policy differ from those a frozen month was published under, the publish goes ahead and adds `policy_changed YYYY-MM` to the warnings.

A month is refreshed on every published run until it is frozen, which happens once every source has final data for it and `freeze_after_days` have passed, or at the latest `freeze_after_days + freeze_cap_extra_days` days after its end at the next run; from then on it is delivered once and does not change, except that an erased person's rows are removed, and after a change of the hashing key manager and person folders show only months frozen under the new key. Serve only the `published` folder, never `builds/` or `frozen/`, and give managers and executives only its latest state; keeping earlier states is the adopter's choice, not the tool's. A reader who keeps every refresh of an in-progress month can sometimes learn one person's usage between two refreshes, one person's month-to-date amount when a correction or an organisation change moves that person, a cell published in one refresh and hidden in the next, or a named exception that appears and then disappears. The adopter accepts this risk and has it validated by its data protection officer.

What this does not do: it deletes no build, migrates no frozen month to a new `schema_version` (a different version exits 4), and has no erase command (see Erasure in [PRIVACY.md](../PRIVACY.md)).

## How long a first backfill takes

A first backfill reads the whole history in one run, and the time is set by the provider's rate limit. These are estimates computed from each connector's documented limits and window sizes at its default rate. They are not measurements. `meterline-anthropic-admin` is verified live on a small Console organisation (two days of usage), as its README records; the other connectors have run only against their fake APIs. Check the real figure on your own account.

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
