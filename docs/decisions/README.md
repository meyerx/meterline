# Decision records

One short record per structural choice of the record format: what was decided, which alternatives were
weighed, what it costs, and when. Read them when a rule of [records.md](../records.md) looks odd, or before
proposing to change one.

| Record | Decision | Decided |
|---|---|---|
| [0001](0001-decimal-money.md) | Money and credits are `Decimal`, never binary floating point | 2026-10-09 (v1) |
| [0002](0002-disjoint-token-buckets.md) | Tokens are four disjoint buckets | 2026-10-10 (v2) |
| [0003](0003-grain-on-every-record.md) | Every record names its grain: `day`, `month` or `window` | 2026-10-10 (v2) |
| [0004](0004-null-means-not-reported.md) | A measure a source does not report is null, never 0 | 2026-10-10 (v2) |
| [0005](0005-anthropic-keeps-v1-zeros.md) | The Anthropic connector keeps v1's zeros, the one exception to 0004 | 2026-10-10 (v2) |
| [0006](0006-jsonl-with-optional-parquet.md) | Records are JSON lines, with Parquet as an option | 2026-10-09 (v1) |
| [0007](0007-five-record-kinds.md) | Five record kinds, not one generic measure record | 2026-10-10 (v2) |

Each record has the same parts: Context, Alternatives considered, Decision, Consequences (with the downsides),
and Sources. Some decisions were taken before this folder existed and were written down on 2026-10-17. Where
the original text named no alternative, the record says so and gives the alternatives as reconstructed then;
it does not pretend they were weighed at the time.

A new structural choice gets the next number. A record is never edited to change its decision: a later record
supersedes it and says so in its first line.
