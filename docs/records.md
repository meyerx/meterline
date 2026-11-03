# meterline record reference (v2)

Every field of the five record types, as the code names them (`packages/meterline/src/meterline/records.py`), with the source of each value per connector. These connectors write v2 records: `meterline-anthropic` (the Anthropic source column in each table below), `meterline-anthropic-admin` (the section [Anthropic Admin sources](#anthropic-admin-sources) below), `meterline-litellm` (the section [LiteLLM sources](#litellm-sources) below) and `meterline-copilot` (the section [GitHub Copilot sources](#github-copilot-sources) below; spec v2 sections 5 and 6). A record is one JSON line; the same fields are the columns of the optional Parquet file. The spec behind this page is `docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md`.

In code, import from the package root: `from meterline import UsageRecord, validate, read_jsonl` (every public name is in `meterline.__all__`). The connectors also import from two submodules, `meterline.records` (the five record classes, `validate`, `batch_failures`, `latest_per_key`) and `meterline.writers` (`read_jsonl`, `write_jsonl`, `write_parquet`); those two routes stay as they are. A name with a leading underscore is internal.

Why the format is as it is (Decimal money, disjoint token buckets, `grain`, null for not reported, JSON lines, five record kinds) is in the [decision records](decisions/README.md), each with the alternatives weighed and the costs accepted.

Where each field would go in FOCUS and in the OpenTelemetry GenAI conventions: [`standards-mapping.md`](standards-mapping.md).

Lines without `record_version` are v1 lines (Anthropic only). Only `meterline.convert_v1` and `meterline.convert_v1_batch` read them; meterline never writes v1 again.

## Conventions

| Topic | Rule |
|---|---|
| `record_version` | Integer, `2` in every record this version writes. What a reader does with another value: [Compatibility](#compatibility). |
| Dates | `date`, written `YYYY-MM-DD`, always UTC. `period_end` is inclusive. |
| `grain` | `day` (`period_start` equals `period_end`), `month` (first to last day of one calendar month; may end in the future while the month runs) or `window` (any other range). A record whose dates do not match its grain is rejected. A reader never adds records of different grains, because a month record already covers its days ([decisions/0003](decisions/0003-grain-on-every-record.md)). |
| Timestamps | `datetime`, timezone-aware, written in UTC with `+00:00`. |
| Money and credits | `Decimal`, written as a string. `cost`, `cost_list` have at least two decimals (`"412.80"`); `quantities` and `extras` Decimals have no trailing zeros (`"12.5"`). Never a float, because binary floating point cannot hold most decimal amounts exactly ([decisions/0001](decisions/0001-decimal-money.md)). |
| Counts | Integers. |
| Null | Nullable means "not reported", never `0`, so that a missing measure is never read as zero use ([decisions/0004](decisions/0004-null-means-not-reported.md)). A nullable field missing from a line reads as `null`. Any other missing field is an error. One exception: `meterline-anthropic` keeps v1's zeros (counts the API sends as null, counters a product block does not have, tokens of a cost-only record), so that converted v1 files and rebuilt files agree ([decisions/0005](decisions/0005-anthropic-keeps-v1-zeros.md)). |
| `scope` | A sub-division inside an organization that a source reports separately. `""` for every connector so far. |
| `provider_account_id` | Never empty, because it is part of every record key and an empty id would merge accounts; unique across levels (a connector that reads several levels prefixes the level). |
| `provider` | The system that reported the data (`anthropic`, `litellm`, `github_copilot`). `model_provider` is who served the model, when the source says so. |
| Order | A JSONL file is sorted by record key, keys sorted alphabetically, LF line ends: the same records always give the same bytes. |
| Never content | No record holds prompts, responses or message bodies, because they can hold anything a person typed and usage figures do not need them. |

### Actor kinds

`user` (a person the provider knows), `proxy_user` (the person a proxy charged the call to), `end_user` (a person named by the calling application), `service_account`, `api_key`, `unassigned` (usage the source attributes to no one; `actor_id` is `(unassigned)`). `proxy_user` and `end_user` records can describe the same traffic: never add them together.

### Tokens: four disjoint buckets

`tokens_input_uncached` + `tokens_cache_read` + `tokens_cache_write` is the whole input; `tokens_output` is the output. `tokens_input_total` is the whole input when a source reports it. Checked: when the three buckets and the total are all set, the total equals their sum; when `tokens_cache_write_5m` and `tokens_cache_write_1h` are both set, they sum to `tokens_cache_write`.

`tokens_cache_write_5m` and `tokens_cache_write_1h` are core fields, not Anthropic fields: the split of cache writes by cache lifetime is a property of prompt caching, and any provider whose cache has two lifetimes can fill them. Today only `meterline-anthropic` does; the other connectors leave them null. `tokens_reasoning` is core for the same reason and no connector fills it yet.

### Cost

`cost` (after discounts) and `cost_list` (at list price) are nullable. `currency` and `cost_basis` describe whichever is set: `currency` is `""` and `cost_basis` is null exactly when both are null. `cost_basis` is `billed` (from the provider's billing system), `consumption` (a usage figure the provider reports but does not invoice) or `estimate` (computed by a third party from a price list).

### Quantities, extras, counters

`quantities` maps a billing unit to a `Decimal` (`{"ai_credit": "12.5"}`), for units that are not money. `extras` maps a name to a number (integer, or a Decimal written as a string) for a source-specific measure with no field. `counters` (activity) maps a name to an integer. Keys are lower-case with underscores, and values are never strings that are not numbers, never nested maps. Each connector documents its own keys.

## Record key and revisions

| Record | Key |
|---|---|
| `UsageRecord` | `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `actor_kind`, `actor_id`, `product`, `model` |
| `ActivityRecord` | `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `actor_kind`, `actor_id`, `product` |
| `SeatSnapshot` | `provider`, `provider_account_id`, `scope`, `period_start` |
| `SeatAssignment` | `provider`, `provider_account_id`, `scope`, `period_start`, `actor_id` |
| `OrgTotal` | `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `product`, `model` |

**Revision rule: the record with the latest `batch_id` per key wins.** A re-pull of a period writes new records under a new `batch_id`, and only for keys whose content changed (every field except `pulled_at`, `data_refreshed_at` and `batch_id`). Earlier batches stay on disk and stay readable. A reader that wants the current state loads all batches of a kind and calls `meterline.latest_per_key`; batch ids sort chronologically, and on an equal `batch_id` the later item wins. The format has no deletion marker: a key that is no longer produced keeps its last record.

Files: `records/<kind>/<batch_id>.jsonl`, where kind is `usage`, `activity`, `seats`, `seat_assignments` or `org_totals`. With `--parquet`, a `.parquet` file with the same columns sits next to each `.jsonl` (maps become Arrow maps; `extras` values become decimals).

## UsageRecord

Tokens, quantities and cost for one actor, one period, one product, one model. `meterline-anthropic` builds it from `user_usage_report`, joined to `user_cost_report` on (user, day, product, model).

| Field | Type | Nullable | Meaning | Anthropic source |
|---|---|---|---|---|
| `record_version` | int | no | `2`. | Constant. |
| `provider` | string | no | The reporting system. | Constant `anthropic`. |
| `provider_account_id` | string | no | The provider account (here, the organization). Never empty. | Top-level `organization_id` of the response. |
| `scope` | string | no | Sub-division. | Constant `""`. |
| `grain` | string | no | `day`, `month` or `window`. | Constant `day`. |
| `period_start`, `period_end` | date | no | UTC, `period_end` inclusive. | Date part of the row's `starting_at`, for both. |
| `actor_kind` | string | no | See actor kinds. | Constant `user`. |
| `actor_id` | string | no | The source's id for the actor. Never empty. | `actor.user_id`. |
| `actor_email` | string | yes | Trimmed and lower-cased. | `actor.email_address`. |
| `actor_login` | string | yes | A user name or login. | Always null. |
| `actor_deleted` | bool | yes | The actor was deleted; null when the source does not say. | `actor.deleted` (false when absent). |
| `product` | string | no | As received; `""` when none. | Row `product`. |
| `model` | string | no | As received; `""` when none or all models. | Row `model`. |
| `model_provider` | string | yes | Who served the model. | Constant `anthropic`. |
| `tokens_input_uncached` | int | yes | Input not read from or written to cache. | `uncached_input_tokens` (0 when null). |
| `tokens_cache_read` | int | yes | Input read from cache. | `cache_read_input_tokens`. |
| `tokens_cache_write` | int | yes | Input written to cache. | Sum of the two below. |
| `tokens_cache_write_5m`, `tokens_cache_write_1h` | int | yes | Cache writes by lifetime. | `cache_creation.ephemeral_5m_input_tokens`, `.ephemeral_1h_input_tokens`. |
| `tokens_input_total` | int | yes | All input tokens. | Uncached + read + 5m + 1h. |
| `tokens_output` | int | yes | Output tokens, reasoning included. | `output_tokens`. |
| `tokens_reasoning` | int | yes | Reasoning tokens, a subset of output. | Always null. |
| `requests` | int | yes | Requests. | `requests`; null when absent, and on a cost-only record. |
| `quantities` | map | no | Non-money billing units. | `{}`. |
| `cost` | decimal | yes | Cost after discounts, major currency units. | `amount` of `user_cost_report`, divided by 100, summed over matching rows; null when no cost row matches. |
| `cost_list` | decimal | yes | Cost at list price. | `list_amount`, divided by 100, summed. |
| `currency` | string | no | ISO code; `""` when no cost. | `currency` of the cost row. |
| `cost_basis` | string | yes | `billed`, `consumption` or `estimate`. | `consumption` when `cost` or `cost_list` is set, else null. |
| `is_final` | bool | no | The connector's finality rule. | True when the period's month ended more than 7 days before the pull. |
| `cost_only` | bool | no | Cost arrived without usage. Its tokens are 0 in `meterline-anthropic`, null elsewhere. | Computed; such keys are also listed in the run manifest under `cost_only`. |
| `extras` | map | no | Source-specific numbers. | `{"web_search_requests": n}`, always present (0 included), from `server_tool_use.web_search_requests`. |
| `pulled_at` | datetime | no | When the run pulled the data. | The run's start time. |
| `data_refreshed_at` | datetime | yes | When the source says the data was last refreshed. | `data_refreshed_at` of the response. |
| `batch_id` | string | no | The run: `pulled_at` as `YYYYMMDDTHHMMSSZ`. Orders revisions. | Computed. |

## ActivityRecord

Activity counters for one actor, one period, one product. `meterline-anthropic` builds it from `users`: one record per product block with at least one non-zero count, plus one with product `all` for web searches. No cost, no `is_final`.

| Field | Type | Nullable | Meaning | Anthropic source |
|---|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end` | | | As in `UsageRecord`. | `provider_account_id` is the `organization_id` of the run's report responses; the day is the `date` the request asked for. |
| `actor_kind`, `actor_id`, `actor_email`, `actor_login` | | | As in `UsageRecord`. | `user` (kind), `user.id`, `user.email_address`, null. |
| `product` | string | no | The product block the counts come from (table below). | The block name in the user row. |
| `sessions` | int | yes | Distinct sessions. | `distinct_session_count` (Claude Code: `core_metrics.distinct_session_count`); 0 when the block has none. |
| `messages` | int | yes | Messages or prompts sent. | `message_count`; 0 for Claude Code. |
| `requests` | int | yes | Requests or agent calls. | Always null. |
| `suggestions_shown`, `suggestions_accepted` | int | yes | Suggestions shown, accepted. | Always null. |
| `lines_added`, `lines_removed` | int | yes | Lines written or removed by the AI and kept. | `core_metrics.lines_of_code.added_count`, `.removed_count`. |
| `edits_accepted`, `edits_rejected` | int | yes | Edit actions accepted, rejected. | `tool_actions.*.accepted_count`, `.rejected_count`, summed over the four edit tools. |
| `commits`, `pull_requests` | int | yes | Made through the tool. | `core_metrics.commit_count`, `.pull_request_count`. |
| `counters` | map | no | Counters with no core field. | Five keys, always present (0 included): `conversations` (`distinct_conversation_count`), `artifacts` (chat: `distinct_artifacts_created_count`; other blocks: `artifacts_created_count`), `skills_used` (`distinct_skills_used_count`), `connectors_used` (`distinct_connectors_used_count`), `web_searches` (`web_search_count`, only on product `all`). |
| `pulled_at`, `data_refreshed_at`, `batch_id` | | | As in `UsageRecord`. | `data_refreshed_at` is always null: the `users` endpoint gives none. |

In `meterline-anthropic` a null count in the API is read as 0, and the eight core counters it fills are 0 where a block has none, as in v1.

| `product` | Block in the `users` row |
|---|---|
| `chat` | `chat_metrics` |
| `claude_code` | `claude_code_metrics` |
| `cowork` | `cowork_metrics` |
| `claude_design` | `design_metrics` |
| `office_agent.excel`, `.outlook`, `.powerpoint`, `.word` | `office_metrics.excel`, `.outlook`, `.powerpoint`, `.word` |
| `science` | `science_metrics` |
| `chat_cowork_unified.chat` | `chat_cowork_unified_metrics.chat` |
| `chat_cowork_unified.sessions` | `chat_cowork_unified_metrics.sessions` |
| `all` | `web_search_count` (the whole user row; only when non-zero) |

These names are activity products. They are not the product values of `UsageRecord`, which come from the usage report and are kept as received.

## SeatSnapshot

Seats and active users for one organization on one day. Grain is always `day`. `meterline-anthropic` builds it from `summaries`.

| Field | Type | Nullable | Meaning | Anthropic source |
|---|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end` | | | As in `UsageRecord`. | `provider_account_id` is the `organization_id` of the run's report responses; the day is the date part of `starting_at`. |
| `seats` | int | yes | Seats assigned. | `assigned_seat_count` (null becomes 0). |
| `pending_invites` | int | yes | Invitations not yet accepted. | `pending_invite_count`. |
| `pending_cancellations` | int | yes | Seats set to end. | Always null. |
| `daily_active`, `weekly_active`, `monthly_active` | int | yes | Active users. | `daily_active_user_count`, `weekly_active_user_count`, `monthly_active_user_count`. |
| `pulled_at`, `data_refreshed_at`, `batch_id` | | | As in `UsageRecord`. | `data_refreshed_at` is always null. |

## SeatAssignment

One person holding a seat on one day, new in v2. Grain is always `day`, and the day is the UTC date of `pulled_at` (seat lists are snapshots). `meterline-anthropic` writes none: its API has no per-person seat list.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end` | | | As in `UsageRecord`. |
| `actor_kind` | string | no | `user`. |
| `actor_id`, `actor_email`, `actor_login` | string | id no, others yes | As in `UsageRecord`. |
| `plan` | string | yes | The seat's plan, as received. |
| `assigned_at` | datetime | yes | When the seat was assigned (the earliest, if several). |
| `last_active_at` | datetime | yes | Last activity the source knows. |
| `last_authenticated_at` | datetime | yes | Last sign-in the source knows. |
| `pending_cancellation_date` | date | yes | The day the seat will end, if cancelled. |
| `granted_by` | string | yes | The teams or organizations that grant the seat, sorted and joined with `,`. |
| `pulled_at`, `data_refreshed_at`, `batch_id` | | | As in `UsageRecord`. |

## OrgTotal

Organization-wide measures for one period, product and model, including usage no actor accounts for (such as API keys). A connector writes either per-model rows or one all-models row (`model` `""`) for a period and product, never both. `meterline-anthropic` builds it from `usage_report` and `cost_report`, one record per day bucket.

| Field | Type | Nullable | Meaning | Anthropic source |
|---|---|---|---|---|
| `record_version`, `provider`, `provider_account_id`, `scope`, `grain`, `period_start`, `period_end` | | | As in `UsageRecord`. | Top-level `organization_id`; the day is the date part of the bucket's `starting_at`. |
| `product` | string | no | `""` when the source gives none. | `""`. |
| `model` | string | no | `""` means all models. | `""`. |
| `model_provider` | string | yes | Who served the model. | `anthropic`. |
| `tokens_*`, `requests`, `quantities`, `extras` | | | The measure fields of `UsageRecord`. | Same sources, summed over the day's rows; `requests` is null when no row reports one. |
| `cost`, `cost_list`, `currency`, `cost_basis` | | | As in `UsageRecord`. | `amount` and `list_amount` of `cost_report`, divided by 100, summed; null when the day has no cost bucket; 0 for an empty bucket. |
| `is_final` | bool | no | As in `UsageRecord`. | Same month rule. |
| `pulled_at`, `data_refreshed_at`, `batch_id` | | | As in `UsageRecord`. | `data_refreshed_at`: the usage report's, else the cost report's. |

## Anthropic Admin sources

What `meterline-anthropic-admin` writes into each record (the package README has the commands, the limits and the calibration). `provider` is `anthropic`; `provider_account_id` is the label given with `--provider-account-id`; `scope` is `""`; `grain` is `day`. The cost is what the Console bills, so `cost_basis` is `billed`, `currency` is `USD` and `cost_list` is null. This API sees API-key usage only.

| Record | Written from | Notes |
|---|---|---|
| `UsageRecord`, `actor_kind` `api_key` | the usage report grouped by key, workspace, model and service tier, summed per day, key and model | `actor_id` is the `api_key_id`; `product` is `api`; `cost` is null (the cost report has no key dimension) |
| `UsageRecord`, `actor_kind` `unassigned` | the same rows whose `api_key_id` is null (Console playground use) | `actor_id` is `(unassigned)`; the rows of several workspaces are summed |
| `OrgTotal`, `product` `api` | the usage rows summed per day and model, and the token rows of the cost report | `cost` is `amount` / 100 summed per day and model; null when the model has no cost row |
| `OrgTotal`, `product` `api-web-search` or `api-code-execution` | the cost rows of that `cost_type` | `model` is `""`; token fields are null |

Fields of the usage and total records:

- `tokens_input_uncached` = `uncached_input_tokens`; `tokens_cache_read` = `cache_read_input_tokens`; `tokens_cache_write_5m` and `tokens_cache_write_1h` = the two `cache_creation` buckets; `tokens_cache_write` = their sum;
- `tokens_input_total` = uncached + read + write; `tokens_output` = `output_tokens`; `requests` and `tokens_reasoning` are null;
- `extras`: `web_search_requests` (from `server_tool_use`), and `priority_tokens` when a row has `service_tier` `priority`, because Priority Tier has no cost line.

The workspace id is not a record field: the format has no place for a text value there, so it stays in the raw files. A named key that appears in two workspaces on one day fails the batch. `is_final` is true when the day's bucket ended at least 48 hours before the pull. No `ActivityRecord`, `SeatSnapshot` or `SeatAssignment` comes from this API.

## LiteLLM sources

What `meterline-litellm` writes into each record (spec v2 section 5; the package README has the commands, the limits and the key fields of every record). `provider` is `litellm`; `provider_account_id` is the label given with `--proxy-name`; `scope` is `""`; `currency` is `USD`. A proxy's figures are an estimate: LiteLLM prices the calls from its own price list, so `cost_basis` is `estimate` and `cost_list` is null. `proxy_user` and `end_user` records describe the same traffic: never add them together.

| Record | Written from | Notes |
|---|---|---|
| `UsageRecord`, `actor_kind` `proxy_user` | each per-user view of the daily activity report, per date and model | `actor_email` from `/user/list` (null if absent); `model_provider` null (the daily view cannot tie a model to a provider) |
| `UsageRecord`, `actor_kind` `unassigned` | the global view minus the sum of the per-user views, per date and model | written only when a field is non-zero; a negative difference fails the run |
| `UsageRecord`, `actor_kind` `end_user` | each per-end-user view (`--end-users`, off by default) | `actor_login` is the end user's alias; no `unassigned` record is derived |
| `OrgTotal`, `grain` `day` | the global view's per-model breakdown, per date and model | per-model rows only; no all-models row |

Fields of the usage and total records:

- `tokens_input_total` = `prompt_tokens`; `tokens_cache_read` = `cache_read_input_tokens`; `tokens_cache_write` = `cache_creation_input_tokens`;
- `tokens_input_uncached` = total - read - write, or null when that is negative (the key is then listed in the run manifest under `token_anomalies`);
- `tokens_output` = `completion_tokens`; `requests` = `api_requests`; `cost` = `spend`;
- `extras`: `successful_requests`, `failed_requests`, and `flat_cost` when non-zero.

The 5m/1h cache split and `tokens_reasoning` are null. `is_final` is true when the day ended at least 2 full UTC days before the pull. No `ActivityRecord`, `SeatSnapshot` or `SeatAssignment` comes from LiteLLM.

## GitHub Copilot sources

What `meterline-copilot` writes into each record, field by field (spec v2 section 6.4). Endpoint letters: **A** the per-user usage report of one day (`users-1-day`), **B** the aggregate report of one day (`organization-1-day` or `enterprise-1-day`), **C** billing AI credits (`settings/billing/ai_credit/usage`), **D** seats (`copilot/billing/seats`), **E** the organization seat summary (`copilot/billing`, organization level only). `provider` is `github_copilot`, `scope` is `""`, `provider_account_id` is `org:<slug>` or `enterprise:<slug>`, `currency` is `USD`.

**Per-person figures are consumption, organization figures are billed.** A person's credits and monthly amounts are what GitHub reports for that person; included credits are pooled across the billing entity, so a person's discount is not their invoice (`cost_basis` `consumption`). Only the organization's billing per day and model is `billed`. For that reason the check "an actor's cost never exceeds the organization's" is not made for Copilot.

| Record | Written from | Fields |
|---|---|---|
| `UsageRecord`, product `copilot`, `grain` `day` | A, one per user row with `ai_credits_used` | `actor_id` = `user_id` as a string; `actor_login` = `user_login`; `actor_email` null; `model` `""`; `quantities` `{"ai_credit": ai_credits_used}`; `cost` = `cost_list` = credits x 0.01; `cost_basis` `consumption`; all token fields null; `is_final` when the day ended at least 4 full UTC days before the pull. |
| `UsageRecord`, products `copilot_cli` and `copilot_app`, `grain` `day` | A, when `totals_by_cli` or `totals_by_copilot_app` is present | `tokens_input_total` = `token_usage.prompt_tokens_sum`; `tokens_output` = `token_usage.output_tokens_sum`; the three input buckets null (GitHub does not split cache); `requests` = `request_count`; `quantities` `{}`; no cost. Credits for every surface stay on the `copilot` record, so credits and tokens add without double counting. |
| `UsageRecord`, `grain` `month` | C with `user`, one call per person and month; items summed per normalised product and model | `period_start`/`period_end` = the month's first and last day (the end may lie in the future); `actor_login` = the login asked for; `actor_id` = that person's `user_id` from a stored A row of the month, else the `assignee.id` of a D list pulled that month, else the login; `product` = item `product` lower-cased with runs of non-alphanumerics as `_`; `model` as received; `quantities` `{unit: grossQuantity}`; `cost_list` = `grossAmount`; `cost` = `grossAmount` - `discountAmount`; `cost_basis` `consumption`; `extras`: `discount_quantity`, `net_quantity`, `net_amount`, and `price_per_unit` when every merged item has the same price; `is_final` when the month ended more than 7 days before the pull. |
| `OrgTotal`, `grain` `day` | C without `user`, one call per day; items summed per normalised product and model | `quantities`, `cost_list` and `extras` as above; `cost` = `netAmount`; `cost_basis` `billed`; `is_final` by the month rule. |
| `ActivityRecord`, product = `feature` | A, one per `totals_by_feature` entry | `messages` = `user_initiated_interaction_count`; `suggestions_shown` = `code_generation_activity_count`; `suggestions_accepted` = `code_acceptance_activity_count`; `lines_added` = `loc_added_sum`; `lines_removed` = `loc_deleted_sum`; `counters`: `lines_suggested_to_add`, `lines_suggested_to_delete`; for features `copilot_cli` and `copilot_app`, `sessions` = `session_count` and `requests` = `request_count` of `totals_by_cli` / `totals_by_copilot_app`; other core counters null. |
| `ActivityRecord`, product `copilot` | A, one per user row that has at least one of the five fields | `counters`: `custom_agents_used`, `mcp_servers_used`, `plugins_used`, `skills_used`, `slash_commands_used` (from `distinct_*_use_count`); core counters null. |
| `SeatSnapshot` | D, E and B, combined per UTC day from every raw file of that day | `seats` = `total_seats` of the latest D pulled that day; `pending_invites` = `seat_breakdown.pending_invitation` and `pending_cancellations` = `seat_breakdown.pending_cancellation` of the latest E pulled that day (null at enterprise level); `daily_active`, `weekly_active`, `monthly_active` from B for that day. B is read on a later day, so the first snapshot of a day has null active counts and a later run writes a revision with them. |
| `SeatAssignment` | D, one per distinct `assignee.id`, day = UTC date of `pulled_at` | `actor_id` = id as a string; `actor_login` = `login`; `actor_email` null; `plan` = `plan_type`; `assigned_at` = earliest `created_at`; `last_active_at` = latest `last_activity_at`; `last_authenticated_at` = latest `last_authenticated_at`; `pending_cancellation_date` = latest non-null; `granted_by` = sorted `assigning_team.slug` values joined by `,` (null if none); `updated_at` is ignored. |

Every Copilot record about a person has `actor_kind` `user` and, where the field exists, `actor_deleted` null (GitHub does not say). Usage records have `cost_only` false. `model_provider` is null in usage records and organisation totals, because GitHub does not name who served a model. In `ActivityRecord`, `edits_accepted`, `edits_rejected`, `commits` and `pull_requests` are null: the reports carry no such counts.

Billing `unitType` values `credits`, `ai-credits` and `ai_credits` become the unit `ai_credit`; `requests` becomes `premium_request`; any other value is lower-cased with non-alphanumerics as `_` and listed in the run manifest under `unknown_units`. Breakdowns by IDE, language, model and agent, the `used_*` flags and the adoption phase stay in `raw/` and are not mapped.

## Validation

Before a connector publishes a batch, every record is checked (`meterline.validate`, `meterline.batch_failures`):

1. `period_end` is not before `period_start`, and the dates match `grain`.
2. No `period_start` in the future, and no `period_end` in the future except on `grain=month`.
3. No negative or non-finite number, in a field or in a map.
4. The token sums above hold.
5. `cost_basis` is null exactly when `cost` and `cost_list` are null, and `currency` is `""` in the same case.
6. `extras`, `counters` and `quantities` hold numbers only, with lower-case keys.
7. No duplicate key in the batch; an `OrgTotal` period and product has per-model rows or one all-models row, not both.
8. Record totals equal the totals of the raw responses (connector level).
9. An actor's cost on a day never exceeds the organization's cost, compared only between records of the same provider, org, scope, grain, day, `cost_basis` and `currency`, and never counting `end_user` records (`meterline.actor_cost_failures`).

A batch that fails any check publishes nothing.

### What validation does not check

Passing every check above means the records are consistent with the responses the connector read. It does not show:

- that a figure equals what the provider shows on its own dashboard or invoice: check 8 compares records with the raw responses, which are the provider's own figures as sent;
- that a period is complete: a page the API skipped, or a day the provider has not finished counting, passes when what was read adds up;
- that an identity is right: an email, login or id is taken as the provider sent it, and no actor is matched to a person;
- that the response shapes are the live ones: the shape checks compare the fake APIs with the transcribed files, not with the provider (see the [evidence levels](glossary.md#evidence-levels));
- that figures of different `cost_basis`, currency or grain are comparable: each check compares like with like only;
- that a later batch is more correct than an earlier one: the latest batch per key wins, whatever it says.

## Compatibility

This section is the contract between the code that writes records and the code that reads them. It applies to the five record types above. The JSON Schemas in [`docs/schemas/`](schemas/) are the same contract in machine-readable form.

### Changes that keep `record_version`

The format grows by expansion only. These changes keep `record_version` and need no change in a reader:

- a new nullable field: a line written before the field existed reads it as null, and an older reader ignores it;
- a new record type, in its own folder under `records/`: an older reader does not open that folder;
- a new key inside `quantities`, `extras` or `counters`: these maps are open;
- a new value of a field kept as received: `product`, `model`, `provider`, `model_provider`, `plan`, the unit names in `quantities`;
- any change to this page that changes no rule, including the order of the fields in its tables.

### Changes that need a new `record_version`

Any change that would make an existing reader misread a new line, or a new reader misread an old line:

- removing or renaming a field, or changing its type, unit or meaning;
- making a nullable field required;
- a new value in a closed list that readers check: `grain`, `actor_kind`, `cost_basis`;
- a change to the record key or to the revision rule;
- a change to how a value is written (dates, timestamps, decimals).

A new `record_version` ships with a converter from the previous one, as `convert_v1` does for v1, and is a breaking release of `meterline` (see below).

### What a reader does

| A line... | What `from_dict` and `read_jsonl` do |
|---|---|
| has the current `record_version` | Read it. |
| has a newer `record_version` | Refuse it with `RecordError`, never guess. The message tells the user what to do, for example: `UsageRecord: record_version 3 is newer than this meterline reads (2); upgrade meterline to read it: pip install --upgrade meterline` |
| has no `record_version` | It is a v1 line: refuse it and point to `meterline.convert_v1` (see [v1 lines](#v1-lines)). |
| has any other `record_version` | Refuse it: `record_version 1 is not 2`. |
| has a field the reader does not know | Ignore the field. The record read has only the fields the reader knows, so writing it back drops the unknown field: never rewrite data with an older meterline than the one that wrote it. |
| lacks a nullable field | Read the field as null. |
| lacks a required field | Refuse it: `missing field <name>`. |

The order of the fields in the tables of this page is free: it is not the order of the dataclass fields, the JSONL keys (sorted alphabetically) or the Parquet columns (dataclass order). Read fields by name, never by position.

### Changes before the first release

Nothing was published under `record_version` 2 before the first public release, so the format was still free to change then. From the first public release on, a rename needs a new `record_version` and a converter, as the section above says.

Retired names: before the first public release `record_version` 2 was redefined, not bumped. The field `org_id` (the account at the provider that a record comes from) is now `provider_account_id`, in every record kind, in the run manifests and in the gold datasets. No converter reads the old name for v2 lines: records written before the change are re-created from the raw store with each connector's `rebuild` command, which makes no API call. Raw files and state files written before the change are still read. v1 files keep their own name for the field, and `convert_v1` writes `provider_account_id`.

### `record_version` and package versions

| `record_version` | How a line shows it | Written by | Read by |
|---|---|---|---|
| 1 | no `record_version` field | `meterline` 0.1.x, `meterline-anthropic` 0.1.x | `meterline` 0.2.0 and later, through `convert_v1` and `convert_v1_batch` only |
| 2 | `"record_version": 2` | `meterline` 0.2.0 and later; `meterline-anthropic` 0.2.0 and later; `meterline-anthropic-admin`, `meterline-litellm`, `meterline-copilot`, `meterline-sim` 0.1.0 and later | `meterline` 0.2.0 and later; `meterline-gold` 0.1.0 and later |

`record_version` is not the package version: a package release that changes no rule above keeps it. The other formats have their own version numbers and their own rules: the gold datasets carry `schema_version` and the gold manifest `gold_schema_version` (the gold spec, and `meterline_gold.GOLD_SCHEMA_VERSION`); the policy and price files carry `policy_format` and `price_list_format`.

### Versions of the packages

- Each package has its own version number and follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). They are released one by one, not in lockstep. Every release is listed in `CHANGELOG.md`.
- The public interface of each package is named in its README. Every other module, and any name with a leading underscore, is internal and may change in any release.
- Before 1.0.0 (every package today: no package is at 1.0.0 yet), a breaking change raises the minor version (0.2.0 to 0.3.0) and a patch release never breaks anything. From 1.0.0, which a package takes once a connector has run against a live account, a breaking change raises the major version.
- A new `record_version` is a breaking change of `meterline`.
- Deprecation: a public name, option or field that is to go is first marked deprecated (a `DeprecationWarning` when it is used, and a line in `CHANGELOG.md`) and keeps working for at least one more minor release. It is removed at the earliest in the release after that.
- Each package that depends on `meterline` declares the lowest `meterline` version that has every name it imports (`meterline>=0.3` for the packages that take the data-folder lock, `meterline>=0.2` for the others today). A CI job installs the lowest declared versions, once editable to run the tests and once from the built wheels to import every module.

## v1 lines

`meterline.convert_v1(line, kind)` turns one v1 line into the v2 line `meterline-anthropic` writes for the same fact; `meterline.convert_v1_batch(path)` writes `<name>.v2.jsonl` beside a v1 file and leaves the v1 file as it was. To upgrade a data directory, move the v1 files out of `records/` and rename the `.v2.jsonl` files to `<batch_id>.jsonl`: the connectors read every `*.jsonl` in a kind's folder as v2 and refuse v1 lines. The mapping is the table in section 4 of the spec. In short:

- `tokens_input` becomes `tokens_input_uncached`; `tokens_cache_write` and `tokens_input_total` are derived;
- `web_search_requests` moves to `extras`; `conversations`, `artifacts`, `skills_used`, `connectors_used` and `web_searches` move to `counters`;
- `cost_basis` is `consumption` when a cost is set;
- `scope` is `""`, `grain` `day`, `actor_kind` `user`, `model_provider` `anthropic`.

## Examples

One line per record type, with invented values, produced by `meterline.write_jsonl`'s serialisation (keys sorted, decimals as strings, `null` for an empty nullable field). The seat assignment is a Copilot record (`meterline-copilot` writes them).

`UsageRecord`:

```json
{"actor_deleted": false, "actor_email": "alex.example@example.com", "actor_id": "user_synthetic_0001", "actor_kind": "user", "actor_login": null, "batch_id": "20261009T060000Z", "cost": "1.2345", "cost_basis": "consumption", "cost_list": "1.50", "cost_only": false, "currency": "USD", "data_refreshed_at": "2026-10-09T05:30:00+00:00", "extras": {"web_search_requests": 0}, "grain": "day", "is_final": false, "model": "claude-example-model", "model_provider": "anthropic", "period_end": "2026-10-07", "period_start": "2026-10-07", "product": "claude_code", "provider": "anthropic", "provider_account_id": "00000000-0000-0000-0000-000000000001", "pulled_at": "2026-10-09T06:00:00+00:00", "quantities": {}, "record_version": 2, "requests": 42, "scope": "", "tokens_cache_read": 20000, "tokens_cache_write": 500, "tokens_cache_write_1h": 0, "tokens_cache_write_5m": 500, "tokens_input_total": 21700, "tokens_input_uncached": 1200, "tokens_output": 3400, "tokens_reasoning": null}
```

`ActivityRecord`:

```json
{"actor_email": "alex.example@example.com", "actor_id": "user_synthetic_0001", "actor_kind": "user", "actor_login": null, "batch_id": "20261009T060000Z", "commits": 2, "counters": {"artifacts": 0, "connectors_used": 0, "conversations": 0, "skills_used": 0, "web_searches": 0}, "data_refreshed_at": null, "edits_accepted": 30, "edits_rejected": 4, "grain": "day", "lines_added": 120, "lines_removed": 45, "messages": 0, "period_end": "2026-10-07", "period_start": "2026-10-07", "product": "claude_code", "provider": "anthropic", "provider_account_id": "00000000-0000-0000-0000-000000000001", "pull_requests": 1, "pulled_at": "2026-10-09T06:00:00+00:00", "record_version": 2, "requests": null, "scope": "", "sessions": 3, "suggestions_accepted": null, "suggestions_shown": null}
```

`SeatSnapshot`:

```json
{"batch_id": "20261009T060000Z", "daily_active": 18, "data_refreshed_at": null, "grain": "day", "monthly_active": 40, "pending_cancellations": null, "pending_invites": null, "period_end": "2026-10-07", "period_start": "2026-10-07", "provider": "anthropic", "provider_account_id": "00000000-0000-0000-0000-000000000001", "pulled_at": "2026-10-09T06:00:00+00:00", "record_version": 2, "scope": "", "seats": 50, "weekly_active": 31}
```

`SeatAssignment`:

```json
{"actor_email": null, "actor_id": "1001", "actor_kind": "user", "actor_login": "login-a", "assigned_at": "2026-08-01T09:00:00+00:00", "batch_id": "20261009T060000Z", "data_refreshed_at": null, "grain": "day", "granted_by": "team-a,team-b", "last_active_at": null, "last_authenticated_at": null, "pending_cancellation_date": "2026-11-01", "period_end": "2026-10-09", "period_start": "2026-10-09", "plan": "business", "provider": "github_copilot", "provider_account_id": "org:example-org", "pulled_at": "2026-10-09T06:00:00+00:00", "record_version": 2, "scope": ""}
```

`OrgTotal`:

```json
{"batch_id": "20261009T060000Z", "cost": "88.00", "cost_basis": "consumption", "cost_list": "99.10", "currency": "USD", "data_refreshed_at": "2026-10-09T05:30:00+00:00", "extras": {"web_search_requests": 0}, "grain": "day", "is_final": false, "model": "", "model_provider": "anthropic", "period_end": "2026-10-07", "period_start": "2026-10-07", "product": "", "provider": "anthropic", "provider_account_id": "00000000-0000-0000-0000-000000000001", "pulled_at": "2026-10-09T06:00:00+00:00", "quantities": {}, "record_version": 2, "requests": null, "scope": "", "tokens_cache_read": 1500000, "tokens_cache_write": 30000, "tokens_cache_write_1h": 0, "tokens_cache_write_5m": 30000, "tokens_input_total": 1620000, "tokens_input_uncached": 90000, "tokens_output": 210000, "tokens_reasoning": null}
```
