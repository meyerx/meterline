# 0006 Records are JSON lines, with Parquet as an option

- Status: accepted
- Decided: 2026-10-09, in the v1 spec ("one JSON line per record, plus optional Parquet"); kept in v2
- Recorded here: 2026-10-17 (the v1 spec stated the choice without alternatives; those below were written down
  on that date)

## Context

Records are written once a day per connector, in small batches, and read by programs of every kind: a script,
a notebook, a warehouse load, gold. Every batch must be reproducible byte for byte by `rebuild`, and a reviewer
should be able to read a record without tools.

## Alternatives considered

- **CSV.** Universal. Rejected: no types (a decimal string and a number look the same), no nested maps for
  `quantities`, `extras` and `counters`, and quoting rules that differ between tools.
- **Parquet only.** Typed, compressed and fast to query. Rejected as the only format: binary files cannot be
  read or diffed by eye, the bytes depend on the writer library's version, and it needs `pyarrow`, a large
  dependency, for every reader and writer.
- **A database (SQLite or a server).** Queries for free. Rejected: a file per batch is simpler to keep, copy,
  back up and rebuild, and the revision rule (latest batch per key) needs no database.
- **JSON lines, sorted, with optional Parquet beside each file (chosen).**

## Decision

Each batch of each record kind is one `records/<kind>/<batch_id>.jsonl` file: one record per line, keys
sorted, lines sorted by record key, LF line ends. With `--parquet` a `.parquet` file with the same columns sits
beside it.

## Consequences

- The same records always give the same bytes; a batch can be read with any JSON parser and diffed with `git
  diff`.
- The core package needs no dependency; Parquet is an extra.
- Downside: JSON lines are larger and slower to query than Parquet; a large reader is expected to load them
  into its own store.
- Downside: Parquet needs a fixed decimal scale, so its amounts are `decimal128(38, 10)` and a value with more
  than ten decimals does not fit. The byte-for-byte rebuild checks compare the JSON lines files.

## Sources

`docs/dev/history/meterline-anthropic-SPEC-v1.0-2026-10-09.md` section 1; `docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md`
sections 1 and 3.8.
