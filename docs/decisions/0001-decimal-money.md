# 0001 Money and credits are Decimal, never binary floating point

- Status: accepted
- Decided: 2026-10-09, in the v1 spec ("no float on the cost path"); restated in spec v2 section 2
- Recorded here: 2026-10-17 (the v1 spec stated the rule without alternatives; those below were written down
  on that date)

## Context

Costs come from provider APIs as decimal strings in cents (Anthropic) or as JSON numbers (GitHub billing,
LiteLLM `spend`). Records are compared against the provider's own totals (validation check 8 of
[records.md](../records.md#validation)), revisions are detected by comparing a record's content byte for byte,
and `rebuild` must reproduce the published files exactly.

## Alternatives considered

- **Binary floating point (`float`).** The default numeric type of JSON parsers and of most tools. Rejected:
  `0.1 + 0.2` is not `0.3` in binary, so sums of many small costs drift from the provider's total, and the same
  value can print differently after a sum, which breaks byte-for-byte rebuilds and revision detection.
- **Integer minor units (cents).** Exact and fast. Rejected: providers report fractions of a cent (LiteLLM
  estimates, per-token prices), and credits have no fixed minor unit, so the scale would differ per field.
- **Decimal (chosen).** Exact decimal arithmetic at whatever precision the source sent.

## Decision

Every amount of money and every credit quantity is a `Decimal` from the moment it is parsed
(`json.loads(..., parse_float=Decimal)`) to the moment it is written, as a string. A test scans the source of
every package for `float` on the cost path.

## Consequences

- Sums and comparisons with provider totals are exact; a rebuild gives the same bytes.
- Downside: every reader must parse amounts as decimals, not as JSON numbers; a spreadsheet or a script that
  reads the strings as floats loses the guarantee on its side.
- Downside: Parquet needs a fixed scale; the writer uses `decimal128(38, 10)`, so a value with more than ten
  decimals does not fit (see the core README's Known limits).
- Downside: a provider that sends binary-float noise (LiteLLM sums `spend` as floats) keeps that noise; checks
  therefore treat a difference below 0.000001 as zero for that connector.

## Sources

`docs/dev/history/meterline-anthropic-SPEC-v1.0-2026-10-09.md` (units, check 7); `docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md`
section 2; `docs/dev/analysis/meterline-v2-analysis.md` section 3, last row of the gap table.
