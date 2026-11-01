# 0004 A measure a source does not report is null, never 0

- Status: accepted
- Decided: 2026-10-10, spec v2 section 3.1

## Context

Some sources report a measure, some do not, and some report it only when an administrator turns logging on.
A zero and a missing value mean different things: "nobody used it" against "we do not know".

## Alternatives considered

- **Fill missing measures with 0.** Every field always has a number, and sums never meet null. Rejected: a
  missing measure would read as zero use, and an average or a share computed over providers would be wrong
  without warning (the v2 analysis calls these "fake zeros").
- **Leave the field out of the line.** Rejected as a rule on its own: a reader cannot tell "not reported" from
  "written by an older version that did not have the field". (A nullable field missing from a line does read
  as null, for compatibility; see [records.md](../records.md#compatibility).)
- **Null means not reported, 0 means reported zero (chosen).**

## Decision

Every measure is nullable. A source that does not report a measure, or sends it as null, gives null; a source
that reports zero gives 0.

## Consequences

- A sum or a count can say how many values it is missing, and a dashboard can show "not reported" instead of 0.
- Downside: every reader must handle null, and a naive sum in a tool that treats null as an error fails.
- Downside: one connector is an exception for compatibility ([0005](0005-anthropic-keeps-v1-zeros.md)).

## Sources

`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md` section 3.1; `docs/dev/analysis/meterline-v2-analysis.md` section 2.5
item 6, section 3 (token row) and section 4 (option C, its "fake zeros").
