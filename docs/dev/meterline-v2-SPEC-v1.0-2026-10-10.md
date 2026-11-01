---
type: spec
status: accepted
accepted: 2026-10-11 (owner, in chat)
updated: 2026-10-11
builds_on: meterline-anthropic-SPEC-v1.1-2026-10-10.md (accepted; frozen) and docs/analysis/meterline-v2-analysis.md
---

# meterline v2: provider-neutral record format + GitHub Copilot and LiteLLM connectors

v2 changes the record format so that it no longer assumes Anthropic's shape, migrates `meterline-anthropic` onto it, and adds two connectors: `meterline-litellm` (a LiteLLM proxy) and `meterline-copilot` (GitHub Copilot). The research behind every choice, with its sources, is in `docs/analysis/meterline-v2-analysis.md`. Response shapes below were transcribed from the vendors' public documentation and source code on 2026-10-10; nothing was checked against a live account. Grounding labels: **[V]** read on the cited page on 2026-10-10, **[I]** inferred.

## 1. Purpose and decisions

meterline reads usage data from provider admin APIs and writes one common record format: one JSON line per record, plus optional Parquet. A reader (a dashboard, a data lake, a cost report) never needs to know which provider a record came from.

Decisions taken with the owner on 2026-10-10, in the order they were asked:

| # | Decision |
|---|---|
| D1 | Copilot cost: per person per day as credits from the usage report (consumption); per person per month per model from one billing call per person per month (consumption); per organization per day per model from billing (billed). Only the last is marked billed. |
| D2 | LiteLLM is read through its admin API with a read-only key (`proxy_admin_viewer`). Shapes are copied from v1.104.2. No database path. Minimum version: asked as v1.65, corrected in review to the first stable release that also has the daily cache-token columns (5.1). |
| D3 | meterline does no identity matching. Each record carries the source's own identifiers; email is the join key wherever a source has one; mapping GitHub logins to people is the reader's job. |
| D4 | v2 breaks the v1 format once, before the first public release, and ships a v1 → v2 converter. meterline never writes v1 again. |
| D5 | No FOCUS or OpenTelemetry exporter in v2. Section 9 maps the v2 fields to both, so an exporter later is mechanical. Revisit for v2.1 once FOCUS 1.5 is released. |
| D6 | Build order: format and Anthropic migration, then LiteLLM, then Copilot. "Done" means built to the documented shapes and passing every done-check on synthetic data; a live run is a separate later item per connector. |
| D7 | No connector beyond LiteLLM and Copilot in v2. v2 proves its format can hold five more tools (Cursor, OpenAI API, Augment, Kiro, Windsurf) with a mapping (section 10) and a done-check. |
| D8 | LiteLLM: records for the person the proxy charged the call to (`actor_kind = proxy_user`: the owner of the key, or the signed-in user under JWT) are always written. End-user records (`actor_kind = end_user`) are optional, off by default. Usage with no person is kept as `actor_kind = unassigned`. `proxy_user` and `end_user` records describe the same traffic and must never be added together. |
| D9 | Copilot reads either the organization or the enterprise level, chosen in configuration; the done-checks run at both. |
| D10 | v2 adds a per-person seat record, `SeatAssignment`, one per person per day while they hold a seat. |

Also settled: meterline does not remove duplicate traffic between sources. When a LiteLLM proxy routes calls to a provider that has its own connector, the same calls appear in both. Each record says which source reported it (`provider`); a reader must not add a proxy's records to the serving provider's records for the same traffic. `model_provider` names the serving provider where a source reports it per model; LiteLLM's daily view does not (5.4 rule 1).

## 2. What stays from v1

Unchanged, and binding on every v2 connector: one fetcher that talks to the API and stores every response page as received under `raw/`; one normaliser that reads only raw files; batches named by `pulled_at`; the revision rule (the record with the latest `batch_id` per key wins; earlier batches stay readable); sorted keys and LF line ends; `Decimal` for every money and credit amount, never `float`; the API credential read from the environment only and never written anywhere; logs carry counts and statuses, never emails; a batch that fails any check publishes nothing; commands `backfill --since`, `daily` and `rebuild`. The v1 conventions table in `docs/records.md` (dates UTC, `period_end` inclusive, money as strings with at least two decimals) still applies.

## 3. The v2 record format (package `meterline`)

### 3.1 Rules common to every record

