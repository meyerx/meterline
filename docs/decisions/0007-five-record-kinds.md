# 0007 Five record kinds, not one generic measure record

- Status: accepted
- Decided: 2026-10-10, spec v2 (option A of the v2 analysis, and decision D10 for `SeatAssignment`)

## Context

v1 had four record kinds shaped by the Anthropic API. GitHub Copilot and LiteLLM brought credits, new
activity counters, seat lists and new kinds of actor, and five more tools were checked against the format.

## Alternatives considered

- **One long record per measure** (option B): a `Measure` record keyed by provider, organisation, day, actor,
  product, model and measure, close to the FOCUS 1.5 draft. Never needs a schema change for a new measure.
  Rejected: the simplest question ("cost per person per month") becomes a grouped, pivoted query, and every
  consumer and check of v1 would have changed.
- **Keep v1 and add a fifth record for credits** (option C). Breaks nothing in the Anthropic connector.
  Rejected: LiteLLM still would not fit (fake zeros or a sixth shape), and a reader would have to union two
  record types to answer "cost per person".
- **Widen the v1 records and add a per-person seat record (chosen, option A).** `UsageRecord`,
  `ActivityRecord`, `SeatSnapshot`, `OrgTotal` and the new `SeatAssignment`.

## Decision

Five record kinds, each with its own key. A source-specific measure with no field goes into a map (`extras`,
`counters`, `quantities`) instead of a new field or a new kind.

## Consequences

- One row per fact, readable by a spreadsheet or a dashboard; the FOCUS long form can be generated from it.
- Downside: the records have many nullable fields, and a new common measure is a format change for every
  reader.
- Downside: one change to v1 was unavoidable: a rename, a derived cache-write total and five activity fields
  moved into `counters`, with a converter for v1 files.

## Sources

`docs/dev/analysis/meterline-v2-analysis.md` section 4 (options A, B and C);
`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md` section 1 (D10) and section 3.
