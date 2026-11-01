# 0005 The Anthropic connector keeps v1's zeros

- Status: accepted
- Decided: 2026-10-10, spec v2 section 4

## Context

The v1 format, written only by `meterline-anthropic`, stored a count the API sent as null, a counter a product
block did not have, and the tokens of a cost-only record as 0. v2 introduced the rule that a measure not
reported is null ([0004](0004-null-means-not-reported.md)). Data written in v1 is converted to v2 with
`meterline.convert_v1`, and `rebuild` re-creates v2 records from the raw responses.

## Alternatives considered

- **Apply the null rule to Anthropic too.** Consistent across connectors. Rejected: `convert_v1` cannot know
  which v1 zeros were really nulls (the raw page may be gone), so a converted file and a rebuilt file would
  differ for the same fact, and the migration could not be checked byte for byte.
- **Keep the zeros in this connector only (chosen).**

## Decision

`meterline-anthropic` writes 0 where v1 wrote 0. It is the one exception to the null rule, stated in the
Conventions table of [records.md](../records.md#conventions).

## Consequences

- `convert_v1` and a v2 `rebuild` give the same bytes, so the migration is checked exactly.
- Downside: in Anthropic records a reader cannot tell a real 0 from a value the API did not report; a reader
  that combines providers must know this exception.
- Downside: the exception has to be carried in every reader-facing document until a later record version
  removes it (which would be a breaking change, see [records.md](../records.md#compatibility)).

## Sources

`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md` sections 3.1 and 4.
