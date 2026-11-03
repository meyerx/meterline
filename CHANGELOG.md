# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and each package follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`export/vX.Y` names an export of the repository, not a package version.

## [meterline 0.3.0] - 2026-11-02

### Added

- `meterline.run.data_folder_lock`, the lock every connector takes on its data folder, and contract rule R17 for it.
- `meterline.run.auto_command` and `meterline.run.final_through`, which decide what `auto` runs and the last final day of a data folder; the run manifest gains an optional field, `requested`.
- Contract rules R18 (`auto`) and R19 (`complete_through`) in `meterline.conformance`; `Connector` gains `complete_through` and `is_final`. R09 now also runs `rebuild` over manifests without `requested`.

## [meterline-anthropic 0.2.1] - 2026-11-02

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-anthropic-admin 0.1.1] - 2026-11-02

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-copilot 0.1.1] - 2026-11-02

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-litellm 0.1.1] - 2026-11-02

### Added

- `auto`, a command of the connector: `backfill` until a backfill has published in the data folder, `daily` afterwards. It needs `--since`, which it uses only when it backfills. The run manifest gains an optional field, `requested` (`auto` when `auto` chose the command, else `null`); a manifest without it is still read by `rebuild`.
- `complete_through(data_dir)`: the last UTC day the connector holds as final in that data folder (`None` before a first published backfill).
- A run takes an exclusive lock on `<data folder>/runs/.lock`; a second run on the same folder stops at once with exit 1.

### Changed

- Requires `meterline>=0.3`.

## [meterline-gold 0.2.0] - 2026-11-02

### Added

- Three small accessors, `write_parquet_text`, `dataset_forms` and `policy_privacy`, that `meterline-job` composes the published folder from (internal-stable, not part of the public API).

## [meterline-job 0.1.0] - 2026-11-02

### Added

- `meterline-job`: the daily job as a command (`meterline-job run` and `meterline-job check`), from `examples/03-daily-job`. It runs each connector's `auto` command, then builds silver and gold, and prints one JSON status line per step. Each step can run alone, a gold failure exits 4 on its own (the connectors keep 0 to 3), `parquet` and `invoice` reach gold, and a leftover build `.tmp` folder is cleared.
- It also locks the gold folder, so two jobs on one folder do not run at once.
- The `publish` step. Each run builds gold into `builds/<date>` (then `-2`, `-3`), never over an existing folder; a month is frozen by copy into `frozen/<month>` with a `SHA256SUMS` file once every source has final data for it and `freeze_after_days` have passed (or forced by `freeze_cap_extra_days`); `published` is a link to an assembled `published-<time>` folder, swapped in at once after the checksums, gold's A09 check and a permission check; manager and person folders restart at a change of the hashing key; `central/published.json` and a `freshness.json` per audience folder say how fresh it is (schemas in `docs/schemas`). New `[publish]` settings. A gold failure no longer leaves a day's folder that blocks a rerun.
- Docs: the delivery rule (a month is refreshed until it is frozen, then delivered once) is written the same way in the gold README, `PRIVACY.md`, `THREAT-MODEL.md` and the operations page; the erasure steps no longer rotate the hashing key; the dashboard example serves `published/`.

### Known limits

- Tested against the connectors' fake APIs only; it is not a connector and does not run through the connector kit.

## [Unreleased]

### Added

- `meterline`: the v2 record format (usage, activity, seat snapshot, seat assignment and organisation total records), their validation, a converter for v1 files, and JSONL and Parquet writers.
- `meterline-anthropic`: a connector for the Anthropic Claude Enterprise Analytics API, with `backfill`, `daily` and `rebuild` commands.
- `meterline-anthropic-admin`: a connector for the Anthropic Admin Usage and Cost API of a Console organisation, with `backfill`, `daily` and `rebuild` commands, and a calibration script (`tools/calibrate_anthropic_admin.py`) that spends a known small amount and checks the connector finds it.
- `meterline-litellm`: a connector for a LiteLLM proxy's admin API.
- `meterline-copilot`: a connector for GitHub Copilot usage reports, billing and seats, at organisation or enterprise level.
- `meterline-sim`: a generator of an invented organisation and its AI usage as silver records, with a ground truth.
- `meterline-gold`: per-person, per-group and per-audience datasets built from silver records, organisation files, a price list and a policy file, with group-size suppression, shadow scenarios, alert events and a static example dashboard.
- `meterline-gold`: a JSON Schema for every dataset file a build writes, a generated dataset reference page, machine-readable audience rules (`audiences.json`) and `meterline_gold.servable(path, audience)`.
- `meterline`: `meterline.conformance`, a public test kit that checks a connector against the connector contract (`docs/reference/connector-contract.md`), and `tools/new_connector.py`, which makes a connector skeleton that passes it.
- `meterline`: `RunResult.failure_kind` (`none`, `transient`, `input` or `permanent`) and `RunResult.http_status`, and a `failure_kind` key in the run manifests of every connector.
- `docs/how-to/operate-daily.md`, the operations page for the daily job, and `meterline.http.FakeClock`, a test clock whose sleep moves it (also `meterline_copilot.testing.FakeClock`).
- The Apache License 2.0, declared in every package.
- This changelog, a security policy, contributing notes and a non-affiliation notice.

### Changed

- The daily job example (`examples/03-daily-job/`) runs `backfill` until a backfill has published, not merely until `state.json` exists, retries only a transient failure, stops and alerts on exit 2 or 3 and reports `failure_kind`.
- Every connector exits with 3 when the provider refuses a request for good (400, 401, 403, 404, 422), where it exited 1 or 2 before; 1 now means only "run again later".
- `meterline`, every connector and `meterline-gold`: `record_version` 2 was redefined before the first public release: the field `org_id` is now `provider_account_id`, in the five record kinds, the run manifests, the raw files and state file that carried the name, the gold datasets, the policy and price files, and the schemas in `docs/schemas`. `record_version` stays 2 because nothing was published; there is no converter for a v2 line that carries the old name. Records written before this change are re-created with each connector's `rebuild` command (no API call); a raw file, a state file or a run manifest written before it is still read. `meterline.convert_v1` still reads the v1 name and writes the new one.
- `meterline-anthropic-admin`: the option `--org-id` is now `--provider-account-id`, and the Python argument `org_id=` of `run()` is now `provider_account_id=`.
- `meterline-gold`: `GOLD_SCHEMA_VERSION` is 2.0 (it was 1.1): the column `org_id` of the gold datasets is now `provider_account_id`, the column `group_id` of `groups_month` and its audience forms is now `cell_kind`, and the option `--org` of `meterline-gold build` is now `--org-files`. A gold folder written by an earlier build is rebuilt from silver. The `org:` cell id prefix, the `org/` folder of organisation files and the `org_totals` record kind are unchanged.

### Known limits

- No connector has been run against a live account yet; they are built against the providers' documented response shapes.
