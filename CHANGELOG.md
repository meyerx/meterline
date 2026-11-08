# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and each package follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`export/vX.Y` names an export of the repository, not a package version.

Each section is headed by a package and its version: `meterline`, `meterline-anthropic`, `meterline-anthropic-admin`, `meterline-litellm`, `meterline-copilot`, `meterline-sim`, `meterline-gold` or `meterline-job`.

## Upgrading from 0.1

The gold folder moved. A v0.1 run of the example job wrote `<gold_root>/<YYYY-MM-DD>`; a v0.2 run writes each build under `builds/` (`builds/<date>`), freezes closed months under `frozen/` and serves `published/`, all inside the gold root. A dashboard or script that reads `<gold_root>/<date>` gets no new data: point it at its audience folder under `published/`. v0.2 neither reads nor deletes the old dated folders, and they hold person-level data, so keep or delete them under your retention rule. Exit code 4 is new: an alert that matches only 1 to 3 misses it.

## [Unreleased]

Dates are set at the v0.3 gate. Each package that changed has a sub-section; the packages whose version is not named here did not change.

### meterline 0.3.1

#### Changed

- `auto` does not count a first backfill that published no record of any kind (`published_counts` adding up to 0) as a backfill: the next `auto` runs `backfill` again, so the history is not skipped for good. A manifest with no `published_counts` counts as before. Contract rule R18 and its check in `meterline.conformance` say so.
- `meterline.run.file_lock` and `data_folder_lock` take `required=True`: no `fcntl`, or a lock file that cannot be made, raises `LockUnavailable` instead of going on unlocked (a connector keeps the old behaviour). `meterline.run.lock_problem` says why in plain words.

### meterline-gold 0.3.0

#### Added

