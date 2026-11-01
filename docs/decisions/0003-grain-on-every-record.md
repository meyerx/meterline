# 0003 Every record names its grain

- Status: accepted
- Decided: 2026-10-10, spec v2 section 3.1
- Recorded here: 2026-10-17 (the spec states the field and its rule; the alternatives below were written down
  on that date)

## Context

Most sources report per day, but some report per calendar month (GitHub per-person billing) and some only over
multi-day windows. v1 records were all daily, and a reader inferred the period from the dates.

## Alternatives considered

- **Daily records only.** Simplest for readers. Rejected: monthly billing cannot be split into days without
  inventing a split, and window-only sources could not be held at all.
- **Infer the period from `period_start` and `period_end`.** No new field. Rejected: a reader adding records
  could not tell a monthly figure from a daily one with the same start date without checking every end date,
  and validation could not reject a record whose dates do not match its intent.
- **An explicit `grain` field, `day`, `month` or `window` (chosen).**

## Decision

Every record has `grain`. `day`: the start equals the end. `month`: the first to the last day of one calendar
month. `window`: any other range. Validation rejects dates that do not match the grain, and `grain` is part of
the record key.

## Consequences

- A reader can filter to one grain before summing, and a monthly and a daily record of the same start date
  never collide on the key.
- Downside: a reader must never add records of different grains; nothing in the format stops a reader that
  ignores the field.
- Downside: a `month` record may be written while its month is still running, so its `period_end` may lie in
  the future, and it is revised as the month goes on.

## Sources

`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md` section 3.1; `docs/dev/analysis/meterline-v2-analysis.md` section 2.5,
item 6.