- **`record_version`**: int, always `2`. A line without it is a v1 line; only the converter (3.8) reads v1 lines.
- **Nullable means "not reported".** A measure the source does not report, or sends as null, is `null`, never `0`. A source that reports zero gives `0`. A nullable field missing from a line reads as `null`. One exception, for compatibility: `meterline-anthropic` keeps v1's zeros (section 4).
- **`grain`**: string, one of `day`, `month`, `window`. `day`: `period_start == period_end`. `month`: `period_start` is the first and `period_end` the last day of one calendar month. `window`: any other range. Validation rejects a record whose dates do not match its grain. A `month` record may be written while its month is still running, so its `period_end` may lie in the future. A reader never adds records of different grains together.
- **`scope`**: string, empty by default. A sub-division inside an organization that a source reports separately (a project, a deployment, a client type, a seat tier). Every connector in this spec writes `""`.
- **`org_id`** is never empty and is unique across levels: a connector that can read more than one level prefixes the level (Copilot: `org:<slug>` or `enterprise:<slug>`).
- **`provider`** is the system that reported the data (`anthropic`, `litellm`, `github_copilot`). **`model_provider`** (nullable) is the provider that served the model, when the source says so.
- **Actor kinds** (`actor_kind`): `user` (a person known to the provider), `proxy_user` (the person a proxy charged the call to), `end_user` (a person named by the calling application), `service_account`, `api_key`, `unassigned` (usage the source attributes to no one). Records with `actor_kind = unassigned` have `actor_id = "(unassigned)"`.
- **Tokens: four disjoint buckets.** `tokens_input_uncached` + `tokens_cache_read` + `tokens_cache_write` is the whole input; `tokens_output` is the output. `tokens_input_total` is the whole input when a source reports it, including when it cannot be split. Validation: when the three input buckets and `tokens_input_total` are all non-null, the total equals their sum; when `tokens_cache_write_5m` and `tokens_cache_write_1h` are both non-null, they sum to `tokens_cache_write`. A source whose "input" includes cache (LiteLLM, OpenAI, OpenTelemetry) gives `tokens_input_uncached = input − cache read − cache write`.
- **Quantities.** `quantities` is a map of billing unit → `Decimal` (serialised as strings), for units that are not money: `{"ai_credit": "12.5"}`. Unit names are lower-case with underscores. Empty map when nothing is reported. meterline converts a quantity into money only when the provider documents a fixed price per unit (Copilot: 1 AI credit = USD 0.01 [V]).
- **Cost.** `cost` (after discounts), `cost_list` (at list price), both nullable; `currency` and `cost_basis` describe whichever of the two is set. `cost_basis`: `billed` (from the provider's billing system), `consumption` (a usage figure the provider reports but does not invoice), `estimate` (computed by a third party from a price list). `currency` is `""` and `cost_basis` is null exactly when `cost` and `cost_list` are both null.
- **`extras`**: a flat map of name → number (`Decimal` or int) for source-specific measures that have no field. Keys are lower-case with underscores and are documented per connector. Never a string value, never a nested map, never content.
- **Never content.** No record and no raw file holds prompts, responses or message bodies. A connector never requests a field or endpoint that returns content.
- Every record carries `pulled_at` (UTC datetime), `data_refreshed_at` (nullable) and `batch_id`, as in v1.

### 3.2 `UsageRecord`

Tokens, quantities and cost for one actor, one period, one product, one model.

**Key:** `provider`, `org_id`, `scope`, `grain`, `period_start`, `actor_kind`, `actor_id`, `product`, `model`.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `record_version` | int | no | `2`. |
| `provider` | string | no | The reporting system. |
| `org_id` | string | no | The organization or deployment the data belongs to. Never empty. |
| `scope` | string | no | Sub-division; `""` by default. |
| `grain` | string | no | `day`, `month` or `window`. |
| `period_start`, `period_end` | date | no | UTC, `period_end` inclusive. |
| `actor_kind` | string | no | See 3.1. |
| `actor_id` | string | no | The source's id for the actor, as a string. Never empty. |
| `actor_email` | string | yes | Trimmed and lower-cased. Null when the source has none. |
| `actor_login` | string | yes | A user name or login, when the source has one. |
| `actor_deleted` | bool | yes | Null when the source does not say. |
| `product` | string | no | As received; `""` when the source gives none. |
| `model` | string | no | As received; `""` when the source gives none or the record covers all models. |
| `model_provider` | string | yes | Who served the model. |
| `tokens_input_uncached` | int | yes | Input tokens not read from or written to cache. |
| `tokens_cache_read` | int | yes | Input tokens read from cache. |
| `tokens_cache_write` | int | yes | Input tokens written to cache. |
| `tokens_cache_write_5m`, `tokens_cache_write_1h` | int | yes | Cache writes by lifetime, when the source splits them. |
| `tokens_input_total` | int | yes | All input tokens. |
| `tokens_output` | int | yes | Output tokens, reasoning included. |
| `tokens_reasoning` | int | yes | Reasoning tokens, a subset of `tokens_output`. |
| `requests` | int | yes | Requests. |
| `quantities` | map string → decimal | no | Non-money billing units. `{}` when none. |
| `cost` | decimal | yes | Cost after discounts. |
| `cost_list` | decimal | yes | Cost at list price. |
| `currency` | string | no | ISO code of `cost` and `cost_list`; `""` when both are null. |
| `cost_basis` | string | yes | `billed`, `consumption` or `estimate`. |
| `is_final` | bool | no | The connector's finality rule (sections 4, 5, 6). |
| `cost_only` | bool | no | Cost arrived without usage (v1 meaning). Its token fields are null, except in `meterline-anthropic`, which keeps v1's zeros. |
| `extras` | map string → number | no | `{}` when none. |
| `pulled_at`, `data_refreshed_at`, `batch_id`, `record_version` | | | As in 3.1. |

### 3.3 `ActivityRecord`

Activity counters for one actor, one period, one product.

**Key:** `provider`, `org_id`, `scope`, `grain`, `period_start`, `actor_kind`, `actor_id`, `product`.

Fields: `record_version`, `provider`, `org_id`, `scope`, `grain`, `period_start`, `period_end`, `actor_kind`, `actor_id`, `actor_email`, `actor_login`, `product`, the core counters below (all int, all nullable), `counters` (map string → int, `{}` when none), `pulled_at`, `data_refreshed_at`, `batch_id`.

| Core counter | Meaning |
|---|---|
| `sessions` | Distinct sessions. |
| `messages` | Messages or prompts the person sent. |
| `requests` | Requests or agent calls. |
| `suggestions_shown` | Code suggestions or generations shown to the person. |
| `suggestions_accepted` | Code suggestions accepted. |
| `lines_added`, `lines_removed` | Lines written or removed by the AI and kept. |
| `edits_accepted`, `edits_rejected` | Edit actions accepted or rejected. |
| `commits`, `pull_requests` | Commits and pull requests made through the tool. |

A counter the source does not report is null (3.1); `meterline-anthropic` keeps v1's zeros instead (section 4). Counters with no core field go to `counters` under the name each connector's mapping gives.

### 3.4 `SeatSnapshot`

Seats and active users for one organization on one day. **Key:** `provider`, `org_id`, `scope`, `period_start`. Grain is always `day`. Each connector states which day a value belongs to; values for one day may come from several pulls, and the normaliser combines the raw files of that day (6.4 rule 8).

Fields: `record_version`, `provider`, `org_id`, `scope`, `grain`, `period_start`, `period_end`, `seats` (int, nullable), `pending_invites` (int, nullable), `pending_cancellations` (int, nullable), `daily_active`, `weekly_active`, `monthly_active` (int, nullable), `pulled_at`, `data_refreshed_at`, `batch_id`.

### 3.5 `SeatAssignment` (new)

One person holding a seat on one day. **Key:** `provider`, `org_id`, `scope`, `period_start`, `actor_id`. Grain is always `day`; the day is the UTC date of `pulled_at` (seat lists are snapshots).

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `record_version`, `provider`, `org_id`, `scope`, `grain`, `period_start`, `period_end` | | | As above. |
| `actor_kind` | string | no | `user`. |
| `actor_id`, `actor_email`, `actor_login` | string | id no, others yes | As in `UsageRecord`. |
| `plan` | string | yes | The seat's plan, as received. |
| `assigned_at` | datetime | yes | When the seat was assigned (the earliest, if several). |
| `last_active_at` | datetime | yes | Last activity the source knows. |
| `last_authenticated_at` | datetime | yes | Last sign-in the source knows. |
| `pending_cancellation_date` | date | yes | The day the seat will end, if cancelled. |
| `granted_by` | string | yes | The teams or organizations that grant the seat, sorted and joined with `,`. |
| `pulled_at`, `data_refreshed_at`, `batch_id` | | | As above. |

### 3.6 `OrgTotal`

Organization-wide measures for one period, product and model, including usage no actor accounts for. **Key:** `provider`, `org_id`, `scope`, `grain`, `period_start`, `product`, `model`. A connector writes either per-model rows or one all-models row (`model` `""`) for a period and product, never both.

Fields: the measure fields of `UsageRecord` (`tokens_*`, `requests`, `quantities`, `cost`, `cost_list`, `currency`, `cost_basis`, `extras`), plus `record_version`, `provider`, `org_id`, `scope`, `grain`, `period_start`, `period_end`, `product` (`""` when the source gives none), `model` (`""` = all models), `model_provider`, `is_final`, `pulled_at`, `data_refreshed_at`, `batch_id`.

### 3.7 Validation (before publishing a batch)

v1 checks still apply: `period_end` not before `period_start`, no future `period_start` (and no future `period_end` except on `grain=month`), no negative or non-finite number, no duplicate key in the batch, record totals equal raw totals. New:

1. `grain` matches the dates (3.1).
2. The token sums of 3.1 hold.
3. `cost_basis` is null exactly when `cost` and `cost_list` are null; `currency` is `""` exactly when `cost` is null.
4. `extras` and `counters` hold numbers only; `quantities` keys match `^[a-z][a-z0-9_]*$`.
5. "Actor cost on a day never exceeds the organization's cost" applies only between records of the same `provider`, `org_id`, `scope`, `grain`, day, `cost_basis` and `currency`, summed over products and models, and never counts `end_user` records (they repeat other actors' traffic).

### 3.8 Files and the converter

- Files: `records/<kind>/<batch_id>.jsonl`, kinds `usage`, `activity`, `seats`, `seat_assignments`, `org_totals`. Optional Parquet beside each file, as in v1.
- `meterline.convert_v1(line: dict, kind: str) -> dict` turns one v1 line (all v1 lines are Anthropic's) into exactly the v2 line the migrated `meterline-anthropic` would write for the same fact, by the table of section 4. `meterline.convert_v1_batch(path)` writes a v2 file beside a v1 file without changing the v1 file.

## 4. Migration of `meterline-anthropic`

The fetcher does not change. The normaliser maps as follows (v1 → v2). For compatibility, `meterline-anthropic` keeps v1's zeros: a count the API sends as null, a counter a product block does not have, and the tokens of a cost-only record stay `0`, as in v1. This is the one exception to 3.1, and it is what lets `convert_v1` and a v2 `rebuild` give the same bytes.

| v1 field | v2 |
|---|---|
| `tokens_input` (`uncached_input_tokens`) | `tokens_input_uncached` |
| `tokens_cache_write_5m`, `tokens_cache_write_1h` | unchanged; `tokens_cache_write` = their sum |
| `tokens_cache_read` | unchanged |
| (new) | `tokens_input_total` = uncached + cache read + cache write |
| `tokens_output` | unchanged; `tokens_reasoning` null |
| `web_search_requests` | `extras["web_search_requests"]`, always present (0 included) |
| `cost`, `cost_list`, `currency` | unchanged; `cost_basis` = `consumption` when `cost` or `cost_list` is not null |
| `actor_deleted` | unchanged (now nullable; Anthropic always sends it) |
| (new on usage) | `actor_kind` `user`, `actor_login` null, `scope` `""`, `grain` `day`, `quantities` `{}`, `model_provider` `anthropic` |
| Activity `conversations`, `artifacts`, `skills_used`, `connectors_used`, `web_searches` | `counters["conversations"]`, `["artifacts"]`, `["skills_used"]`, `["connectors_used"]`, `["web_searches"]`, all five always present (0 included) |
| Activity `sessions`, `messages`, `commits`, `pull_requests`, `lines_added`, `lines_removed`, `edits_accepted`, `edits_rejected` | unchanged core counters, 0 as in v1; `requests`, `suggestions_shown`, `suggestions_accepted` null |
| (new on activity) | `actor_kind` `user`, `actor_login` null, `scope` `""`, `grain` `day` |
| `SeatSnapshot` | `scope` `""`, `grain` `day`; `pending_cancellations` null |
| `OrgTotal` | as `UsageRecord` tokens and `extras`; `product` `""`, `model` `""`, `quantities` `{}`, `model_provider` `anthropic` |

**The 17 done-checks of v1.1 under v2.** All seventeen must pass against v2 records. Assertions change in three:

- Check 5: `rec.tokens_input` and `before.tokens_input` become `tokens_input_uncached`.
- Check 13: the expected numbers are unchanged, but `conversations`, `artifacts`, `skills_used`, `connectors_used` and `web_searches` are read from `rec.counters[...]` instead of attributes; the other eight names are still attributes.
- Check 16: the five token attributes it asserts as 0 become `tokens_input_uncached`, `tokens_output`, `tokens_cache_write_5m`, `tokens_cache_write_1h`, `tokens_cache_read` (still 0, by the compatibility rule above).

Checks 1-4, 6-12, 14, 15 and 17 read keys, costs, manifests, shapes or files and run unchanged; check 9 (rebuild byte for byte) now compares v2 output. The manifest keys `unknown_products` and `unknown_models` keep their v1 names; the new connectors use the same names, plus `unknown_units` (Copilot).

## 5. Connector `meterline-litellm`

### 5.1 Source

- A LiteLLM proxy's admin API. Base URL from `LITELLM_BASE_URL`; key from `LITELLM_API_KEY`, sent as `Authorization: Bearer <key>`. The key should belong to a user with role `proxy_admin_viewer`, which may call every GET route used here and nothing that writes [V `litellm/proxy/auth/route_checks.py`]. Supported proxy versions: the first stable release that contains both the daily cache-token columns (migration `20250415151647_add_cache_read_write_tokens_daily_spend_transactions`) and the per-key breakdown (PR #10152, merged 2025-04-19); `/user/daily/activity` itself exists from v1.65.0 [V]. The release number is not resolved [I: v1.67.0 or later]; the builder resolves it from the repository's tags and writes it into the connector README and the error message of 5.3 rule 4. `org_id` comes from the required option `--proxy-name` (any non-empty label chosen by the operator).
- No rate limit is documented on these routes; the client throttles to a configurable rate (default 120 requests per minute) and backs off on 429 and 5xx as in v1.
- Sources: https://github.com/BerriAI/litellm/blob/v1.104.2/litellm/proxy/management_endpoints/internal_user_endpoints.py (`/user/daily/activity`, L2873), .../common_daily_activity.py, https://github.com/BerriAI/litellm/blob/v1.104.2/litellm/types/proxy/management_endpoints/common_daily_activity.py (response model), .../customer_endpoints.py (`/customer/daily/activity`, L866), https://docs.litellm.ai/docs/proxy/cost_tracking , https://docs.litellm.ai/docs/proxy/customers .

### 5.2 Response shapes (normative)

**A. `GET /user/daily/activity`**: query `start_date`, `end_date` (`YYYY-MM-DD`, both required, both inclusive, UTC days), optional `user_id`, `page` (≥ 1), `page_size` (1-1000; the connector sends 1000). Do not send `include_current_utc_day` (it defaults to false). Response model, transcribed from the source [V]:

```
SpendAnalyticsPaginatedResponse = { "results": [ DailySpendData, ... ], "metadata": DailySpendMetadata }
DailySpendData = { "date": "YYYY-MM-DD", "metrics": SpendMetrics, "breakdown": BreakdownMetrics }
SpendMetrics = { "spend": float, "flat_cost": float, "prompt_tokens": int, "completion_tokens": int,
                 "cache_read_input_tokens": int, "cache_creation_input_tokens": int,
                 "compression_saved_tokens": int, "compression_savings_spend": float,
                 "prompt_caching_savings_spend": float, "gateway_injected_caching_savings_spend": float,
                 "autorouter_savings_spend": float, "total_tokens": int, "successful_requests": int,
                 "failed_requests": int, "api_requests": int, "total_response_time_ms": int,
                 "timed_requests": int }
BreakdownMetrics = { "mcp_servers": {str: MetricWithMetadata}, "models": {str: MetricWithMetadata},
                     "model_groups": {str: MetricWithMetadata}, "providers": {str: MetricWithMetadata},
                     "endpoints": {str: MetricWithMetadata}, "api_keys": {str: KeyMetricWithMetadata},
                     "entities": {str: MetricWithMetadata} }
MetricWithMetadata = { "metrics": SpendMetrics, "metadata": {str: any},
                       "api_key_breakdown": {str: KeyMetricWithMetadata} }
KeyMetricWithMetadata = { "metrics": SpendMetrics,
                          "metadata": { "key_alias": str|null, "team_id": str|null, "user_id": str|null,
                                        "user_email": str|null, "key_exists": bool|null } }
DailySpendMetadata = { "total_spend": float, "total_flat_cost": float, "total_prompt_tokens": int,
                       "total_completion_tokens": int, "total_tokens": int, "total_api_requests": int,
                       "total_successful_requests": int, "total_failed_requests": int,
                       "total_cache_read_input_tokens": int, "total_cache_creation_input_tokens": int,
                       "total_compression_saved_tokens": int, "total_compression_savings_spend": float,
                       "total_prompt_caching_savings_spend": float,
                       "total_gateway_injected_caching_savings_spend": float,
                       "total_autorouter_savings_spend": float, "total_response_time_ms": int,
                       "total_timed_requests": int, "page": int, "total_pages": int, "has_more": bool }
```

Pages cut through the table's raw rows (ordered by date, then id), so the same date can appear on two consecutive pages with partial numbers [V]. `breakdown.entities` is keyed by user id and has no model split; `breakdown.models` has no user split [V]. A field not listed may appear and is ignored. Older proxy versions may lack some `SpendMetrics` fields; a missing metric reads as not reported (null), as does a token field the proxy does not send.

**B. `GET /customer/daily/activity`** (end users; only when `--end-users` is set): query `end_user_ids` (comma list), `start_date`, `end_date`, `page`, `page_size`; same response model as A, with `breakdown.entities` keyed by end-user id and entity metadata `{"alias": str|null}` [V]. Admin or admin-viewer only; any other role gets 401 [V].

**C. `GET /user/list`**: used only to read `user_id` → `user_email`. Takes `page` and `page_size` (≤ 100) [V]. Its exact envelope was not transcribed in this research. The builder transcribes it from the response model in the source at tag v1.104.2 into `docs/api-shapes/litellm/`, and names the source file and line in `docs/api-shapes/litellm/README.md`; the reviewer of the build checks that transcription against the source, because the shape check (L01) can only compare the fake with the file.

### 5.3 Fetch rules

1. A window is at most 31 days. For each window: one call to A without `user_id` (all pages; the **global** view), then one call to A with `user_id` for each key of the global view's `breakdown.entities` (all pages; the **per-user** views). An empty, null or `Unassigned` entity key is never called; its usage falls into the derived `unassigned` record (5.4 rule 4). Users with no activity in the window cost no call. With `--end-users`: one call to B without `end_user_ids` (the global end-user view), then one call to B per end-user id found in its `breakdown.entities`, one id per call, so that `breakdown.models` belongs to that end user alone. At 1,000 active people this is about 1,000 calls per window for each view [I].
2. Pages are fetched while `metadata.has_more` is true; page results are merged by `date` (sum every metric; merge breakdown maps key by key) before normalising.
3. `daily` reads every closed UTC day since the last loaded day, and always re-reads the two days before the pull day (rows are written in batches and can arrive late [V]). `backfill --since` walks windows backwards from yesterday.
4. A 404 on A stops the run with the message "path not found: wrong base URL, or a proxy older than <minimum version>", and publishes nothing.
5. JSON numbers are parsed straight into `Decimal` (`json.loads(..., parse_float=Decimal)`); `float` never appears on the cost path.

### 5.4 Mapping rules

1. **Per-user usage.** For each per-user view, each date and each model in `breakdown.models`: one `UsageRecord` with `actor_kind` `proxy_user`, `actor_id` the user id, `actor_email` from C (null if absent), `grain` `day`, `product` `""`, `model` the model key, `model_provider` null (the daily view splits providers and models in separate maps, so a model cannot be tied to its provider [V]).
2. **Tokens.** `tokens_input_total` = `prompt_tokens`; `tokens_cache_read` = `cache_read_input_tokens`; `tokens_cache_write` = `cache_creation_input_tokens`; `tokens_input_uncached` = total − read − write; `tokens_output` = `completion_tokens`. LiteLLM's `prompt_tokens` includes both cache figures [V `litellm/llms/anthropic/chat/transformation.py` L2395-2455; Bedrock Converse likewise]. If the subtraction is negative, `tokens_input_uncached` is null and the key is listed in the manifest under `token_anomalies`. The 5m/1h split and `tokens_reasoning` are null.
3. **Cost.** `cost` = `spend`, `cost_list` null, `currency` `USD`, `cost_basis` `estimate` (LiteLLM prices calls from its own price list [V]). `requests` = `api_requests`. `extras`: `successful_requests`, `failed_requests`, and `flat_cost` when non-zero.
4. **Unassigned.** For each date and model, the global view minus the sum of the per-user views, field by field, is written as one `UsageRecord` with `actor_kind` `unassigned` and `actor_id` `(unassigned)`, but only if any field is non-zero. A negative difference fails the run (the views disagree). This covers keys with no owner and calls signed in with a JWT that carries no user claim [V: empty user stored and shown as "Unassigned"].
5. **End users** (`--end-users` only). As rule 1, from each per-end-user call to B, with `actor_kind` `end_user`, `actor_email` null and `actor_login` = the entity's `alias`. No unassigned record is derived for end users. These records describe the same traffic as the `proxy_user` records and must never be added to them; the connector's README says so in its first section.
6. **OrgTotal.** One per date and model from the global view's `breakdown.models`, `product` `""`, same measures as rules 2 and 3. No all-models row is written (3.6).
7. **Finality.** `is_final` is true when the day ended at least 2 full UTC days before `pulled_at`.
8. No `ActivityRecord`, `SeatSnapshot` or `SeatAssignment` comes from LiteLLM.
9. **Record totals equal raw totals**: the sum of `proxy_user` plus `unassigned` records per date equals the global view's `metrics` per date, for every token field, `requests` and `cost`. `end_user` records are never part of this sum.

### 5.5 Out of scope

The database path, `/spend/logs` and `/spend/logs/v2` (per-request detail), the licence-gated `*/spend/report` routes, team and tag views, the pre-release export route of v1.105, and any per-request field (`messages`, `response`, `metadata`).

## 6. Connector `meterline-copilot`

### 6.1 Source

- GitHub REST API, `https://api.github.com`, headers `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2026-03-10`, `Authorization: Bearer <token>`, and a `User-Agent` naming meterline [V https://docs.github.com/en/rest/about-the-rest-api/api-versions].
- Level, from the required option `--level organization|enterprise` and `--slug <name>` (the organization login or enterprise slug). `org_id` = `org:<slug>` or `enterprise:<slug>` (3.1), so the two levels never share a key. All paths below exist at both levels with identical shapes, except that billing replies carry `organization` or `enterprise` at the top level [V].
- Credentials (environment only): `COPILOT_TOKEN` for usage reports and seats; `COPILOT_BILLING_TOKEN` for billing, defaulting to `COPILOT_TOKEN`. What each level needs [V https://docs.github.com/en/rest/copilot/copilot-usage-metrics , https://docs.github.com/en/rest/copilot/copilot-user-management , https://docs.github.com/en/enterprise-cloud@latest/rest/billing/usage?apiVersion=2026-03-10]:

| Level | Usage reports | Seats | Billing |
|---|---|---|---|
| organization | fine-grained token or GitHub App with "Organization Copilot metrics" read; classic `read:org` | fine-grained with "GitHub Copilot Business" or "Administration" read; classic `manage_billing:copilot` or `read:org` | fine-grained or GitHub App with "Administration" read |
| enterprise | classic `manage_billing:copilot` or `read:enterprise`, or a GitHub App with "Enterprise Copilot metrics" read; not fine-grained tokens | **classic token only** (`manage_billing:copilot` or `read:enterprise`) | GitHub App token with "Enterprise billing" read (classic scopes not documented) |

  A GitHub App installation token expires after an hour; obtaining one is the operator's job, not meterline's.
- Limits: 5,000 requests per hour for a user token, 15,000 for a GitHub App on Enterprise Cloud; at most 100 concurrent requests; 900 points per minute per endpoint; a 403 or 429 with `retry-after` or `x-ratelimit-reset` means wait [V https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api]. The client throttles to a configurable rate (default 600 per hour), honours both headers, and backs off as in v1.
- History: usage reports from 2025-10-10, at most one year back [V]; billing 24 months back [V]. Usage reports "typically finalize within three full UTC days" [V https://docs.github.com/en/copilot/reference/copilot-usage-metrics/reconciling-usage-metrics].

### 6.2 Response shapes (normative)

**A. Usage report, per user, one day.** `GET /orgs/{org}/copilot/metrics/reports/users-1-day?day=YYYY-MM-DD` or `GET /enterprises/{enterprise}/copilot/metrics/reports/users-1-day?day=YYYY-MM-DD`. Response [V https://docs.github.com/en/rest/copilot/copilot-usage-metrics]:

```json
{ "download_links": [ "https://example.com/copilot-usage-report-1.ndjson",
                      "https://example.com/copilot-usage-report-2.ndjson" ],
  "report_day": "2025-07-01" }
```

204 means no report for that day [V for organization `users-1-day`]. Each link is a signed URL to an NDJSON file, one JSON object per line [I: one object per line]. The client downloads every link, without sending the GitHub token to the download host, and stores each file under `raw/` as received. One row, documented example trimmed to one entry per array [V https://docs.github.com/en/copilot/reference/copilot-usage-metrics/example-schema]:

```json
{ "ai_adoption_phase": { "phase": "Phase 2", "phase_number": 2, "version": "v1" },
  "ai_credits_used": 12.5,
  "code_acceptance_activity_count": 3, "code_generation_activity_count": 3,
  "day": "2025-10-01",
  "distinct_custom_agent_use_count": 2, "distinct_mcp_use_count": 2, "distinct_plugin_use_count": 1,
  "distinct_skill_use_count": 3, "distinct_slash_cmd_use_count": 2,
  "enterprise_id": "1",
  "loc_added_sum": 32, "loc_deleted_sum": 6, "loc_suggested_to_add_sum": 34, "loc_suggested_to_delete_sum": 6,
  "totals_by_cli": { "last_known_cli_version": { "cli_version": "1.0.8", "sampled_at": "2025-10-01T00:01:43.000Z" },
                     "prompt_count": 2, "request_count": 2, "session_count": 2,
                     "token_usage": { "avg_tokens_per_request": 4400.0, "output_tokens_sum": 5000, "prompt_tokens_sum": 3800 } },
  "totals_by_copilot_app": { "prompt_count": 1, "request_count": 3, "session_count": 1,
                             "token_usage": { "avg_tokens_per_request": 3200.0, "output_tokens_sum": 4200, "prompt_tokens_sum": 5400 } },
  "totals_by_custom_agent": [ { "custom_agent": "general-purpose", "interaction_count": 4 } ],
  "totals_by_3rd_party_agent": [ { "agent_id": "2246796", "agent_name": "...", "user_initiated_interaction_count": 2 } ],
  "totals_by_feature": [ { "code_acceptance_activity_count": 1, "code_generation_activity_count": 1,
                           "feature": "code_completion", "loc_added_sum": 8, "loc_deleted_sum": 0,
                           "loc_suggested_to_add_sum": 10, "loc_suggested_to_delete_sum": 0,
                           "user_initiated_interaction_count": 0 } ],
  "totals_by_ide": [ { "code_acceptance_activity_count": 1, "code_generation_activity_count": 1, "ide": "vscode",
                       "last_known_ide_version": { "ide_version": "1.85.0", "sampled_at": "2025-10-01T00:00:02.000Z" },
                       "last_known_plugin_version": { "plugin": "", "plugin_version": "", "sampled_at": "2025-10-01T00:00:02.000Z" },
                       "loc_added_sum": 8, "loc_deleted_sum": 0, "loc_suggested_to_add_sum": 10, "loc_suggested_to_delete_sum": 0,
                       "user_initiated_interaction_count": 0 } ],
  "totals_by_language_feature": [ { "code_acceptance_activity_count": 1, "code_generation_activity_count": 1,
                                    "feature": "code_completion", "language": "unknown", "loc_added_sum": 8,
                                    "loc_deleted_sum": 0, "loc_suggested_to_add_sum": 10, "loc_suggested_to_delete_sum": 0 } ],
  "totals_by_language_model": [],
  "totals_by_mcp": [ { "interaction_count": 8, "mcp": "github-mcp-server" } ],
  "totals_by_model_feature": [],
  "totals_by_vscode_agent": { "session_count": 1, "total_user_messages": 1 },
  "totals_by_plugin": [ { "interaction_count": 2, "plugin": "other" } ],
  "totals_by_skill": [ { "interaction_count": 5, "skill": "other" } ],
  "totals_by_slash_cmd": [ { "interaction_count": 3, "slash_cmd": "/plan" } ],
  "used_agent": false, "used_chat": false, "used_cli": true, "used_copilot_app": true,
  "used_copilot_cloud_agent": false, "used_copilot_code_review_active": null,
  "used_copilot_code_review_passive": null, "used_copilot_coding_agent": false, "used_vscode_agent": true,
  "user_id": 1, "user_login": "login1", "user_initiated_interaction_count": 1 }
```

Organization-level rows also carry `organization_id` [V]. Optional in the shape check (C01): `organization_id`, `totals_by_cli`, `totals_by_copilot_app`, `totals_by_vscode_agent`, `totals_by_3rd_party_agent`, `used_vscode_agent`, the `distinct_*_use_count` fields and the `totals_by_<name>` top-five arrays [V nullable per the field table of https://docs.github.com/en/copilot/reference/copilot-usage-metrics/copilot-usage-metrics]. `totals_by_cli` and `totals_by_copilot_app` are absent when unused [V]. A field not listed may appear and is ignored.

**B. Usage report, aggregate, one day.** `GET /orgs/{org}/copilot/metrics/reports/organization-1-day?day=` or `/enterprises/{enterprise}/copilot/metrics/reports/enterprise-1-day?day=`. Same envelope as A [V]. The connector reads only `daily_active_users`, `weekly_active_users` and `monthly_active_users` for the requested day; every other field is kept in `raw/` and ignored. The three names appear in the documented example [V https://docs.github.com/en/copilot/reference/copilot-usage-metrics/example-schema], but that example shows them inside a `day_totals` array, and the exact layout of one `*-1-day` aggregate row was not transcribed [I]. The builder transcribes the documented aggregate example into `docs/api-shapes/copilot/` (naming the page and section in `docs/api-shapes/copilot/README.md`) before writing the client; C01 then pins it.

**C. Billing, AI credits.** `GET /organizations/{org}/settings/billing/ai_credit/usage` or `/enterprises/{enterprise}/settings/billing/ai_credit/usage`, query `year`, `month`, optional `day`, optional `user` (a login) [V]. Always send `year` and `month` explicitly (the documented default when they are omitted is inconsistent between pages [V]). Response [V https://docs.github.com/en/enterprise-cloud@latest/rest/billing/usage?apiVersion=2026-03-10]:

```json
{ "timePeriod": { "year": 2025 },
  "enterprise": "GitHub",
  "usageItems": [ { "product": "Copilot", "sku": "Copilot AI Credits", "model": "GPT-5",
                    "unitType": "credits", "pricePerUnit": 0.01, "grossQuantity": 100, "grossAmount": 1,
                    "discountQuantity": 0, "discountAmount": 0, "netQuantity": 100, "netAmount": 1 } ] }
```

The organization reply carries `"organization"` instead of `"enterprise"` [V]. `timePeriod` also holds `month` and `day` when they were sent [V schema]. Items carry no user and no date [V]. No pagination is documented [V].

**D. Seats.** `GET /orgs/{org}/copilot/billing/seats` or `/enterprises/{enterprise}/copilot/billing/seats`, `page` and `per_page` (≤ 100; the connector sends 100), continue while the `Link` header has `rel="next"` [V]. Public preview [V]. Response, one seat, assignee trimmed:

```json
{ "total_seats": 2,
  "seats": [ { "created_at": "2021-08-03T18:00:00-06:00", "updated_at": "2021-09-23T15:00:00-06:00",
               "pending_cancellation_date": null, "last_activity_at": "2021-10-14T00:53:32-06:00",
               "last_activity_editor": "vscode/1.77.3/copilot/1.86.82",
               "last_authenticated_at": "2021-10-14T00:53:32-06:00", "plan_type": "business",
               "assignee": { "login": "octocat", "id": 1, "node_id": "MDQ6VXNlcjE=", "type": "User", "site_admin": false },
               "assigning_team": { "id": 1, "name": "Justice League", "slug": "justice-league" } } ] }
```

`plan_type` and `assigning_team` may be absent [V]. At enterprise level, one user can appear once per organization or team granting the seat; `total_seats` counts them once [V].

**E. Organization seat summary** (organization level only). `GET /orgs/{org}/copilot/billing` [V]:

```json
{ "seat_breakdown": { "total": 12, "added_this_cycle": 9, "pending_invitation": 0, "pending_cancellation": 0,
                      "active_this_cycle": 12, "inactive_this_cycle": 11 },
  "seat_management_setting": "assign_selected", "plan_type": "business" }
```

### 6.3 Fetch rules

1. `daily` reads A and B for every day since the last loaded day through yesterday (UTC), and always re-reads the four days before the pull day (three-day finality plus one day; GitHub has raised past credit figures retroactively [V https://github.blog/changelog/2026-07-02-improved-accuracy-and-coverage-in-copilot-usage-metrics-reports]).
2. For each day read in rule 1, one call to C with `year`, `month`, `day` and no `user` (organization per day per model).
3. Per person per month: one call to C with `year`, `month` and `user` for each login that appears in a stored A row dated in that month, or in a D response pulled in that month. `daily` does this for the current month and, on days 1 to 8 inclusive, the previous month. At 1,000 people this is about 1,000 calls per month per pass [I].
4. D (and E at organization level) once per run; a second run on the same day replaces the day's snapshot through the revision rule.
5. `backfill --since` reads A and B day by day back to the given day (not before 2025-10-10, not more than one year back), C per day and per person per month over the same range (not more than 24 months back), and D once.
6. Download links are fetched immediately after the report call; a 403 or 404 from the download host restarts that day from the report call once, then fails the run.
7. JSON numbers are parsed into `Decimal`. The token is never written to `raw/`, logs or the manifest, and never sent to a host other than `api.github.com`.

### 6.4 Mapping rules

1. **Daily credits** (from A). Per row: one `UsageRecord` with `provider` `github_copilot`, `grain` `day`, `actor_kind` `user`, `actor_id` = `user_id` as a string, `actor_login` = `user_login`, `actor_email` null, `product` `copilot`, `model` `""`, `quantities` `{"ai_credit": ai_credits_used}`, `cost` = `cost_list` = credits × 0.01, `currency` `USD`, `cost_basis` `consumption`, all token fields null. Written only when `ai_credits_used` is present.
2. **CLI and app tokens** (from A). When `totals_by_cli` is present: one `UsageRecord` with `product` `copilot_cli`, `model` `""`, `tokens_input_total` = `token_usage.prompt_tokens_sum`, `tokens_output` = `token_usage.output_tokens_sum`, the three input buckets null (GitHub does not split cache [V]), `requests` = `request_count`, `quantities` `{}`, no cost. The same for `totals_by_copilot_app` with `product` `copilot_app`. These records carry no credits: credits for all surfaces sit on the rule 1 record, so a reader can add credits and tokens without counting twice.
3. **Activity** (from A). One `ActivityRecord` per entry of `totals_by_feature`, `product` = `feature` as received: `messages` = `user_initiated_interaction_count`, `suggestions_shown` = `code_generation_activity_count`, `suggestions_accepted` = `code_acceptance_activity_count`, `lines_added` = `loc_added_sum`, `lines_removed` = `loc_deleted_sum`; `counters`: `lines_suggested_to_add` = `loc_suggested_to_add_sum`, `lines_suggested_to_delete` = `loc_suggested_to_delete_sum`. For the `copilot_cli` and `copilot_app` features, `sessions` and `requests` come from `totals_by_cli` / `totals_by_copilot_app`. Other core counters are null. One more record with `product` `copilot` per row carries, in `counters`, the fields that are present among: `custom_agents_used` = `distinct_custom_agent_use_count`, `mcp_servers_used` = `distinct_mcp_use_count`, `plugins_used` = `distinct_plugin_use_count`, `skills_used` = `distinct_skill_use_count`, `slash_commands_used` = `distinct_slash_cmd_use_count`; its core counters are null. Breakdowns by IDE, language, model and agent, the `used_*` flags and the adoption phase stay in `raw/` (out of scope).
4. **Raw totals** (from A). For every row, the sum of the `totals_by_feature` entries equals the row's top-level `code_generation_activity_count`, `code_acceptance_activity_count`, `loc_added_sum`, `loc_deleted_sum` and `user_initiated_interaction_count` [I: holds in every documented example]. A row where it does not hold is listed in the manifest under `feature_sum_mismatch`; the run still publishes. Record totals must equal the feature sums.
5. **Monthly person × model** (from C with `user`). Items with the same normalised `product` and `model` are summed (amounts, and quantities per unit); then one `UsageRecord` per (product, model): `grain` `month`, `period_start` / `period_end` the month's first and last day, `actor_kind` `user`, `actor_login` = the `user` sent, `actor_id` = that user's `user_id` from A or `assignee.id` from D (if neither has it, the login), `product` = item `product` normalised (lower-cased, every run of non-alphanumerics replaced by `_`: `Copilot` → `copilot`), `model` = item `model` as received, `quantities` `{unit: grossQuantity}`, `cost_list` = `grossAmount`, `cost` = `grossAmount` − `discountAmount`, `currency` `USD`, `cost_basis` `consumption` (included credits are pooled across the billing entity, so a person's discount is not their invoice [V pooling, https://docs.github.com/en/copilot/concepts/billing-and-usage/organizations-and-enterprises/billing]), `extras`: `discount_quantity`, `net_quantity`, `net_amount`, `price_per_unit`.
6. **Unit names.** `unitType` values `credits`, `ai-credits` and `ai_credits` map to the unit `ai_credit` (the documented examples disagree [V]); `requests` maps to `premium_request`; any other value is lower-cased, has non-alphanumerics replaced by `_`, and is listed in the manifest under `unknown_units`.
7. **Organization per day per model** (from C without `user`). Items summed as in rule 5, then one `OrgTotal` per (product, model): `grain` `day`, `product` normalised as in rule 5, `model` = item `model`, `quantities` `{unit: grossQuantity}`, `cost` = `netAmount`, `cost_list` = `grossAmount`, `currency` `USD`, `cost_basis` `billed`, `extras` as rule 5.
8. **Seats.** One `SeatSnapshot` per UTC day d for which raw data exists: `seats` = `total_seats` from the latest D pulled on day d; `pending_invites` = `seat_breakdown.pending_invitation` and `pending_cancellations` = `seat_breakdown.pending_cancellation` from the latest E pulled on day d (null at enterprise level); `daily_active`, `weekly_active`, `monthly_active` from B for day d. B for day d is read on a later day, so the first snapshot of day d has null active counts, and a later run writes a new revision with them (the normaliser reads every raw file of day d, whenever it was pulled). A value with no raw source for day d is null.
9. **Seat assignments.** One `SeatAssignment` per distinct `assignee.id` in D: `actor_id` = id as a string, `actor_login` = `login`, `plan` = `plan_type`, `assigned_at` = the earliest `created_at`, `last_active_at` = the latest `last_activity_at`, `last_authenticated_at` = the latest, `pending_cancellation_date` = the latest non-null, `granted_by` = the sorted `assigning_team.slug` values joined by `,` (null if none). `updated_at` is deprecated and ignored [V].
10. **Finality.** Day records from A: `is_final` when the day ended at least four full UTC days before the pull. Records from C: when the month ended more than 7 days before the pull (v1 rule).
11. **Cost check scope.** The v1 check "actor cost per day not above the organization's" does not apply to Copilot: per-person figures are `consumption` and organization figures are `billed` (3.7 rule 5).

### 6.5 Out of scope

Premium-request billing (`premium_request/usage`, only for plans still on request pricing), the general `/settings/billing/usage` and `/usage/summary` endpoints, the 28-day reports, repository and team reports, the AI usage report (a web download with per-model tokens; no API is documented [V https://docs.github.com/en/billing/reference/billing-reports]), the IDE, language and model breakdowns, and the legacy `/copilot/metrics` API (closed 2026-04-02 [V]).

## 7. Layout

```
packages/meterline/                 v2 records, validation, writers, convert_v1
packages/meterline-anthropic/       migrated to v2 (fetcher unchanged)
packages/meterline-litellm/         src/meterline_litellm/{client.py, fetch.py, normalise.py, cli.py}, tests/, tests/fixtures/
packages/meterline-copilot/         src/meterline_copilot/{client.py, fetch.py, normalise.py, cli.py}, tests/, tests/fixtures/
docs/records.md                     rewritten for v2: every field, with its source per connector
docs/api-shapes/                    Anthropic shapes stay where they are
docs/api-shapes/litellm/            one JSON file per endpoint A-C, synthetic values, plus a README naming each file's source
docs/api-shapes/copilot/            one JSON file per endpoint A-E, one users row and one aggregate row file; synthetic values, plus a README naming each file's source
docs/api-shapes/fit/                one synthetic input per tool of section 10
```

Python 3.10+. Each connector depends on `meterline` and `httpx`, has a fake API built on `httpx.MockTransport` (the Copilot fake also serves the download host), and synthetic fixtures only: invented people with `@example.com` emails and invented logins. Commands as in v1: `meterline-litellm backfill --since`, `daily`, `rebuild`; the same for `meterline-copilot`.

## 8. Done-checks

Each check runs on synthetic data, is a test named `test_done_check_<prefix><NN>_*`, and fails on today's `main` (which has no v2 format and neither connector). The build changes no check after it has been shown to fail.

**Shape comparison** (L01, C01) works as v1.1 check 12: keys and nesting must match the shape file, values are ignored, the first element stands for a list, an empty list in the shape file is not compared, fields listed as optional for that endpoint may be absent, and a map whose keys are data (LiteLLM `breakdown.*`, `api_key_breakdown`) is compared by the shape of its values, not its key names.

**Format (F), package `meterline`**
- F01. One example of each of the five record types serialises to JSONL with `record_version` 2 and sorted keys, and reads back equal. Fails today: no `record_version`, no `SeatAssignment`.
- F02. `convert_v1` turns every line of the v1.1 synthetic fixture batches into a v2 line that validates; the converted batch has as many records as the v1 batch, the same set of v1 keys (with `scope` `""`, `grain` `day`, `actor_kind` `user` added), and for each row of the section 4 table the v2 field holds the v1 value it names.
- F03. A `UsageRecord` whose `tokens_input_total` differs from the sum of three non-null input buckets is rejected; one with a null bucket and any total is accepted; 5m + 1h ≠ cache write is rejected.
- F04. A `grain=day` record with `period_end` after `period_start` is rejected; a `grain=month` record from 2026-09-01 to 2026-09-30 is accepted; one ending 2026-09-29 is rejected; with `pulled_at` 2026-10-15, a `grain=month` record for 2026-10-01 to 2026-10-31 is accepted and a `grain=day` record for 2026-10-16 is rejected.
- F05. A line without a nullable counter reads it as null and writes it back as `null`, never `0`.
- F06. Two records that differ only in `grain`, `actor_kind` or `scope` have different keys; two identical keys in one batch fail the batch.
- F07. `cost` without `cost_basis`, `cost_basis` without `cost`, and a `quantities` key `"AI Credit"` are each rejected.
- F08 (format fit). For each of Cursor, OpenAI API, Augment, Kiro and Windsurf, the synthetic input in `docs/api-shapes/fit/` is mapped by the table of section 10 (a test helper, not a connector) and every resulting record validates.

**Anthropic migration (A)**
- A01. All 17 v1.1 done-checks pass against v2 records, with only the assertion changes listed in section 4 (checks 5, 13, 16).
- A02. On every `UsageRecord`, `tokens_input_total` = uncached + read + 5m + 1h and `tokens_cache_write` = 5m + 1h.
- A03. Converting the v1.1 published fixture batch with `convert_v1` gives the same records, byte for byte, as a v2 `rebuild` of the same raw files.

**LiteLLM (L)**
- L01. For each endpoint A-C, the fake proxy's response matches its `docs/api-shapes/litellm/` file by the shape comparison above. Optional for A: any `SpendMetrics` field except `spend`, `prompt_tokens`, `completion_tokens`, `api_requests`.
- L02. A user whose day is split across pages 1 and 2 of A yields one record per model with the sum of both pages.
- L03. A model day with `prompt_tokens` 1,000, cache read 600 and cache write 300 gives `tokens_input_uncached` 100 and `tokens_input_total` 1,000; one with `prompt_tokens` 500, read 600 gives null uncached and a `token_anomalies` entry in the manifest.
- L04. A fixture whose global view has an entity with key `""` (no owner) and whose other entities sum to less than the global view: no per-user call is made for `""`, an `unassigned` record holds exactly the difference per date and model, and `proxy_user` + `unassigned` totals equal the global totals to the cent.
- L05. Without `--end-users` no `end_user` record is written and endpoint B is never called. With it, a fixture with two end users who used different models gives `end_user` records whose model split matches each end user's own fixture data (one B call per end user), and the `proxy_user` records are identical to the run without the switch.
- L06. Spend values 0.1 and 0.2 on two pages sum to exactly `0.30`; a static check finds no `float` on the cost path.
- L07. Running `daily` twice on the same day writes no new records; a value changed in the re-read window writes a new batch where the latest wins.
- L08. A 404 on A stops the run with the message of 5.3 rule 4 (naming the minimum version) and publishes nothing.
- L09. Only users present in the global view's `entities` are called: in a one-window fixture with 50 users in C, 12 of them active, where every view fits in one page, the run makes exactly 13 requests to A (1 global, 12 per-user).

**Copilot (C)**, each run at both levels unless stated
- C01. For each endpoint A-E, the fake API's response, one users NDJSON row and one aggregate row match their `docs/api-shapes/copilot/` files by the shape comparison above, with the optional fields listed in 6.2 A, plus `plan_type` and `assigning_team` in D.
- C02. A report with two download links loads the rows of both files; the fake download host records that no request to it carried an `Authorization` header.
- C03. A row with `ai_credits_used` 12.5 gives a `UsageRecord` with `quantities` `{"ai_credit": "12.5"}`, `cost` `0.125` (serialised `"0.125"`), `cost_basis` `consumption`, and null tokens.
- C04. A row with `totals_by_cli` and `totals_by_copilot_app` gives `copilot_cli` and `copilot_app` records with `tokens_input_total` and `tokens_output` set, three null input buckets, and no `quantities`.
- C05. A row with two `totals_by_feature` entries gives two `ActivityRecord`s whose counters sum to the row's top-level counters; a fixture row whose features do not sum is listed under `feature_sum_mismatch` and the run publishes.
- C06. For a month in which the stored A rows hold logins x and y and one D response pulled that month holds y and z, the run makes exactly three per-person calls to C for that month (x, y, z), and writes `grain=month` records per product and model with `cost_basis` `consumption` and `net_amount` in `extras`.
- C07. Organization daily billing writes one `OrgTotal` per day per model with `cost_basis` `billed` and `cost` = `netAmount`.
- C08. Items with `unitType` `credits` and `ai-credits` both land under `ai_credit`; an item with `unitType` `minutes` lands under `minutes` and is listed in `unknown_units`; two items with the same model and products `Copilot` and `copilot` give one `OrgTotal`.
- C09. A day re-read inside the four-day window whose `ai_credits_used` changed writes a new batch, the latest wins, and the record has `is_final` false.
- C10 (enterprise level). A user listed twice in D gives one `SeatAssignment` with the earliest `assigned_at` and both team slugs in `granted_by`; `SeatSnapshot.seats` equals `total_seats`. A first run on day d writes the day's `SeatSnapshot` with null active counts; a run on d+1 that reads B for day d writes a new revision of day d's snapshot with the same `seats` and the active counts set.
- C11. A scripted 403 with `retry-after` and a 429 with `x-ratelimit-reset` are both waited out; 300 requests never exceed the configured rate.
- C12. A scan of the package, its fixtures and a run's `raw/`, `runs/` and log output finds no token value and no email outside `@example.com`.

## 9. Mapping to the standards (no exporter in v2)

| v2 field | FOCUS 1.4 column | OpenTelemetry GenAI |
|---|---|---|
| `cost` | `EffectiveCost` (or `BilledCost` when `cost_basis` is `billed`) | none |
| `cost_list` | `ListCost` | none |
| `currency` | `BillingCurrency` | none |
| `quantities[unit]` | `ConsumedQuantity` with `ConsumedUnit` = unit | none |
| `period_start`, `period_end` | `ChargePeriodStart`, `ChargePeriodEnd` (exclusive end: `period_end` + 1 day) | none |
| `provider` | `ServiceProviderName` | `gen_ai.provider.name` (Copilot and LiteLLM are custom values) |
| `product` | `ServiceName`; `ServiceCategory` = "AI and Machine Learning" | none |
| `model` | `SkuId` or a tag (1.5 draft: `ModelId`) | `gen_ai.response.model` |
| `actor_id`, `actor_email` | `Tags` (1.5 draft: `PrincipalId`) | `user.id` / `enduser.id` (flagged as personal data) |
| `tokens_input_uncached`, `tokens_cache_read`, `tokens_cache_write`, `tokens_output` | one row each (1.5 draft: `TokenDirection` Input/Output, `TokenCacheAction` Uncached/Read/Write) | `gen_ai.usage.input_tokens` = uncached + read + write; `gen_ai.usage.cache_read.input_tokens`; `gen_ai.usage.cache_write.input_tokens`; `gen_ai.usage.output_tokens` |
| `tokens_reasoning` | (1.5 open item) | `gen_ai.usage.reasoning.output_tokens` |

Sources: https://focus.finops.org/docs/specification/v1-4/ ; the FOCUS 1.5 working draft (PR #2606 token properties, PRs #2360 and #2553 requester columns, PR #2442 model columns) in https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec ; https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md (status Development).

## 10. Format fit for five more tools (check F08)

| Tool | Source | Maps to | Notes |
|---|---|---|---|
| Cursor | `POST /teams/filtered-usage-events` (per event: `userEmail`, `model`, `tokenUsage.inputTokens`, `cacheReadTokens`, `cacheWriteTokens`, `outputTokens`, `totalCents`; `chargedCents`) [V https://cursor.com/docs/account/teams/admin-api] | `UsageRecord`, summed per day, user and model: `actor_email` = `userEmail`, buckets one to one, `cost_list` = Σ `totalCents` / 100, `cost` = Σ `chargedCents` / 100, `cost_basis` `billed` | Fractional cents: Decimal. `requestsCosts` → `quantities["request_unit"]`. |
| Cursor | `POST /teams/daily-usage-data` | `ActivityRecord`: `suggestions_shown` / `accepted` = tabs shown / accepted, `lines_added` / `removed` = accepted lines added / deleted, `edits_accepted` / `rejected` = accepts / rejects | Requests per mode → `counters`. |
| OpenAI API | `/v1/organization/usage/completions`, `group_by=user_id,model`, `bucket_width=1d` (`input_tokens`, `input_cached_tokens`, `input_cache_write_tokens`, `input_uncached_tokens`, `output_tokens`, `num_model_requests`) [V SDK types, https://unpkg.com/openai@7.3.0/resources/admin/organization/usage.d.ts] | `UsageRecord` with `actor_kind` `api_key` or `user`, buckets one to one, `tokens_input_total` = `input_tokens`; cost null | Costs API (per project, no user) → `OrgTotal` with `scope` = project. |
| Augment | analytics API per user per day per model (`credits_consumed`, `cost_metrics.billed_amount_usd`, token buckets) [V https://docs.augmentcode.com/analytics/api-reference.md] | `UsageRecord`: `quantities["credit"]`, `cost` = billed amount, `cost_basis` `billed` | `service_account_name` → `actor_kind` `service_account`. |
| Kiro | daily per-user CSV (`Date`, `UserId`, `User_Email`, `Client_Type`, `Credits_Used`, `Overage_Credits_Used`, `Total_Messages`) [V https://kiro.dev/docs/enterprise/monitor-and-track/user-activity/] | `UsageRecord`: `quantities` `{"credit", "overage_credit"}`, cost null; `ActivityRecord`: `messages` | `Client_Type` → `scope`. |
| Windsurf | v2 consumption, `granularity=daily&group_by=user,model_uid` (`user_id`, `user_email`, `prompt_credits`, `flex_credits`) [V https://docs.devin.ai/desktop/accounts/api-reference/get-consumption.md] | `UsageRecord`: `quantities` `{"prompt_credit", "flex_credit"}`, tokens null, cost null | Two units on one record: the reason `quantities` is a map. |

## 11. Out of scope for v2

Identity matching across sources (D3); exporters (D5); connectors beyond LiteLLM and Copilot (D7); the LiteLLM database path (D2); seat-fee allocation, pricing from tokens, contract discounts and currency conversion (v1); removing duplicate traffic between sources (section 1); alerting; any mapping to a specific downstream schema.

## 12. Open questions for acceptance

1. LiteLLM `prompt_tokens` includes cache for Anthropic and Bedrock [V]; for OpenAI-style providers it follows OpenAI's convention, where cached tokens are part of the prompt count [I]. A live run should confirm it before `tokens_input_uncached` is trusted for those providers.
2. The minimum LiteLLM version (5.1) is not resolved: the daily cache-token columns and per-key breakdown arrived after v1.65.0; the builder resolves the first stable release containing both.
3. The exact envelope of `/user/list` (5.2 C) and the exact layout of one Copilot `*-1-day` aggregate row (6.2 B) are to be transcribed by the builder, with their sources named.
4. Copilot: no documented example shows a populated `totals_by_model_feature`; the signed-URL lifetime is undocumented; a 204 on enterprise `users-1-day` is documented only at organization level.
5. Copilot: the classic-token scopes for the billing endpoints are not documented; enterprise billing may require a GitHub App token.
6. Copilot: 1 AI credit = USD 0.01 is taken as fixed [V]. If GitHub changes it, rule 6.4.1 must read the price from billing (`pricePerUnit`) instead.
7. Whether the usage-report endpoints are still in public preview [I]; the seats endpoints are [V].
