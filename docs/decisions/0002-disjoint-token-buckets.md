# 0002 Tokens are four disjoint buckets

- Status: accepted
- Decided: 2026-10-10, spec v2 section 3.1 (option A of the v2 analysis)

## Context

Providers count input tokens differently. Anthropic reports uncached input, cache reads and cache writes
separately; LiteLLM's `prompt_tokens` includes the cache figures for some providers; OpenAI and the
OpenTelemetry conventions report an input total that includes cached tokens. The v1 format had five
mandatory Anthropic-shaped token fields, which the new sources could not fill honestly.

## Alternatives considered

- **Keep v1's five mandatory fields.** Rejected: sources that do not report a field would have to send fake
  zeros, and an inclusive input count would be stored under a name that means exclusive.
- **One inclusive input total plus "of which cached".** Close to OpenAI and OpenTelemetry. Rejected: every
  reader would have to subtract to get uncached input, and a source that reports only the parts would have to
  add them first.
- **Four disjoint buckets plus an optional total (chosen).** Uncached input, cache read, cache write, output;
  `tokens_input_total` when a source reports the whole input.

## Decision

`tokens_input_uncached + tokens_cache_read + tokens_cache_write` is the whole input and `tokens_output` the
output, all nullable. When all three input buckets and the total are set, validation checks that they add up.
A source whose input includes cache gives `uncached = input - cache read - cache write`.

## Consequences

- A sum of any bucket across providers counts each token once; the buckets match both v1 and the FOCUS 1.5
  draft's token columns.
- Downside: the record has more nullable fields, and every reader must handle null.
- Downside: the subtraction relies on the source's own semantics. For LiteLLM with OpenAI-style providers it
  is inferred, not verified; a negative result is recorded as null and listed under `token_anomalies`.

## Sources

`docs/dev/analysis/meterline-v2-analysis.md` section 3 (token row) and section 4 (option A);
`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md` section 3.1 and open question 1.