- `person_day.model_provider` (the provider that served the model; `mixed` if the merged records differ), `costs_month.overlap_cost_ref` (the part of a proxy source's reference cost that was routed to a provider whose own connector is in the build) and a `proxy_overlap` event in `alert_events` for each proxy, provider and month with an amount.
- `month_status` and `is_final` in `seats_month` and `costs_month`, and `month_status` in `pools_month`, in the central and the exec forms, so a running month can be told from a closed one at organisation level. `pools_month.is_final` now also needs a complete month.
- `GOLD_SCHEMA_VERSION` is 2.1: columns were appended only (before `hidden` in the exec forms, which keeps it last).

#### Changed

- The README's "No delivery" line points to an example of a sender, [example 07](https://github.com/meyerx/meterline/blob/main/examples/07-person-messages/README.md), which is not part of meterline.
- Requires `meterline>=0.2.0,<0.4` (it was `>=0.2`).

#### Known limits

- A frozen month written under `schema_version` 2.0 cannot be combined with a 2.1 build: publish exits 4, as it does for any change of the schema version. Decide before upgrading what to do with the frozen months you hold.

### meterline-job 0.2.0

#### Added

- `central/published.json` lists each source (`name`, `provider`, `step_ok`, `last_ok_run`, `complete_through`, `failure_kind`) and the warnings `stale_source <name>` and `complete_through_unavailable <name>`; every `freshness.json` has `data_through` and `stale_sources`. New `[publish]` settings `stale_after_days` (3) and `keep_builds` (2).
- A crash between the rename of a frozen month and the write of `frozen.json` is recovered on the next publish (`frozen_entry_recovered <month>`); a forced freeze prints `warning: forced_freeze <month>` on stderr.
- A connector's step line has `records_total` and, for a successful run that published nothing, `empty: true`.

#### Changed

- Silver, gold and publish run under one hold of `<gold_root>/.lock`; a second run that finds it held writes nothing there.
- A publish failure is `failure_kind` `publish` and a publish skipped because gold failed is `skipped`, with gold's exit code.
- Every line on stdout is one JSON object; what a connector prints goes to stderr.
- The job needs file locks and says it is POSIX only (Linux and macOS): without `fcntl`, or where the lock file cannot be made, it exits 2 before any step.
- Publish refuses a symbolic link anywhere in a build or a frozen month (exit 4). After a publish the job keeps the newest `keep_builds` builds and deletes the others, and `delivery/` from the older kept ones.
- Requires `meterline>=0.3.1,<0.4` and `meterline-gold>=0.3.0,<0.4`.

### meterline-anthropic 0.2.2

#### Changed

- Requires `meterline>=0.3.1,<0.4`; `setuptools` is pinned exactly in `[build-system]`; PyPI shows a Documentation link. No code changed.

### meterline-anthropic-admin 0.1.2

#### Changed

- Requires `meterline>=0.3.1,<0.4`; `setuptools` is pinned exactly in `[build-system]`; PyPI shows a Documentation link. No code changed.

### meterline-copilot 0.1.2

#### Changed

- Requires `meterline>=0.3.1,<0.4`; `setuptools` is pinned exactly in `[build-system]`; PyPI shows a Documentation link. No code changed.

### meterline-litellm 0.1.2

#### Changed

- Requires `meterline>=0.3.1,<0.4`; `setuptools` is pinned exactly in `[build-system]`; PyPI shows a Documentation link. No code changed.

### meterline-sim 0.1.1

#### Changed

- Requires `meterline>=0.2.0,<0.4`; `setuptools` is pinned exactly in `[build-system]`; PyPI shows a Documentation link. No code changed.

### Repository

#### Changed

- The release job attests the files before it runs anything else with the token: the tag is checked in a first job, the job that attests only installs the build tools from `tools/release-constraints.txt` with hashes, builds, attests and uploads, and a second job without those permissions compares the digests before it runs `twine`, installs the wheel or runs the SBOM tool.
- `tools/export_public.py --onto` takes its baseline from the maintainer's first-parent export commits only.
- The documents say that each reader receives only their own sub-folder of `published/`, with `servable(path, audience)` as the check; the operations page has "Watch it", "When something goes wrong" and "Back up", and example 03 a complete systemd service and timer; the privacy page has a section on correcting a delivered month; the cookbook leaves out the month in progress, subtracts the overlap of a proxy and has a cash view; `AGENTS.md`, `llms.txt` and the README know the daily job.

## [meterline 0.3.0] - 2026-11-03

### Added

- `meterline.run.data_folder_lock`, the lock every connector takes on its data folder, and contract rule R17 for it.
- `meterline.run.auto_command` and `meterline.run.final_through`, which decide what `auto` runs and the last final day of a data folder; the run manifest gains an optional field, `requested`.
- Contract rules R18 (`auto`) and R19 (`complete_through`) in `meterline.conformance`; `Connector` gains `complete_through` and `is_final`. R09 now also runs `rebuild` over manifests without `requested`.

## [meterline-anthropic 0.2.1] - 2026-11-03

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-anthropic-admin 0.1.1] - 2026-11-03

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-copilot 0.1.1] - 2026-11-03

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-litellm 0.1.1] - 2026-11-03

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-gold 0.2.0] - 2026-11-03

### Added

- Three small accessors, `write_parquet_text`, `dataset_forms` and `policy_privacy`, that `meterline-job` composes the published folder from (internal-stable, not part of the public API).

## [meterline-job 0.1.0] - 2026-11-03

### Added

- `meterline-job`: the daily job as a command (`meterline-job run` and `meterline-job check`), from `examples/03-daily-job`. It runs each connector's `auto` command, then builds silver and gold, and prints one JSON status line per step. Each step can run alone, a gold failure exits 4 on its own (the connectors keep 0 to 3), `parquet` and `invoice` reach gold, and a leftover build `.tmp` folder is cleared.
- It also locks the gold folder, so two jobs on one folder do not run at once.
- The `publish` step. Each run builds gold into `builds/<date>` (then `-2`, `-3`), never over an existing folder; a month is frozen by copy into `frozen/<month>` with a `SHA256SUMS` file once every source has final data for it and `freeze_after_days` have passed (or forced by `freeze_cap_extra_days`); `published` is a link to an assembled `published-<time>` folder, swapped in at once after the checksums, gold's A09 check and a permission check; manager and person folders restart at a change of the hashing key; `central/published.json` and a `freshness.json` per audience folder say how fresh it is (schemas in `docs/schemas`). New `[publish]` settings. A gold failure no longer leaves a day's folder that blocks a rerun.
- Docs: the delivery rule (a month is refreshed until it is frozen, then delivered once) is written the same way in the gold README, `PRIVACY.md`, `THREAT-MODEL.md` and the operations page; the erasure steps no longer rotate the hashing key; the dashboard example serves `published/`.

