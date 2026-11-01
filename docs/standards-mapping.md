# Mapping to FOCUS and OpenTelemetry

meterline exports neither format (v2 spec, decision D5). This page says, for every field of the five record types,
where the value would go in each standard, or that the standard has no place for it. An exporter written later
follows this page. It replaces the table in section 9 of the v2 spec, which is frozen; the differences are under
[Differences from the v2 spec](#differences-from-the-v2-spec).

meterline records are per-actor usage analytics, not billing lines and not per-call telemetry. Neither standard
was written for them, so "no equivalent" is a normal answer below, not a defect.

## Standards checked

| Standard | Version checked | Status | Where | Checked |
|---|---|---|---|---|
| FOCUS, the FinOps Open Cost and Usage Specification | 1.4, Cost and Usage dataset | Published release, marked latest. The page gives no release date. | <https://focus.finops.org/docs/specification/v1-4/> (columns: <https://focus.finops.org/docs/specification/v1-4/columns/>) | 2026-10-17 |
| FOCUS 1.5 | working draft | Not ratified. Ratification is planned for 2026-12-03; until then every 1.5 name below may change. | <https://focus.finops.org/focus-1-5-release-scope/> | 2026-10-17 |
| OpenTelemetry semantic conventions for generative AI | no release yet; `main` at commit `f3c6d0c9bb66ebd474668a3a0be25c61226f02c2` (2026-10-14) | Every attribute and metric used here is Development (not stable). | <https://github.com/open-telemetry/semantic-conventions-genai>: attributes in `docs/registry/attributes/gen-ai.md`, token metrics in `docs/gen-ai/gen-ai-token-metrics.md`. The former page <https://opentelemetry.io/docs/specs/semconv/gen-ai/> is now a notice that the conventions moved there. | 2026-10-17 |
| OpenTelemetry semantic conventions, general registry (for `user.id`, `enduser.id`) | v1.44.0 | Development | <https://github.com/open-telemetry/semantic-conventions> | 2026-10-17 |

**Review by:** 2027-01-31, or earlier when FOCUS 1.5 is ratified or the OpenTelemetry GenAI conventions publish a
release. At review: fetch the pages again, update the versions and the commit above, and re-check every row that
names a FOCUS 1.5 draft column or an OpenTelemetry attribute.

## Decisions

Decided by the maintainer on 2026-10-16 for this mapping, unless the row says otherwise.

| # | Decision | Why |
|---|---|---|
| S1 | No exporter in this version; this page is the mapping. (v2 spec D5, 2026-10-10.) | An exporter is mechanical once the mapping is fixed; FOCUS token columns are only in the 1.5 draft. |
| S2 | `cost` maps to FOCUS `BilledCost` only when `cost_basis` is `billed`. A `cost` whose `cost_basis` is `consumption` or `estimate` is not `EffectiveCost`: at most it approximates it. An exporter writes such a cost to a custom column (`x_MeterlineCost`, with `x_MeterlineCostBasis`) and leaves `EffectiveCost` empty. | FOCUS `EffectiveCost` is the cost of a charge with the recognised share of related purchases (prepayments, commitments) included, and across covering and covered charges it must add up to `BilledCost`. A `consumption` figure is what a provider reports for usage but does not invoice (GitHub Copilot's per-person amounts, where included credits are pooled; Anthropic's analytics cost); an `estimate` is priced by a third party from a price list (LiteLLM). A FOCUS reader that took either as `EffectiveCost` would add it to invoice totals. |
| S3 | `cost_list` maps to `ListCost` as an approximation, labelled as such. | FOCUS defines `ListCost` as `ListUnitPrice` times `PricingQuantity`; meterline carries the provider's list-price amount, without the unit price or the pricing quantity. |
| S4 | `period_end` is inclusive in meterline and `ChargePeriodEnd` is exclusive in FOCUS: `ChargePeriodEnd` = `period_end` + 1 day, at 00:00 UTC. | Both standards' rules, unchanged from the v2 spec. |
| S5 | Tokens stay in four disjoint buckets. OpenTelemetry `gen_ai.usage.input_tokens` = `tokens_input_uncached` + `tokens_cache_read` + `tokens_cache_write` when all three are set, else `tokens_input_total`. A null bucket is left out, never written as 0. In the FOCUS 1.5 draft each bucket is one row (`TokenDirection`, `TokenCacheAction`), so the rows of one record add up to its input and output. | OpenTelemetry counts cache reads and writes inside input tokens; meterline keeps them apart so they can be priced apart. |
| S6 | The cache-write split by lifetime (`tokens_cache_write_5m`, `tokens_cache_write_1h`) has no equivalent in either standard; an exporter keeps it in custom columns or attributes. | Neither standard names a cache lifetime. |
| S7 | `model` maps to `gen_ai.response.model`, not `gen_ai.request.model`. In FOCUS 1.4 it goes to `Tags`, not to `SkuId`; the 1.5 draft adds `ModelId` (inside `SkuPriceDetails`). | The sources report the model that served the usage, which is the response model; what the caller asked for is not in any source. `SkuId` is the provider's SKU, which none of the sources report. |
| S8 | Actors: `actor_id` maps to OpenTelemetry `user.id` (`enduser.id` for `end_user` records) and to FOCUS 1.4 `Tags` (1.5 draft: `PrincipalId`). Both carry personal data and an exporter must treat them as such. | No GenAI attribute names a person; the general registry's `user.id` and `enduser.id` do. |
| S9 | Seats and allocation: `SeatSnapshot` and `SeatAssignment` hold counts and holders, not money, and map to no FOCUS cost column. A seat price spread over its holders (what `meterline-gold` computes as `seat_cost`) is an allocation; in FOCUS terms it would be described with the allocation columns `AllocatedMethodId` and `AllocatedMethodDetails` (FOCUS 1.3 and later). gold does not export FOCUS (gold spec, section 12). | FOCUS separates billed charges from how a cost is split afterwards. |
| S10 | `provider` values: `anthropic` is an OpenTelemetry well-known `gen_ai.provider.name` value; `litellm` and `github_copilot` are custom values, which the conventions allow. | Unchanged from the v2 spec. |

## UsageRecord

| Field | FOCUS 1.4 (Cost and Usage) | OpenTelemetry GenAI | Note |
|---|---|---|---|
| `record_version` | no equivalent | no equivalent | Version of meterline's format. |
| `provider` | `ServiceProviderName` | `gen_ai.provider.name` | S10. For `litellm` the proxy is not the provider of the model; see `model_provider`. |
| `provider_account_id` | `BillingAccountId` (approximation) | no equivalent | The provider account the source reports; for LiteLLM a label chosen by the user. |
| `scope` | `SubAccountId` when not `""` | no equivalent | `""` for every connector so far. |
| `grain` | no equivalent | no equivalent | Implied by the charge period in FOCUS. |
| `period_start` | `ChargePeriodStart` | no equivalent | OpenTelemetry records a time per call, not a period. |
| `period_end` | `ChargePeriodEnd` (+1 day, exclusive) | no equivalent | S4. |
| `actor_kind` | no equivalent | no equivalent | Decides `user.id` or `enduser.id` (S8). |
| `actor_id` | `Tags` (1.5 draft: `PrincipalId`) | `user.id`, or `enduser.id` for `end_user` | S8, personal data. |
| `actor_email`, `actor_login` | `Tags` | no equivalent | Personal data; an exporter should leave them out unless the reader needs them. |
| `actor_deleted` | no equivalent | no equivalent | |
| `product` | `ServiceName`; `ServiceCategory` = "AI and Machine Learning" | no equivalent | `gen_ai.operation.name` is the kind of call (chat, embeddings), not the product. |
| `model` | `Tags` (1.5 draft: `ModelId`) | `gen_ai.response.model` | S7. |
| `model_provider` | `ServiceProviderName` when the record comes from a proxy | `gen_ai.provider.name` when the record comes from a proxy | Who served the model. |
| `tokens_input_uncached` | no token column (1.5 draft: a row with `TokenDirection` Input, `TokenCacheAction` Uncached) | no single attribute: `gen_ai.usage.input_tokens` minus the two cache attributes | S5. |
| `tokens_cache_read` | no token column (1.5 draft: Input, Read) | `gen_ai.usage.cache_read.input_tokens` | S5. |
| `tokens_cache_write` | no token column (1.5 draft: Input, Write) | `gen_ai.usage.cache_write.input_tokens` | S5. |
| `tokens_cache_write_5m`, `tokens_cache_write_1h` | no equivalent | no equivalent | S6, a gap in both standards. |
| `tokens_input_total` | no token column (1.5 draft: the sum of the Input rows) | `gen_ai.usage.input_tokens` | S5. |
| `tokens_output` | no token column (1.5 draft: a row with `TokenDirection` Output) | `gen_ai.usage.output_tokens` | Reasoning included, as in OpenTelemetry. |
| `tokens_reasoning` | no equivalent | `gen_ai.usage.reasoning.output_tokens` | A subset of output in both. |
| `requests` | `ConsumedQuantity` with a requests unit, on its own row | no attribute: one span per request | |
| `quantities` | `ConsumedQuantity` with `ConsumedUnit` = the unit | no equivalent | `PricingQuantity` and `PricingUnit` are not produced: no source gives the provider's pricing unit. |
| `cost` | `BilledCost` when `cost_basis` is `billed`; otherwise custom column `x_MeterlineCost` | no equivalent | S2. OpenTelemetry defines no cost. |
| `cost_list` | `ListCost` (approximation) | no equivalent | S3. |
| `currency` | `BillingCurrency` | no equivalent | |
| `cost_basis` | custom column `x_MeterlineCostBasis` | no equivalent | Decides the cost column (S2). |
| `is_final` | no equivalent | no equivalent | The nearest idea is `BillingPeriodStatus` in FOCUS's separate Billing Period dataset. |
| `cost_only` | no equivalent | no equivalent | |
| `extras` | custom columns (`x_` prefix) | no equivalent | Source-specific numbers. |
| `pulled_at`, `data_refreshed_at`, `batch_id` | no equivalent | no equivalent | Provenance of the record; an exporter may keep them as custom columns. |

The token counters of OpenTelemetry (`gen_ai.client.inference.usage.input_tokens`, `.output_tokens`,
`.cache_read.input_tokens`, `.cache_write.input_tokens`, `.reasoning.output_tokens`) count per call; a
`UsageRecord` is their sum over one day (or month) for one actor, product and model. The metric
`gen_ai.client.token.usage` with the attribute `gen_ai.token.type`, which earlier versions used, is not in the
documents at the commit checked.

## ActivityRecord

Activity counts are not cost and not per-call telemetry: neither standard has a place for them.

| Field | FOCUS 1.4 (Cost and Usage) | OpenTelemetry GenAI | Note |
|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end`, `actor_kind`, `actor_id`, `actor_email`, `actor_login`, `product`, `pulled_at`, `data_refreshed_at`, `batch_id` | as in `UsageRecord` | as in `UsageRecord` | |
| `sessions`, `messages`, `requests` | no equivalent | no equivalent | `gen_ai.conversation.id` identifies one conversation on a span; it does not count them. |
| `suggestions_shown`, `suggestions_accepted`, `lines_added`, `lines_removed`, `edits_accepted`, `edits_rejected`, `commits`, `pull_requests` | no equivalent | no equivalent | |
| `counters` | no equivalent | no equivalent | Source-specific counts. |

## SeatSnapshot

| Field | FOCUS 1.4 (Cost and Usage) | OpenTelemetry GenAI | Note |
|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end`, `pulled_at`, `data_refreshed_at`, `batch_id` | as in `UsageRecord` | as in `UsageRecord` | |
| `seats`, `pending_invites`, `pending_cancellations` | no equivalent | no equivalent | Counts, not money (S9). A seat fee, when a source bills one, is a charge row of its own. |
| `daily_active`, `weekly_active`, `monthly_active` | no equivalent | no equivalent | |

## SeatAssignment

| Field | FOCUS 1.4 (Cost and Usage) | OpenTelemetry GenAI | Note |
|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end`, `actor_kind`, `actor_id`, `actor_email`, `actor_login`, `pulled_at`, `data_refreshed_at`, `batch_id` | as in `UsageRecord` | as in `UsageRecord` | |
| `plan` | no equivalent | no equivalent | |
| `assigned_at`, `last_active_at`, `last_authenticated_at`, `pending_cancellation_date` | no equivalent | no equivalent | |
| `granted_by` | no equivalent | no equivalent | Who grants the seat, not who pays for it. A cost split over holders is an allocation (S9). |

## OrgTotal

| Field | FOCUS 1.4 (Cost and Usage) | OpenTelemetry GenAI | Note |
|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end`, `product`, `model`, `model_provider` | as in `UsageRecord` | as in `UsageRecord` | `model` `""` means all models: no model value. |
| `tokens_input_uncached`, `tokens_cache_read`, `tokens_cache_write`, `tokens_cache_write_5m`, `tokens_cache_write_1h`, `tokens_input_total`, `tokens_output`, `tokens_reasoning`, `requests`, `quantities`, `extras` | as in `UsageRecord` | as in `UsageRecord` | Organization-wide, including usage no actor accounts for. |
| `cost`, `cost_list`, `currency`, `cost_basis`, `is_final` | as in `UsageRecord` | as in `UsageRecord` | Copilot organization billing is `billed`, so it maps to `BilledCost` (S2). |
| `pulled_at`, `data_refreshed_at`, `batch_id` | as in `UsageRecord` | as in `UsageRecord` | |

## FOCUS columns meterline does not produce

Of the 65 Cost and Usage columns of FOCUS 1.4, an exporter would leave these empty or set a constant, because no
source gives the value:

- prices and pricing units: `ListUnitPrice`, `ContractedUnitPrice`, `ContractedCost`, `PricingQuantity`,
  `PricingUnit`, `PricingCategory`, `SkuId`, `SkuPriceId`, `SkuPriceDetails`, `SkuMeter`, and the `PricingCurrency*`
  columns;
- charge description: `ChargeCategory` (an exporter would write `Usage`), `ChargeClass`, `ChargeDescription`,
  `ChargeFrequency`;
- billing and invoice: `BillingPeriodStart`, `BillingPeriodEnd`, `BillingAccountName`, `BillingAccountType`,
  `InvoiceId`, `InvoiceDetailId`, `InvoiceIssuerName`, `SubAccountName`, `SubAccountType`;
- commitments and reservations: the `CommitmentDiscount*` columns, `CommitmentProgramEligibilityDetails`,
  `ContractApplied`, `CapacityReservationId`, `CapacityReservationStatus`;
- resources and location: `ResourceId`, `ResourceName`, `ResourceType`, `RegionId`, `RegionName`,
  `AvailabilityZone`, `HostProviderName`, `ServiceSubcategory`;
- allocation: `AllocatedMethodId`, `AllocatedMethodDetails`, `AllocatedResourceId`, `AllocatedResourceName`,
  `AllocatedTags` (see S9).

## Differences from the v2 spec

Section 9 of the v2 spec mapped `cost` to `EffectiveCost` for every basis but `billed`, and `model` to `SkuId` or a
tag. This page maps a `consumption` or `estimate` cost to a custom column instead (S2), labels `cost_list` as an
approximation of `ListCost` (S3), maps `model` to a tag only (S7), adds the fields the spec did not map, and cites
the OpenTelemetry conventions at their new place and at a fixed commit. The change is recorded in
`docs/dev/change-list-meterline-v2.md`.
