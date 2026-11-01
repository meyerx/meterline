# Anthropic Admin API shapes

Provenance: both files were transcribed by hand from the Anthropic API reference pages
`organization/usage_report/retrieve_messages` and `organization/cost_report/retrieve` and from the usage and cost
guide, read on 2026-10-17. They are the
documented shapes. Mark [V]: read on the named page and transcribed as shown there.

Status: both endpoints became legacy on 2026-10-29. They are still supported and no sunset date is known. Their
successors, `GET /v1/organizations/analytics/usage` and `/cost`, answer in another shape and have no file here; the
move to them is tracked separately.

Synthetic data: every value is invented (ids such as `apikey_a` and `wrkspc_a`, models `model-a`, round numbers).
Only the keys, the nesting and the types follow the provider. Never commit a real response here.

| File | Endpoint | Source | Read on | Mark | Note |
|---|---|---|---|---|---|
| `usage_report_messages.json` | `GET /v1/organizations/usage_report/messages` | Anthropic API reference, `organization/usage_report/retrieve_messages`, and the usage and cost guide | 2026-10-17 | [V] | Token counts per bucket. `amount` is not here; cost is a separate endpoint. A grouping field that was not asked for is `null`. |
| `cost_report.json` | `GET /v1/organizations/cost_report` | Anthropic API reference, `organization/cost_report/retrieve`, and the usage and cost guide | 2026-10-17 | [V] | `amount` is a decimal string in cents. `model` and `token_type` appear when the query groups by `description`. |

The values the documentation leaves open (which fields are present when a query does not group by them, which are
null, how the cost is rounded) are learned from the first live response. The connector fails the batch on a
field it does not know instead of guessing; see Response shapes in the package README.
