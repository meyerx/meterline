# meterline

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

The common record format for AI usage data: record types, validation, and JSONL and Parquet writers. No network and no provider knowledge. Provider connectors, such as [`meterline-anthropic`](https://github.com/meyerx/meterline/blob/main/packages/meterline-anthropic/README.md), emit these records.

Python 3.10 or later. No runtime dependencies; `pyarrow` is an optional extra for Parquet.

**Status:** the v2 record format, its validation and its writers are built, and the connectors, the simulator and gold use them. See [Known limits](#known-limits).

```sh
pip install meterline                  # core
pip install "meterline[parquet]"       # adds pyarrow
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline                  # core
pip install -e "packages/meterline[parquet]"       # adds pyarrow
pip install -e "packages/meterline[dev]"           # adds the test tools
```

To try the format without a provider, generate invented records with the simulator and read them back:

```sh
pip install -e packages/meterline-sim
meterline-sim generate --preset tiny --seed 7 --out sim-out
python -c "import glob; from meterline import UsageRecord, read_jsonl; f = sorted(glob.glob('sim-out/*/records/usage/*.jsonl'))[0]; print(len(read_jsonl(f, UsageRecord)), 'usage records in', f)"
```

## Records

Five record classes, each with the key that identifies a record across batches:

| Class | Record key (used for revisions) |
|---|---|
| `UsageRecord` | provider, provider_account_id, scope, grain, period_start, actor_kind, actor_id, product, model |
| `ActivityRecord` | provider, provider_account_id, scope, grain, period_start, actor_kind, actor_id, product |
| `SeatSnapshot` | provider, provider_account_id, scope, period_start |
| `SeatAssignment` | provider, provider_account_id, scope, period_start, actor_id |
| `OrgTotal` | provider, provider_account_id, scope, grain, period_start, product, model |

All are frozen, keyword-only dataclasses; `record_version` is always 2. Dates are `date` (UTC, `period_end` inclusive), timestamps are timezone-aware `datetime`, amounts are `Decimal` or `None`. Every field, with its type, unit, nullability and meaning, is in the [record reference](https://github.com/meyerx/meterline/blob/main/docs/records.md); the code is `src/meterline/records.py`. The format is specified in section 3 of the [v2 spec](https://github.com/meyerx/meterline/blob/main/docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md).

## Use

```python
from datetime import date
from meterline import UsageRecord, batch_failures, latest_per_key, read_jsonl, validate, write_jsonl

validate(record, today=date.today())          # raises RecordError on any rule of spec 3.1 and 3.7 (grain, token sums, cost basis, future dates, negative numbers, ...)
batch_failures(records, today)                # [] or messages: record rules plus duplicate keys and mixed org-total rows
write_jsonl(records, "records/usage/batch.jsonl")   # sorted by key, keys sorted, LF line ends: deterministic bytes
rows = read_jsonl("records/usage/batch.jsonl", UsageRecord)
newest = latest_per_key(rows_from_all_batches)      # {key: record}, latest batch_id wins
```

Import from the package root: every public name is in `meterline.__all__`. Two submodule routes are also kept stable, because the connectors import from them: `meterline.records` (the five record classes, `validate`, `batch_failures`, `latest_per_key`) and `meterline.writers` (`read_jsonl`, `write_jsonl`, `write_parquet`). A name with a leading underscore is internal.

- `to_dict()` / `from_dict()` convert to and from plain dicts. Money is written as a string with at least two decimals (`Decimal("412.800000")` becomes `"412.80"`); quantities and decimal extras as strings without trailing zeros (`"12.5"`). Timestamps are written as UTC with `+00:00`; `from_dict` requires a timezone. A nullable field that is missing reads as `None`; a line with no `record_version` is a v1 line and is refused.
- `convert_v1(line, kind)` and `convert_v1_batch(path)` turn v1 lines (all Anthropic's) into the v2 lines `meterline-anthropic` writes; the v1 file is not changed. To upgrade a data directory, move the v1 files out of `records/<kind>/` and rename each `.v2.jsonl` to `<batch_id>.jsonl`; a connector reads every `*.jsonl` there as v2.
- `actor_cost_failures(usage, org_totals)` is the check that one actor's cost on a day never exceeds the organization's, applied only within one provider, org, scope, grain, day, `cost_basis` and currency, never counting `end_user` records.
- `latest_per_key` takes the record with the greatest `batch_id`. Batch ids sort chronologically (`20261009T060000Z`). On a tie, the later item wins.
- `write_parquet(records, path, cls=None)` writes Decimal columns as `decimal128(38, 12)`; the `quantities` and `extras` maps become Arrow maps (extras values as decimals). Pass `cls` to take the schema from an empty list. Without `pyarrow` it raises a `RuntimeError` that names the `parquet` extra.

## Known limits

The limits of the core package, grouped by kind.

**Reading records.** `from_dict`, and so `read_jsonl`, converts integer and boolean fields with Python's `int()` and `bool()` and does not reject other JSON types: a string `"false"` in a boolean field reads as true. Files written by meterline never hold such values; a hand-edited file or one from another writer can.

**Parquet.** Decimals are written as `decimal128(38, 12)`. The format keeps at most 12 decimals in a money or quantity figure (`meterline.MAX_DECIMALS`): `validate` refuses more, so a record that validates always writes. The JSON lines file holds every digit it was given.

**Revisions.** The format has no deletion marker. A record key that a source stops reporting keeps its last record as the latest, so a reader cannot tell a value that was removed from one that did not change (the LiteLLM README gives an example).

## Public interface

What later releases keep working, under the compatibility policy in [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md#compatibility):

- the record format: every field in [`docs/records.md`](https://github.com/meyerx/meterline/blob/main/docs/records.md), and its JSON Schemas in [`docs/schemas/`](https://github.com/meyerx/meterline/blob/main/docs/schemas);
- the names in `meterline.__all__`, importable from the package root; the names the connectors import from `meterline.records`, `meterline.writers` and `meterline.http` stay importable from those three modules too. `meterline.http` holds the HTTP plumbing every connector shares (request throttle, retry back-off, `Retry-After` reading and capping, secret masking); it imports no HTTP library, so the core package keeps no dependencies.

Everything else is internal and may change in any release: every other module, and any name with a leading underscore.

## Tests

```sh
python -m pytest -q packages/meterline
```