### Known limits

- Tested against the connectors' fake APIs only; it is not a connector and does not run through the connector kit.

## [meterline 0.2.0] - 2026-11-01

### Added

- The v2 record format: usage, activity, seat snapshot, seat assignment and organisation total records, their validation, a converter for v1 files, and JSONL and Parquet writers.
- `meterline.conformance`, a public test kit that checks a connector against the connector contract (`docs/reference/connector-contract.md`), and `tools/new_connector.py`, which makes a connector skeleton that passes it.
- `RunResult.failure_kind` (`none`, `transient`, `input` or `permanent`) and `RunResult.http_status`, and a `failure_kind` key in the run manifests of every connector.
- `meterline.http.FakeClock`, a test clock whose sleep moves it.
- The Apache License 2.0, declared in every package.

### Changed

- Every connector exits with 3 when the provider refuses a request for good (400, 401, 403, 404, 422), where it exited 1 or 2 before; 1 now means only "run again later".
- `record_version` 2 was redefined before the first public release: the field `org_id` is now `provider_account_id`, in the five record kinds, the run manifests, the raw files and state file that carried the name, the gold datasets, the policy and price files, and the schemas in `docs/schemas`. `record_version` stays 2 because nothing was published; there is no converter for files written with `org_id`.

## [meterline-anthropic 0.2.0] - 2026-11-01

### Added

- A connector for the Anthropic Claude Enterprise Analytics API, with `backfill`, `daily` and `rebuild` commands.

## [meterline-anthropic-admin 0.1.0] - 2026-11-01

### Added

- A connector for the Anthropic Admin Usage and Cost API of a Console organisation, with `backfill`, `daily` and `rebuild` commands, and a calibration script (`tools/calibrate_anthropic_admin.py`) that spends a known small amount and checks the connector finds it.

### Changed

- The option `--org-id` is now `--provider-account-id`, and the Python argument `org_id=` of `run()` is now `provider_account_id=`.

## [meterline-litellm 0.1.0] - 2026-11-01

### Added

- A connector for a LiteLLM proxy's admin API.

## [meterline-copilot 0.1.0] - 2026-11-01

### Added

- A connector for GitHub Copilot usage reports, billing and seats, at organisation or enterprise level.

## [meterline-sim 0.1.0] - 2026-11-01

### Added

- A generator of an invented organisation and its AI usage as silver records, with a ground truth.

## [meterline-gold 0.1.0] - 2026-11-01

### Added

- Per-person, per-group and per-audience datasets built from silver records, organisation files, a price list and a policy file, with group-size suppression, shadow scenarios, alert events and a static example dashboard.
- A JSON Schema for every dataset file a build writes, a generated dataset reference page, machine-readable audience rules (`audiences.json`) and `meterline_gold.servable(path, audience)`.

### Changed

- `GOLD_SCHEMA_VERSION` is 2.0 (it was 1.1): the column `org_id` of the gold datasets is now `provider_account_id`, the column `group_id` of `groups_month` and its audience forms is now `cell_kind`, and the option `--org` of `meterline-gold build` is now `--org-files`. A gold folder written by an earlier build is rebuilt from silver.

## [Repository v0.1] - 2026-11-01

### Added

- `docs/how-to/operate-daily.md`, the operations page for the daily job.
- This changelog, a security policy, contributing notes and a non-affiliation notice.

### Changed

- The daily job example (`examples/03-daily-job/`) runs `backfill` until a backfill has published, not merely until `state.json` exists, retries only a transient failure, stops and alerts on exit 2 or 3 and reports `failure_kind`.

### Known limits

- One connector had run against a live account: `meterline-anthropic-admin`, on a small Console organisation (one model, two days of usage). The others were built against the providers' documented response shapes and not run live.
