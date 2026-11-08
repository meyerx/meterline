# Gold datasets

Generated from `meterline_gold` by `python -m meterline_gold.contract docs/schemas docs/reference/gold-datasets.md`; do not edit by hand. A test fails when this page differs from what the code generates.

This page lists every dataset a gold build writes, with its audience, grain, key, columns and the rule for which cost column to sum. The same facts are machine-readable in `docs/schemas/gold-dataset-*.schema.json` (one schema per file form) and `docs/schemas/gold-audiences.json` (who may be served which file). Every file is a CSV whose first column is `schema_version`; money is written with at least two decimals, a ratio with four, and an empty cell means no value. Which reader may be served which file is in the gold package README, section "What a build writes". Three kinds of cost figure appear below and must not be added together: billed, consumption and reference cost; the cookbook section [Billed, consumption and reference cost](../how-to/finops-kpis.md#billed-consumption-and-reference-cost) explains them with a do and a don't.

Units: `currency` (an amount in the reference currency, or the provider's own where the column says so), `tokens`, `count`, `ratio`, `date`, `month`, `key` (an id or pseudonymous key, not a quantity), `text` and `flag` (`true` or `false`).

## Compatibility

What may change in a gold dataset without breaking a reader, and what a reader must do. The record format has the same kind of rule ([records, Compatibility](../records.md#compatibility)).

**Non-breaking** (the minor part of `GOLD_SCHEMA_VERSION` rises):

- a new dataset;
- a new column, appended after the existing ones (the columns that were there keep their name, type and order);
- a new value in an open list: a text column that holds a status, a reason, a provider or a unit takes values from a list that may grow.

**Breaking** (a new major `GOLD_SCHEMA_VERSION`):

- a column renamed, removed or retyped;
- a dataset removed, or its grain changed (its key or what one row stands for).

**A reader**:

- selects columns by name, never by position;
- ignores a column it does not know;
- fails on a column it needs that is missing, naming the column and the dataset (a missing column is not an empty cell: an empty cell means no value, a missing column means the file is not what the reader expects);
- treats a value it does not know in an open list as "other" and does not fail;
- reads `schema_version`, the first column of every file, before the rest: the same major part means this rule applies, a different major part means the reader stops and says which version it read and which it supports.

The JSON schemas in `docs/schemas` describe one version exactly (the columns in file order, no others): use them to check a file of that version, not as a loader that must accept the next one.

Retired names: `GOLD_SCHEMA_VERSION` 2.0 is the first major version. Before the first public release the column `org_id` was renamed `provider_account_id` and the column `group_id` was renamed `cell_kind`, and the option `--org` of `meterline-gold build` became `--org-files`; a gold folder written by the 1.x builds is rebuilt from silver.

## `people`

People. One row per person and validity period.

- **Audiences and files:** central (`central/people.csv`).
- **Key:** `person_key`, `valid_from`.
- **Schema:** `gold-dataset-people.schema.json`.
- **Joins:** `unit_id` to `units.unit_id`; `manager_key` to `people.person_key`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `person_key` | key | no | Pseudonymous person key: a keyed hash of the person id, stable for one hashing key. |
| `valid_from` | date | no | First day the row is valid. |
| `valid_to` | date | yes | Last day the row is valid; empty if open. |
| `unit_id` | key | no | Organisation unit id from the organisation files. |
| `manager_key` | key | yes | person_key of the person's manager on the attribution day. |
| `function` | text | yes | Function of the person or unit, from the organisation files. |
| `level` | text | yes | Level of the person, from the organisation files. |
| `cost_center` | text | yes | Cost centre of the person or unit, from the organisation files. |
| `population` | text | no | Population label of the person, from the organisation files. |
| `attrs` | text | yes | JSON object of the person's kept attributes. |

**Which cost column to sum.** No cost column.

## `units`

Units. One row per organisation unit and validity period.

- **Audiences and files:** central (`central/units.csv`).
- **Key:** `unit_id`, `valid_from`.
- **Schema:** `gold-dataset-units.schema.json`.
- **Joins:** `parent_id` to `units.unit_id`; `head_key` to `people.person_key`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `unit_id` | key | no | Organisation unit id from the organisation files. |
| `valid_from` | date | no | First day the row is valid. A unit has one row per validity period: take the row valid on the day. See [column traps](../how-to/finops-kpis.md#column-traps). |
| `valid_to` | date | yes | Last day the row is valid; empty if open. A join on unit_id alone repeats people: check both ends of the range. See [column traps](../how-to/finops-kpis.md#column-traps). |
| `dimension` | text | no | Dimension of the unit tree the unit belongs to. |
| `key` | key | no | Short key of the unit within its dimension. |
| `name` | text | yes | Display name of the unit. |
| `kind` | text | yes | Kind of the unit, such as team or division. |
| `parent_id` | key | yes | unit_id of the parent unit; empty for a root. |
| `function` | text | yes | Function of the unit, from the organisation files. |
| `cost_center` | text | yes | Cost centre of the person or unit, from the organisation files. |
| `head_key` | key | yes | person_key of the head of the unit. |

**Which cost column to sum.** No cost column.

## `identity_map`

Identity map. One row per provider actor and validity period.

- **Audiences and files:** central (`central/identity_map.csv`).
- **Key:** `provider`, `actor_kind`, `actor_key`, `valid_from`.
- **Schema:** `gold-dataset-identity_map.schema.json`.
- **Joins:** `person_key` to `people.person_key`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `person_key` | key | yes | Pseudonymous person key; empty when method is non_person. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `method` | text | no | How the identity map matches the actor: login, email, id or non_person. |
| `actor_kind` | text | no | What the actor is: user, proxy user, end user, service account, API key or unassigned; mixed if merged records differ. |
| `actor_key` | key | yes | Keyed hash of the provider's own actor id, for an actor that is not resolved to a person. |
| `valid_from` | date | no | First day the row is valid. |
| `valid_to` | date | yes | Last day the row is valid; empty if open. |

**Which cost column to sum.** No cost column.

## `directory`

Directory. One row per person with a person_month row; the only file with names and emails.

- **Audiences and files:** delivery (`delivery/directory.csv`).
- **Key:** `person_key`.
- **Schema:** `gold-dataset-directory.schema.json`.
- **Joins:** `person_key` to `people.person_key`.

The columns of the delivery form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `person_key` | key | no | Pseudonymous person key: a keyed hash of the person id, stable for one hashing key. |
| `given_name` | text | yes | Given name of the person. |
| `family_name` | text | yes | Family name of the person. |
| `email` | text | yes | Email address of the person. |

**Which cost column to sum.** No cost column.

## `person_day`

Person per day. One row per day, provider, provider account, person or actor, product and model.

- **Audiences and files:** central (`central/person_day.csv`); person (`person/{person_key}/person_day.csv`).
- **Key:** `day`, `provider`, `provider_account_id`, `person_key`, `actor_key`, `product`, `model`.
- **Schema:** `gold-dataset-person_day.schema.json`.
- **Joins:** `person_key` to `people.person_key`; `actor_key` to `identity_map.actor_key`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `day` | date | no | Calendar day, YYYY-MM-DD (UTC). |
| `month` | month | no | Calendar month, YYYY-MM. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `provider_account_id` | key | no | Provider account label from the silver records; empty only where seats are matched on provider alone. |
| `person_key` | key | yes | Pseudonymous person key; empty when the actor is not resolved to a person. |
| `actor_key` | key | yes | Keyed hash of the provider's actor id; empty for a resolved person with one account. |
| `identity_status` | text | no | How the actor was resolved: resolved, non_person or unresolved. |
| `identity_method` | text | yes | How the identity map matched the actor; mixed if merged records differ. |
| `actor_kind` | text | no | What the actor is: user, proxy user, end user, service account, API key or unassigned; mixed if merged records differ. |
| `product` | text | yes | Product within the provider; empty when the provider has none. |
| `model` | text | yes | Model name as the provider reports it; empty when the record has none. |
| `model_tier` | text | yes | Tier of the model in the price list, such as premium; empty for an unknown model. |
| `requests` | count | yes | Number of requests. |
| `tokens_input_uncached` | tokens | yes | Input tokens not read from or written to a cache. |
| `tokens_cache_read` | tokens | yes | Input tokens read from a cache. |
| `tokens_cache_write` | tokens | yes | Input tokens written to a cache. |
| `tokens_cache_write_5m` | tokens | yes | Cache-write tokens in the 5-minute cache. |
| `tokens_cache_write_1h` | tokens | yes | Cache-write tokens in the 1-hour cache. |
| `tokens_input_total` | tokens | yes | All input tokens: uncached, cache read and cache write. |
| `tokens_output` | tokens | yes | Output tokens. |
| `credits` | count | yes | AI credits consumed, for providers that meter in credits. |
| `cost_source` | currency | yes | Cost as the provider reports it, in cost_source_currency and on the cost_source_basis. |
| `cost_source_basis` | text | yes | What cost_source is: billed, consumption or estimate; mixed if the rows differ. |
| `cost_source_currency` | text | yes | ISO 4217 currency of cost_source. |
| `cost_ref` | currency | yes | Reference cost of the row; empty when any merged record could not be priced. |
| `cost_ref_status` | text | no | How cost_ref was priced: priced, included, unpriced or unknown_model. |
| `is_final` | flag | no | True when the provider will no longer revise the figure. |
| `source_records` | count | no | Number of silver records merged into the row. |
| `model_provider` | text | yes | Provider that served the model, when it differs from provider (a proxy such as litellm routes calls to a provider); mixed if the merged records differ; empty when the record does not say. |

**Which cost column to sum.** `model_provider` is the provider that served the model, where a proxy routes calls on. Sum `cost_ref` to total or compare across providers and models: it prices every row from one price list in one currency, and is empty when a record could not be priced (see `cost_ref_status`). Sum `cost_source` only within one `cost_source_basis` and one `cost_source_currency`: billed, consumption and estimated amounts do not add up.

## `person_day_activity`

Person activity per day. One row per day, provider, provider account, person or actor and product.

- **Audiences and files:** central (`central/person_day_activity.csv`).
- **Key:** `day`, `provider`, `provider_account_id`, `person_key`, `actor_key`, `product`.
- **Schema:** `gold-dataset-person_day_activity.schema.json`.
- **Joins:** `person_key` to `people.person_key`; `actor_key` to `identity_map.actor_key`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `day` | date | no | Calendar day, YYYY-MM-DD (UTC). |
| `month` | month | no | Calendar month, YYYY-MM. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `provider_account_id` | key | no | Provider account label from the silver records; empty only where seats are matched on provider alone. |
| `person_key` | key | yes | Pseudonymous person key: a keyed hash of the person id, stable for one hashing key. |
| `actor_key` | key | yes | Keyed hash of the provider's own actor id, for an actor that is not resolved to a person. |
| `identity_status` | text | no | How the actor was resolved: resolved, non_person or unresolved. |
| `product` | text | yes | Product within the provider; empty when the provider has none. |
| `sessions` | count | yes | Number of sessions. |
| `messages` | count | yes | Number of messages. |
| `requests` | count | yes | Number of requests. |
| `suggestions_shown` | count | yes | Suggestions shown to the person. |
| `suggestions_accepted` | count | yes | Suggestions the person accepted. |
| `lines_added` | count | yes | Lines of code added. |
| `lines_removed` | count | yes | Lines of code removed. |
| `edits_accepted` | count | yes | Edits the person accepted. |
| `edits_rejected` | count | yes | Edits the person rejected. |
| `commits` | count | yes | Commits made. |
| `pull_requests` | count | yes | Pull requests opened. |
| `source_records` | count | no | Number of silver records merged into the row. |

**Which cost column to sum.** No cost column.

## `person_month`

Person per month. One row per person with an employed day in the month.

- **Audiences and files:** central (`central/person_month.csv`); person (`person/{person_key}/person_month.csv`).
- **Key:** `month`, `person_key`.
- **Schema:** `gold-dataset-person_month.schema.json`, `gold-dataset-person-person_month.schema.json`.
- **Joins:** `person_key` to `people.person_key`; `unit_id` to `units.unit_id`; `manager_key` to `people.person_key`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `person_key` | key | no | Pseudonymous person key: a keyed hash of the person id, stable for one hashing key. |
| `month_status` | text | no | complete for a finished month, running for the month in progress. |
| `attribution_day` | date | no | Last employed day of the month: the day whose organisation row attributes the person. |
| `unit_id` | key | no | Organisation unit id from the organisation files. |
| `manager_key` | key | yes | person_key of the person's manager on the attribution day. |
| `function` | text | yes | Function of the person or unit, from the organisation files. |
| `level` | text | yes | Level of the person, from the organisation files. |
| `cost_center` | text | yes | Cost centre of the person or unit, from the organisation files. |
| `population` | text | yes | Population label of the person, from the organisation files. |
| `employed_days` | count | no | Days of the month on which the person is employed. |
| `active_days` | count | no | Days of the month with any usage or activity row. |
| `active_days_by_tool` | count | no | JSON object of tool (provider or provider/product) to the person's active days with it. |
| `requests` | count | yes | Sum of the person's requests in the month. |
| `tokens_input_total` | tokens | yes | All input tokens: uncached, cache read and cache write. |
| `tokens_output` | tokens | yes | Output tokens. |
| `tokens_cache_read` | tokens | yes | Input tokens read from a cache. |
| `credits` | count | yes | AI credits consumed, for providers that meter in credits. |
| `cost_ref` | currency | no | Sum of the person's cost_ref over the month and all providers; 0.00 when nothing is priced. |
| `cost_ref_by_provider` | currency | no | JSON object of provider to the person's cost_ref at that provider. |
| `rows_unpriced` | count | no | Number of the person's person_day rows that could not be priced (cost_ref_status unpriced). |
| `rows_unknown_model` | count | no | Number of the person's person_day rows whose model is not in the price list. |
| `premium_share` | ratio | yes | Share of the person's cost_ref that went to premium-tier models. |
| `cache_rate` | ratio | yes | Cache-read tokens divided by all input tokens, where both are known. |
| `intensity_band` | text | no | Band of the person's month cost from the policy's bands. |
| `is_final` | flag | no | True when the provider will no longer revise the figure. |
| `projected_cost_ref` | currency | yes | Month-end projection of cost_ref for a running month; empty when complete. |

The person form (`gold-dataset-person-person_month.schema.json`) leaves out `manager_key`.

**Which cost column to sum.** `cost_ref` is the person's month total across providers; `cost_ref_by_provider` splits it. Seat fees are in no person's cost. `projected_cost_ref` projects a running month: do not add it to `cost_ref`.

## `seats_month`

Seats per month. One row per month, provider and provider account with seats.

- **Audiences and files:** central (`central/seats_month.csv`); exec (`exec/seats_month.csv`).
- **Key:** `month`, `provider`, `provider_account_id`.
- **Schema:** `gold-dataset-seats_month.schema.json`, `gold-dataset-exec-seats_month.schema.json`.
- **Joins:** `month` to `costs_month.month`; `provider` to `costs_month.provider`; `provider_account_id` to `costs_month.provider_account_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `provider_account_id` | key | yes | Provider account label; empty when the provider has none or several. |
| `seat_source` | text | no | Where the seat holders come from: assignments or identities. |
| `eval_day` | date | no | Day on which seats are evaluated for the month. |
| `seats_billed` | count | no | Distinct seat holders on any day of the month. |
| `seats_on_eval_day` | count | no | Seat holders on the evaluation day. |
| `active_seats` | count | no | Seat holders with usage or activity of the provider during the month. |
| `idle_seats` | count | no | Seat holders on the evaluation day with no usage in the idle window. |
| `seats_reported_max` | count | yes | Highest seat count the provider itself reported in the month. |
| `seat_price` | currency | yes | Price per seat per month from the price list. |
| `seat_cost` | currency | yes | Seat fee: seats_billed times seat_price. A fee, not usage. |
| `currency` | text | no | ISO 4217 reference currency of the row. |
| `month_status` | text | no | complete for a finished month, running for the month in progress. |
| `is_final` | flag | no | True when the month is complete. The seat fee of a running month is the full month's fee. |

The exec form (`gold-dataset-exec-seats_month.schema.json`) adds `hidden`; withholds values in `active_seats`, `idle_seats` and says so in `hidden`.

**Which cost column to sum.** `seat_cost` is a fee, not usage. Add it to usage only through `costs_month.total_cost_ref`.

## `pools_month`

Credit pools per month. One row per month, provider and provider account with a credit pool.

- **Audiences and files:** central (`central/pools_month.csv`); exec (`exec/pools_month.csv`).
- **Key:** `month`, `provider`, `provider_account_id`.
- **Schema:** `gold-dataset-pools_month.schema.json`, `gold-dataset-exec-pools_month.schema.json`.
- **Joins:** `month` to `seats_month.month`; `provider` to `seats_month.provider`; `provider_account_id` to `seats_month.provider_account_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `provider_account_id` | key | yes | Provider account label from the silver records; empty only where seats are matched on provider alone. |
| `seats_billed` | count | no | Distinct seat holders on any day of the month. |
| `included_per_seat` | count | yes | Credits included per seat each month. |
| `pool_credits` | count | no | Credit pool: seats_billed times included_per_seat. |
| `gross_credits` | count | yes | Credits billed in the month, before the pool is applied. |
| `pool_used_credits` | count | yes | Credits covered by the pool. |
| `overage_credits` | count | yes | Credits beyond the pool. |
| `overage_amount` | currency | yes | Billed amount for the credits beyond the pool. |
| `pool_used_share` | ratio | yes | pool_used_credits divided by pool_credits. |
| `budget_amount` | currency | yes | Budget for the provider and provider account from the policy. |
| `budget_state` | text | no | none without a budget, crossed once overage passed it, otherwise within. |
| `budget_crossed_on` | date | yes | First day on which the overage passed the budget. |
| `currency` | text | no | ISO 4217 reference currency of the row. |
| `is_final` | flag | no | True when the month is complete and the provider will no longer revise the billing rows. |
| `month_status` | text | no | complete for a finished month, running for the month in progress. |

The exec form (`gold-dataset-exec-pools_month.schema.json`) adds `hidden`; withholds values in `gross_credits`, `pool_used_credits`, `overage_credits`, `overage_amount`, `pool_used_share`, `budget_state`, `budget_crossed_on` and says so in `hidden`.

**Which cost column to sum.** `overage_amount` is billed money for credits beyond the pool and is part of no `cost_ref`. The credit columns are quantities, not currency.

## `costs_month`

Costs per month. One row per month, provider and provider account.

- **Audiences and files:** central (`central/costs_month.csv`); exec (`exec/costs_month.csv`).
- **Key:** `month`, `provider`, `provider_account_id`.
- **Schema:** `gold-dataset-costs_month.schema.json`, `gold-dataset-exec-costs_month.schema.json`.
- **Joins:** `month` to `reconciliation_month.month`; `provider` to `reconciliation_month.provider`; `provider_account_id` to `reconciliation_month.provider_account_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `provider_account_id` | key | yes | Provider account label from the silver records; empty only where seats are matched on provider alone. |
| `currency` | text | no | ISO 4217 reference currency of the row. |
| `usage_cost_source` | currency | yes | Sum of cost_source over the provider's usage rows. |
| `cost_source_basis` | text | yes | What cost_source is: billed, consumption or estimate; mixed if the rows differ. |
| `usage_cost_ref` | currency | no | Sum of cost_ref over the provider's usage rows. |
| `seat_cost` | currency | yes | Seat fee: seats_billed times seat_price. A fee, not usage. |
| `total_cost_ref` | currency | no | usage_cost_ref plus seat_cost. |
| `cost_basis_label` | text | yes | Plain-language label of the basis of usage_cost_source. |
| `invoice_usage` | currency | yes | Invoiced usage amount from the invoice file, if one was given. |
| `overlap_cost_ref` | currency | no | Part of usage_cost_ref that a proxy routed to a provider whose own connector is also in this build: those calls are in both providers' rows. 0 for every other row; empty where the row is hidden. |
| `month_status` | text | no | complete for a finished month, running for the month in progress. |
| `is_final` | flag | no | True when the month is complete and every usage row of it is final. |

The exec form (`gold-dataset-exec-costs_month.schema.json`) adds `hidden`; withholds values in `usage_cost_source`, `cost_source_basis`, `usage_cost_ref`, `total_cost_ref`, `overlap_cost_ref` and says so in `hidden`.

**Which cost column to sum.** For spend by provider and month sum `usage_cost_ref` (reference cost) or `total_cost_ref` (usage plus seats). Never add `seat_cost` to `total_cost_ref`: it is already inside. Sum `usage_cost_source` only within one `cost_source_basis`. A proxy row's `overlap_cost_ref` is the part of its cost that another provider's row also holds: subtract it before adding the two. A row with `is_final` false belongs to a running month, whose `seat_cost` is the full month's fee.

## `reconciliation_month`

Reconciliation per month. One row per month, provider and provider account.

- **Audiences and files:** central (`central/reconciliation_month.csv`).
- **Key:** `month`, `provider`, `provider_account_id`.
- **Schema:** `gold-dataset-reconciliation_month.schema.json`.
- **Joins:** `month` to `costs_month.month`; `provider` to `costs_month.provider`; `provider_account_id` to `costs_month.provider_account_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `provider_account_id` | key | no | Provider account label from the silver records; empty only where seats are matched on provider alone. |
| `currency` | text | no | ISO 4217 reference currency of the row. |
| `actor_cost_source` | currency | yes | Sum of cost_source allocated to actors. |
| `org_cost_source` | currency | yes | Organisation-wide cost the provider reports (OrgTotal records). |
| `org_cost_basis` | text | yes | cost_basis of the organisation totals; mixed if they differ. |
| `unallocated_cost_source` | currency | yes | org_cost_source minus actor_cost_source; empty when the bases differ. |
| `unreconciled_share` | ratio | yes | unallocated_cost_source divided by org_cost_source: the organisation remainder only. It is not the reference unallocated share (the cookbook KPI one_unallocated_share), which also counts the cost of non-person and unresolved actors. See [reference unallocated share](../how-to/finops-kpis.md#reference-unallocated-share). |
| `actor_credits` | count | yes | Credits allocated to actors. |
| `org_credits` | count | yes | Organisation-wide credits the provider reports. |
| `unallocated_credits` | count | yes | org_credits minus actor_credits. |
| `cost_ref` | currency | yes | Reference cost: usage priced from the price list you supplied, in the reference currency. |
| `ref_vs_source_gap` | currency | yes | cost_ref minus actor_cost_source; may be negative. |
| `ref_vs_source_share` | ratio | yes | Absolute ref_vs_source_gap divided by actor_cost_source. |
| `invoice_usage` | currency | yes | Invoiced usage amount from the invoice file, if one was given. |
| `invoice_usage_gap` | currency | yes | invoice_usage minus org_cost_source. |
| `invoice_seats` | currency | yes | Invoiced seat amount from the invoice file, if one was given. |
| `invoice_seats_gap` | currency | yes | invoice_seats minus seat_cost. |

**Which cost column to sum.** `org_cost_source` is the provider's organisation-wide total and `actor_cost_source` the part allocated to actors; compare them, do not add them. `cost_ref` is a different basis from both.

## `data_quality`

Data quality. One row per month, provider, provider account and metric.

- **Audiences and files:** central (`central/data_quality.csv`).
- **Key:** `month`, `provider`, `provider_account_id`, `metric`.
- **Schema:** `gold-dataset-data_quality.schema.json`.
- **Joins:** `provider` to `costs_month.provider`; `provider_account_id` to `costs_month.provider_account_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | yes | Calendar month, YYYY-MM; empty on the freshness metrics. |
| `provider` | text | no | Provider the usage or seats came from, as the silver records name it. |
| `provider_account_id` | key | no | Provider account label from the silver records; empty only where seats are matched on provider alone. |
| `metric` | text | no | Name of the data-quality metric. The metric unallocated_share is the not-a-person cost over the reference cost (cost_ref): it is not the reference unallocated share (the cookbook KPI one_unallocated_share), which is the one to report. See [reference unallocated share](../how-to/finops-kpis.md#reference-unallocated-share). |
| `value` | text | no | Value of the metric, written in the metric's own type (count, money, ratio or date). The metric sets the type, not the column: read metric before you cast or sum. See [column traps](../how-to/finops-kpis.md#column-traps). |

**Which cost column to sum.** `value` mixes counts, amounts, ratios and dates; read `metric` first and sum nothing across metrics.

## `groups_month`

Groups per month. One row per month and grid cell.

- **Audiences and files:** central (`central/groups_month.csv`); exec (`exec/groups_month.csv`); manager (`manager/{manager_key}/groups_month.csv`).
- **Key:** `month`, `cell_id`.
- **Schema:** `gold-dataset-groups_month.schema.json`, `gold-dataset-exec-groups_month.schema.json`, `gold-dataset-manager-groups_month.schema.json`.
- **Joins:** `parent_cell_id` to `groups_month.cell_id`; `cell_key` to `units.unit_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `cell_id` | key | no | Id of the grid cell: org:, unit:<unit_id> or attr:<attribute>=<value>|... |
| `cell_kind` | key | no | Kind of grid cell: org, unit or attr. |
| `cell_key` | key | yes | Cell id without its kind prefix. |
| `depth` | count | no | Depth of the cell in the grid: 0 for the organisation, 1 for a root unit. |
| `parent_cell_id` | key | yes | cell_id of the parent cell; empty for the organisation cell. |
| `suppressed` | flag | yes | True when the privacy algorithm withholds the cell's figures. |
| `suppression_reason` | text | yes | Why a cell is suppressed: k, differencing or secondary. |
| `persons` | count | yes | Number of attributed persons in the cell. |
| `active_persons` | count | yes | Persons with at least one active day; withheld when it would expose a few people. |
| `active_days` | count | yes | Sum of the cell's persons' active days. |
| `cost_ref` | currency | yes | Sum of cost_ref over the cell's persons. |
| `p50_cost_ref` | currency | yes | Median cost_ref per person in the cell (nearest rank). |
| `p75_cost_ref` | currency | yes | 75th percentile of cost_ref per person in the cell. |
| `p90_cost_ref` | currency | yes | 90th percentile of cost_ref per person in the cell. |
| `p99_cost_ref` | currency | yes | 99th percentile of cost_ref per person in the cell. |
| `top_decile_share` | ratio | yes | Share of the organisation's cost_ref spent by its top tenth of persons; organisation cell only. |
| `is_final` | flag | yes | True when the provider will no longer revise the figure. |

The exec form (`gold-dataset-exec-groups_month.schema.json`) adds `hidden`; leaves out `suppressed`, `suppression_reason`.

The manager form (`gold-dataset-manager-groups_month.schema.json`) adds `hidden`; leaves out `suppressed`, `suppression_reason`.

**Which cost column to sum.** A cell's `cost_ref` already sums its persons, and cells overlap: a unit cell contains its sub-unit cells. Sum the cells of one `cell_kind` at one `depth`, never across depths.

## `comparison_groups`

Comparison groups. One row per month and comparison cell.

- **Audiences and files:** central (`central/comparison_groups.csv`).
- **Key:** `month`, `compare_cell`.
- **Schema:** `gold-dataset-comparison_groups.schema.json`.
- **Joins:** `compare_cell` to `comparison.compare_cell`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `compare_cell` | key | no | Comparison cell id: the compare_by attributes and their values. |
| `persons` | count | no | Number of persons in the comparison cell. |
| `p50_cost_ref` | currency | yes | Median cost_ref per person in the cell (nearest rank). |
| `p75_cost_ref` | currency | yes | 75th percentile of cost_ref per person in the cell. |
| `p90_cost_ref` | currency | yes | 90th percentile of cost_ref per person in the cell. |

**Which cost column to sum.** No cost to sum: the cut points are quantiles, not totals.

## `comparison`

Own comparison. One row per month: the person's own cost and band.

- **Audiences and files:** person (`person/{person_key}/comparison.csv`).
- **Key:** `month`.
- **Schema:** `gold-dataset-comparison.schema.json`.
- **Joins:** `compare_cell` to `comparison_groups.compare_cell`.

The columns of the person form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `compare_cell` | key | no | Comparison cell id: the compare_by attributes and their values. |
| `own_cost_ref` | currency | no | The person's own month cost_ref. |
| `comparison_band` | text | yes | Where the person sits against the published cut points of their comparison cell. Covered by `hidden`. |
| `hidden` | flag | no | True when a privacy rule withheld at least one covered value of the row; empty values then mean withheld. |

**Which cost column to sum.** `own_cost_ref` is the person's own `cost_ref`; there is nothing to add across rows.

## `scenario_persons`

Scenario per person. One row per scenario, month and person with a cap.

- **Audiences and files:** central (`central/scenario_persons.csv`).
- **Key:** `scenario_id`, `month`, `person_key`.
- **Schema:** `gold-dataset-scenario_persons.schema.json`.
- **Joins:** `person_key` to `people.person_key`; `scenario_id` to `scenario_summary.scenario_id`; `month` to `scenario_summary.month`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `scenario_id` | key | no | Id of the shadow scenario from the policy. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `person_key` | key | no | Pseudonymous person key: a keyed hash of the person id, stable for one hashing key. |
| `cap` | currency | no | The person's spending cap in the scenario. |
| `cost_ref` | currency | no | The person's position: month-to-date cost_ref at the end of the month. |
| `stopped_on` | date | yes | First day the position passed the cap or envelope; empty if it never did. |
| `excess` | currency | no | How far the position at the end of the month is over the cap or envelope; 0.00 if not over. |
| `top_ups` | count | yes | Number of top-up amounts needed to cover the excess. |
| `top_up_total` | currency | yes | top_ups times the scenario's top-up amount. |

**Which cost column to sum.** `cost_ref` is the position at the end of the month, not a new cost; `excess` is not spend.

## `scenario_units`

Scenario per unit. One row per scenario, month and unit with an envelope.

- **Audiences and files:** central (`central/scenario_units.csv`).
- **Key:** `scenario_id`, `month`, `unit_id`.
- **Schema:** `gold-dataset-scenario_units.schema.json`.
- **Joins:** `unit_id` to `units.unit_id`; `scenario_id` to `scenario_summary.scenario_id`; `month` to `scenario_summary.month`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `scenario_id` | key | no | Id of the shadow scenario from the policy. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `unit_id` | key | no | Organisation unit id from the organisation files. |
| `envelope` | currency | no | The unit's spending envelope in the scenario. |
| `position` | currency | no | Month-to-date cost_ref of the unit at the end of the month. |
| `stopped_on` | date | yes | First day the position passed the cap or envelope; empty if it never did. |
| `excess` | currency | no | How far the position at the end of the month is over the cap or envelope; 0.00 if not over. |
| `top_ups` | count | yes | Number of top-up amounts needed to cover the excess. |
| `top_up_total` | currency | yes | top_ups times the scenario's top-up amount. |

**Which cost column to sum.** `position` is a month-to-date cost, `excess` a shadow figure; neither is added to spend.

## `scenario_summary`

Scenario summary. One row per scenario and month.

- **Audiences and files:** central (`central/scenario_summary.csv`); exec (`exec/scenario_summary.csv`).
- **Key:** `scenario_id`, `month`.
- **Schema:** `gold-dataset-scenario_summary.schema.json`, `gold-dataset-exec-scenario_summary.schema.json`.
- **Joins:** `scenario_id` to `scenario_persons.scenario_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `scenario_id` | key | no | Id of the shadow scenario from the policy. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `persons` | count | no | Number of persons with a cap in the scenario. |
| `persons_stopped` | count | no | Persons whose position passed their cap. |
| `persons_excess` | currency | no | Sum of the persons' excess. |
| `persons_top_up_total` | currency | yes | Sum of the persons' top_up_total. |
| `units` | count | no | Number of units with an envelope in the scenario. |
| `units_stopped` | count | no | Units whose position passed their envelope. |
| `units_excess` | currency | no | Sum of the units' excess. |
| `units_top_up_total` | currency | yes | Sum of the units' top_up_total. |

The exec form (`gold-dataset-exec-scenario_summary.schema.json`) adds `hidden`; leaves out `persons_excess`, `persons_top_up_total`, `units_stopped`, `units_excess`, `units_top_up_total`; withholds values in `persons_stopped` and says so in `hidden`.

**Which cost column to sum.** The `*_excess` and `*_top_up_total` columns are what the scenario would have done; they are never spend.

## `alert_events`

Alert events. One row per event; nothing is sent.

- **Audiences and files:** central (`central/alert_events.csv`).
- **Key:** `day`, `event_type`, `scenario_id`, `subject_kind`, `subject_key`, `threshold`.
- **Schema:** `gold-dataset-alert_events.schema.json`.
- **Joins:** `subject_key` to `people.person_key`, `units.unit_id`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `day` | date | no | Calendar day, YYYY-MM-DD (UTC). |
| `event_type` | text | no | breaker, spike, scenario_alert or proxy_overlap. |
| `scenario_id` | key | yes | Id of the shadow scenario from the policy. |
| `subject_kind` | text | no | person, unit or proxy. |
| `subject_key` | key | no | person_key or unit_id, as subject_kind says; for proxy, the proxy's provider and the provider it routes to, joined by a slash. |
| `value` | currency | no | Day cost or position that fired the event; for proxy_overlap, the amount counted twice. |
| `threshold` | currency | no | Limit the event crossed, in the reference currency. |
| `trailing_sum` | currency | yes | Sum of the day costs in the spike's trailing window; empty on other events. |
| `trailing_days` | count | yes | Days in the spike's trailing window; empty on other events. |

**Which cost column to sum.** `value` and `threshold` are day or position costs; do not sum events. A `proxy_overlap` event carries the amount that two providers' rows both count.

## `named_exceptions`

Named exceptions. One row per month, rule and selected person.

- **Audiences and files:** central (`central/named_exceptions.csv`); manager (`manager/{manager_key}/named_exceptions.csv`).
- **Key:** `month`, `rule_id`, `person_key`.
- **Schema:** `gold-dataset-named_exceptions.schema.json`.
- **Joins:** `person_key` to `people.person_key`; `manager_key` to `people.person_key`.

The columns of the central form, in file order:

| Column | Unit | May be empty | Description |
|---|---|---|---|
| `schema_version` | text | no | GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row. |
| `month` | month | no | Calendar month, YYYY-MM. |
| `rule_id` | key | no | Id of the named-exception rule from the policy. |
| `audience` | text | no | Audience the rule delivers to: central or manager. |
| `person_key` | key | no | person_key of the person the rule selected. |
| `manager_key` | key | yes | person_key of the person's manager; empty if none. |
| `condition` | text | no | What the rule tests: month_cost_ref_above, breaker_events_at_least or spike_events_at_least. |
| `threshold` | text | no | The rule's threshold, as written in the policy. |
| `value` | text | no | The person's value for the rule: a cost_ref or an event count, as condition says. |

**Which cost column to sum.** `value` is a cost for `month_cost_ref_above` and a count otherwise; do not sum across rules.
