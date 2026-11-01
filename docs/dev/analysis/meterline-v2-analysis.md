---
type: analysis
status: current
updated: 2026-10-10
feeds: docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md (the accepted v2 spec)
---

# meterline v2: what a provider-neutral format and the Copilot and LiteLLM connectors would require

Desk research from public documentation and public source code only, read on 2026-10-10. No credential was used and no live API or proxy was called. Load-bearing facts carry a grounding label: **[V]** read on the cited page today, **[I]** inferred from verified facts or not found anywhere. Every URL is listed in section 8.

## 1. Summary (bad news first)

1. **The v1 record format cannot hold GitHub Copilot without change.** Copilot reports no tokens for most of its usage: only the CLI and the Copilot app carry token counts, and without a cache split [V]. Cost is expressed in **AI credits** (1 credit = USD 0.01) [V]. The v1 `UsageRecord` makes five token fields mandatory and has no place for a quantity in another unit. The v1 `ActivityRecord` holds Claude's counters (conversations, artifacts, skills, connectors...), not Copilot's (code generations, acceptances, suggested lines, IDE, feature).
2. **Per-person cost from Copilot is a consumption figure, not an invoice.** The per-user daily metrics row carries `ai_credits_used`, documented as "for consumption analysis, not invoicing totals" and not split by model [V]. The billing API does split by model and gives gross, discount and net, but its items carry **no user and no day column**. Per-user or per-day cost needs one call per user and per period [V]. Included credits are pooled across the whole billing entity, so a per-user "net" is not a clean cost [V for pooling; I for the consequence].
3. **LiteLLM fits the v1 grain well, but its token semantics differ.** The proxy keeps a pre-aggregated daily table per user, day, key, model, provider and endpoint, with real cache-read and cache-write columns [V]. But its `prompt_tokens` **includes** cache tokens, while Anthropic's `uncached_input_tokens` (v1 `tokens_input`) **excludes** them [V]. LiteLLM also has no 5-minute/1-hour cache split, and its `spend` is LiteLLM's own estimate from its price list, held as a float, not the provider's invoice [V].
4. **"The user" means different things in each source.** In Anthropic it is a seat holder with an email. In Copilot it is a GitHub login with a numeric id and no email [V]. In LiteLLM it is the *owner of the virtual key*, which is different from the *end user* named in the request [V]. A shared key puts everyone's spend on one owner [V].
5. **No existing open-source tool does what meterline does.** Reading provider admin APIs into per-person daily records is done today only by closed SaaS tools [V for the two checked, I for the market]. The two public standards are useful for naming but are still moving. The OpenTelemetry GenAI conventions are all "Development" [V]. FOCUS 1.4 has no AI columns and no user column, and the AI token work sits in an unreleased 1.5 draft [V].

**Recommendation in one line:** make v2 a wide, provider-neutral format. Tokens become optional, in four disjoint buckets. The record adds a map of billed quantities by unit (for credits), a cost-basis flag, an optional sub-scope, and a small map for source-specific numbers. Section 2.5 checks this shape against fifteen comparable tools: it holds most of them. Build it with meterline-anthropic migrated first, then LiteLLM (simplest and closest to v1), then Copilot (most moving parts). Each connector's done-checks run on synthetic data alone. A live-credential check comes later, as a separate step.

## 2. The sources

### 2.1 GitHub Copilot

| Need | What is available | Grain | Label |
|---|---|---|---|
| Activity per person per day | Usage metrics report `users-1-day` (enterprise or organization scope). The API answers with signed download links to NDJSON files, one row per user per day. | user × day | [V] |
| Cost per person per day | `ai_credits_used` in the same row: credits, all models together, "not invoicing totals". | user × day | [V] |
| Cost per model | Billing `ai_credit/usage` with `year`, `month`, `day`, `user`, `model`, `product`, `organization`, `cost_center_id` filters. Items: `product, sku, model, unitType, pricePerUnit, grossQuantity, grossAmount, discountQuantity, discountAmount, netQuantity, netAmount`. No user or date column in the items. | whatever the filters fix: one call = one (period, user?) slice, rows per model | [V] |
| Tokens | `totals_by_cli.token_usage` and `totals_by_copilot_app.token_usage` hold `prompt_tokens_sum`, `output_tokens_sum` and `avg_tokens_per_request`. There is no cache split and no model split. Per-model tokens with cache read/write exist only in the "AI usage report", which is a download from the web interface, sent by email, 31 days maximum. No API is documented for it. | user × day (CLI, app only) | [V]; no API [I] |
| Activity counters | `user_initiated_interaction_count`, `code_generation_activity_count`, `code_acceptance_activity_count`, `loc_suggested_to_add_sum`, `loc_suggested_to_delete_sum`, `loc_added_sum`, `loc_deleted_sum`, `used_*` flags, and breakdowns by feature, IDE, language, model and feature (`totals_by_model_feature`, never populated in any documented example), CLI and app sessions/requests/prompts. | user × day, plus breakdown arrays | [V] |
| Seats | `GET /orgs/{org}/copilot/billing/seats` and `/enterprises/{e}/copilot/billing/seats`: `total_seats` plus one object per seat (`assignee.login`, `assignee.id`, `plan_type`, `created_at`, `pending_cancellation_date`, `last_activity_at`, `last_authenticated_at`, `assigning_team`). Public preview. Current state only: a connector must keep its own daily history. A user can appear more than once in the enterprise list. | snapshot | [V]; history [I] |
| Active users | Aggregate reports `enterprise-1-day` / `organization-1-day` hold `daily_active_users`, `weekly_active_users`, `monthly_active_users` (plus per-surface variants). | org × day | [V] |
| Organization totals | Billing `ai_credit/usage` with `day=` and no `user`: per model, gross, discount and net for the whole entity. This is the only invoice-grade figure. | org × day × model | [V]; "invoice-grade" [I] |
| Auth | Metrics need "View Enterprise/Organization Copilot Metrics": a classic token with `manage_billing:copilot` or `read:enterprise` / `read:org`, or a GitHub App token; fine-grained personal tokens work at organization scope only. Enterprise seats work with a **classic token only**. Enterprise billing needs a GitHub App token with "Enterprise billing" read, and the classic scopes are not documented. Organization billing paths are `/organizations/{org}/...`, not `/orgs/`. | | [V] |
| History | Metrics from 2025-10-10, up to one year back. Billing: 24 months. Seats: none. | | [V] |
| Lag | Metrics "typically finalize within three full UTC days". A 2026-07-02 fix raised earlier `ai_credits_used` values retroactively. Re-pull at least the last 3 to 4 days. | | [V]; window [I] |
| Call budget | 5,000 requests/hour for a user token, 15,000/hour for a GitHub App on Enterprise Cloud; at most 100 concurrent; 900 points per minute per endpoint. Metrics: 1 call per day plus the file downloads. Seats: one page per 100 seats. Billing per user per day: users × days calls (1,000 people × 30 days = 30,000 calls a month, about 6 hours at 5,000/hour). Billing per user per month: 1,000 calls a month. | | [V] limits; [I] arithmetic |
| Version | Header `X-GitHub-Api-Version: 2026-03-10`. Without it the default is 2022-11-28. The legacy `/copilot/metrics` API was closed on 2026-04-02, and two older ones on 2026-03-02. | | [V] |

Pitfalls: the AI-credit `unitType`, `sku` and `product` strings differ between the documented examples ("credits" vs "ai-credits"), so nothing may be keyed on them [V]. The signed-URL lifetime is undocumented [I]. Whether `users-28-day/latest` holds one row per day or one row per user is not stated, so the 1-day report is the safe source [I].

### 2.2 LiteLLM proxy

Read from the source code of the latest stable release, v1.104.2 (2026-10-08), and from docs.litellm.ai.

| Need | What is available | Label |
|---|---|---|
| Tokens and spend per person per day per model | Table `LiteLLM_DailyUserSpend`: one row per (`user_id`, `date`, `api_key`, `model`, `custom_llm_provider`, `mcp_namespaced_tool_name`, `endpoint`) with `prompt_tokens`, `completion_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `spend`, `api_requests`, `successful_requests`, `failed_requests`, and `model_group`. `date` is the UTC day of the request. Several rows per (user, day, model) must be summed. | [V] |
| API path | `GET /user/daily/activity?start_date&end_date&user_id&model&api_key&page&page_size` (page_size ≤ 1000). It returns `results[]` per date with `metrics` and a `breakdown` by models, model groups, providers, endpoints, keys and entities. `breakdown.entities[user]` has **no model split**. A user × model grid needs either one call per user (`/user/list` to enumerate) or a rebuild from `breakdown.models[m].api_key_breakdown[key].metadata.user_id`. Pages cut through raw rows, so one date can span two pages and must be merged. | [V]; rebuild [I] |
| Database path | A read-only role on the proxy's Postgres can sum the daily table in one query and join `LiteLLM_UserTable.user_email`. It works technically, but it is **not documented or supported**, and the schema changed often in 2026. | [V] not documented; [I] works |
| Per-request detail | `GET /spend/logs/v2` (since v1.80.8): page_size ≤ 1000. The total is clamped at 10,000 (`total_is_capped`), but paging past it works. Ordering has no tiebreaker, so read closed past windows and de-duplicate on `request_id`. Cache tokens sit inside the `metadata` JSON there. The old `/spend/logs` is deprecated and capped at 10,000 rows since 2026-08-27. | [V] |
| Avoid | `/global/spend/report` and `/{key,user,team,organization}/spend/report` are licence-gated in code (`premium_user`) and have no per-day × user × model grid. `/user/info` spend is a running budget counter that resets. Tag tables double-count across tags. | [V] |
| Cost | `spend` is LiteLLM's USD estimate from its model price map, a float. Custom pricing and discounts are operator settings. Committed-use rates are not reflected unless configured. Unknown models show 0. | [V] |
| Identity | Daily `user_id` = the **owner of the virtual key**, empty for keys without an owner. The end user (the request's `user` field, a customer header or a JWT claim) goes to `LiteLLM_DailyEndUserSpend` / `/customer/daily/activity` (admin or admin-viewer only). The proxy records identity itself, so traffic routed to any backend (Bedrock included) keeps it. Only `LiteLLM_UserTable` has an email. | [V]; Bedrock [V code, I conclusion] |
| Auth | Role `proxy_admin_viewer` is read-only and may call every GET route above. `internal_user` keys see only their own data. | [V] |
| Versions | `/user/daily/activity` since v1.65.0 (2025-03), team daily since v1.67.0, `/spend/logs/v2` since v1.80.8 (2025-12). The pre-release v1.105.0-rc.3 adds `/{entity}/daily/activity/export` (CSV/JSON with `user_id`, `user_email`, `model`, tokens, spend), which would be the simplest bulk path once stable. `disable_spend_logs` does not stop the daily tables; `disable_spend_updates` stops everything. | [V] |
| History and lag | The daily user table is not in the retention clean-up job, so its history is as long as the proxy's. Rows are written in batches (default every 10 s; 60 s recommended in production). Re-pull the last 1 to 2 UTC days. | [V]; kept indefinitely [I] |
| Call budget | No rate limit on these routes was found. With about 1,000 people, the daily table has a few thousand rows a day, so 3 to 5 pages per day; a year's backfill is around 1,000 to 2,000 calls. The per-user mode costs one call per user per window. | [I] |

### 2.3 Anthropic (today's connector, for comparison)

Per user × day × product × model: tokens in four buckets (uncached input, output, cache read, cache write split 5m/1h), cost after discount and at list price in cents. Per user × day × product: activity counters. Per org × day: seats and active users; organization tokens and cost. One API key, 60 requests/minute, data from 2026-01-01. See `docs/records.md`.

### 2.4 Side by side

| | Anthropic | Copilot | LiteLLM |
|---|---|---|---|
| Person id | `user_id` + email | `user_id` (int) + `user_login`, no email | key owner `user_id` (+ email from the user table); end user separate |
| Tokens | yes, 4 buckets + 5m/1h split, input **excludes** cache | CLI and app only, input/output, no cache | yes, `prompt_tokens` **includes** cache, cache read/write separate |
| Model on per-person rows | yes | only through billing calls per user | yes |
| Cost unit | USD cents | AI credits (USD 0.01) | USD float |
| Cost meaning | provider amount after discount, plus list | consumption (metrics) or billed gross/discount/net (billing, no user column) | proxy estimate from a price list |
| Activity | Claude product blocks | code generation/acceptance, lines, features, IDE, CLI/app sessions | requests (succeeded/failed) only |
| Seats | daily series | snapshot only | none |
| Org totals | daily tokens and cost | daily credits per model (billing) | sum of the daily tables |

### 2.5 Other AI tools: could the same format and connector pattern hold them?

Added at the owner's request, before the interview: a check of comparable coding assistants and AI providers against the proposed v2 format (section 4, option A). Same method: public documentation only, read 2026-10-10.

| Source | Admin source | Per person | Per model | Tokens | Cost unit | Fit | Effort |
|---|---|---|---|---|---|---|---|
| **Cursor** (team/enterprise) | REST admin API: `POST /teams/filtered-usage-events` (one row per event: `userEmail`, `model`, `tokenUsage{inputTokens, outputTokens, cacheWriteTokens, cacheReadTokens, totalCents}`, `chargedCents`, `requestsCosts`), `POST /teams/daily-usage-data` (per user per day: lines added/deleted, accepted lines, tabs shown/accepted, applies/accepts/rejects, chat/agent requests), `/teams/members`, analytics API (daily active users, per-user per-model message counts) [V] | yes (email) | yes (events) | all four buckets [V] | fractional US cents [V] | good | medium |
| **Windsurf** (now "Devin Desktop", Cognition) | Analytics API v2 (alpha): `consumption?granularity=daily&group_by=user,model_uid` → `user_id`, `user_email`, `prompt_credits`, `flex_credits` (or compute units on newer plans); active users; accepted lines out. v1: per-user range totals, credit balance, seat count [V] | yes | yes | none [V] | credits only, several kinds at once [V] | partial | small-medium |
| **Google Antigravity** (in Gemini Enterprise) | No usage API or export [V by absence]. Per-person data only from an opt-in Cloud Logging entry per inference call (`labels.user_id`, `labels.model`, `labels.model_provider`; `promptTokenCount` including cache, `cachedContentTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`); 30-day default log retention. Org cost from the Cloud Billing export (no user column). Seats from the licence API [V] | yes, if logging is on | yes | input (incl. cache), cache read, output, reasoning; no cache write [V] | none per person; billed cost per project [V] | partial | medium-large |
| **Gemini Code Assist** | Being withdrawn: no new subscriptions from 2026-10-09, renewals end 2027 [V]. Cloud Logging acceptance events per user; Cloud Monitoring org aggregates | yes (logs) | no | no | per-seat licence | poor, and sunsetting | not worth it |
| **OpenAI API** | Admin key: `/v1/organization/usage/completions` (group by `user_id`, `api_key_id`, `project_id`, `model`; `input_tokens` **includes** cache, plus `input_cached_tokens`, `input_cache_write_tokens`, `input_uncached_tokens`, `output_tokens`, `num_model_requests`); `/v1/organization/costs` (daily by project and line item; no user, no model) [V] | yes (tokens), no (cost) | yes | all four buckets [V] | USD, org/project only | good | medium |
| **OpenAI Codex** (enterprise) | Per-user per-day analytics endpoint with threads, turns, credits, tokens per model, lines added/removed [I: third-party docs only] | yes | yes | no cache write | credits | good, unconfirmed | medium |
| ChatGPT Enterprise | Dashboard and downloadable reports; no analytics API [V] | — | — | — | — | poor | n/a |
| **Amazon Q Developer** | Daily per-user CSV written to S3 (~45 metric columns: messages, inline suggestions shown/accepted, accepted lines...); no email, tokens or cost [V]. CloudWatch org metrics | yes (user id) | no | no | per seat | partial | small-medium |
| **Kiro** (AWS) | Daily per-user CSV to S3: `Date`, `UserId`, `User_Email`, `Client_Type`, `Credits_Used`, `Overage_Credits_Used`, `Total_Messages`, `<model>_messages` [V] | yes | message counts | no | credits (USD 0.04 per overage credit) | good | small-medium |
| **Augment Code** | Analytics API: per-user credits by date and model; `cost_metrics` with `billed_amount_usd`, estimated cost, all four token buckets [V] | yes (email or service account) | yes | all four buckets [V] | USD and credits | good | small |
| **Amp** (Sourcegraph) | Official OpenAPI: per-user per-day lines and USD usage plus list-price estimate; per-thread per-model tokens (all four buckets) [V] | yes | per thread | yes | USD | good | small (daily), large (per thread) |
| JetBrains AI | Analytics API: per-date, per-user, per-tool credits; active users; token-lived service account [V] | yes | by tool, not model | not found | credits | good | medium |
| Tabnine, Sourcegraph Cody | Usage APIs exist; response schemas not public [V/I] | yes | unknown | unknown | unknown | partial | medium |
| Mistral | Monthly billing by category; chat per-user stats over a window [V] | window only | partly | partly | monthly | partial | medium |
| Claude through Amazon Bedrock | Invocation logs (`identity.arn`, `modelId`, input/output tokens; also prompt bodies, never to be read); cost per IAM principal through the cost and usage report [V] | per IAM principal | yes | input/output | USD per principal | partial | large |
| Azure OpenAI / Foundry | Azure Monitor token metrics per deployment, no user; cost metric is an estimate [V] | no | yes | yes | USD estimate | partial | medium-large |
| Microsoft 365 Copilot | Microsoft Graph usage reports: rolling 7–180-day aggregates per licensed user (masked by default), daily enabled/active users per app [V] | window only | no | no | none | poor (people), good (seats) | medium |

**What this shows.** The pattern of a fetcher that keeps raw files, a normaliser that reads only raw files, batches and latest-batch-wins revisions carries over to every source above. Sources that write files (Amazon Q and Kiro to S3) or logs (Antigravity, Bedrock) need a different fetcher, but the normaliser and record format are the same [I]. The record format of option A holds most of them, with six adjustments, now folded into section 4:

1. **Several billing units at once.** Windsurf reports prompt credits and flex credits together, and Kiro reports included and overage credits. A single `quantity` + `quantity_unit` pair is too narrow, so it becomes a `quantities` map (unit → amount).
2. **Cost without money.** Credit-only sources (Windsurf, Kiro, JetBrains) have no currency cost, so `cost` stays null and the credits sit in `quantities`. Credits are never converted to money by meterline.
3. **A third kind of identity.** Service accounts (Augment, Cursor cloud agents), API keys and projects (OpenAI) and IAM principals (Bedrock) appear beside people. `actor_kind` takes these values; cost that belongs to no actor at all (OpenAI costs, cloud billing exports) goes to `OrgTotal`.
4. **A sub-scope inside an organization.** OpenAI projects, Kiro client types, Azure deployments and seat tiers need one more key dimension. A `scope` field (empty string by default) is added to the keys of `UsageRecord`, `OrgTotal` and `SeatSnapshot`.
5. **Two more common activity counters.** Suggestions shown and accepted exist in Copilot, Cursor, Amazon Q and Gemini Code Assist, so they join the generic core. `lines_added` / `lines_removed` are defined as lines written by the AI and kept, as every source above means them.
6. **Window aggregates and opt-in data.** Some sources give only multi-day windows (Microsoft 365, Windsurf v1). v1 already allows `period_end` after `period_start`, so this needs no change, only a rule that a reader never mixes grains. Where data depends on an admin switch (Antigravity logging), "not reported" must read as null, never as 0. Content fields (prompts, responses) are never fetched.

## 3. Gap table: v1 format against the new sources

| v1 element | Copilot | LiteLLM | Change needed |
|---|---|---|---|
| `provider` (in every key) | `github_copilot` | `litellm`, but the serving provider (`bedrock`, `anthropic`, `openai`...) is a separate fact | Keep `provider` as the reporting source. Add an optional `model_provider` for the provider that served the model. |
| `org_id` | enterprise or organization slug/id | none in the daily table; the proxy itself is the scope | The connector sets it from configuration (a proxy name). It must never be empty. |
| `actor_id`, `actor_email`, `actor_deleted` | numeric id, login, no email | key owner id, email via join, no deleted flag | Add an optional `actor_login`. `actor_email` stays nullable. `actor_deleted` becomes nullable ("not known"). Add an optional `actor_kind` (`user`, `proxy_user`, `end_user`, `unassigned`). |
| `product` | `copilot` (or feature: `code_completion`, `chat_panel_agent_mode`...) | `endpoint` (chat, responses, messages...) | Fits as is: values are recorded as received. |
| `model` | none on the metrics row; present on billing rows | `model` and `model_group` | Fits; empty string for "all models". Add an optional `model_group`. |
| 5 mandatory token fields | mostly absent | present, but different semantics | **Breaks.** Tokens become nullable (null = not reported; 0 = reported zero). Define four disjoint buckets. The 5m/1h split moves to optional fields. |
| `requests`, `web_search_requests` | CLI/app `request_count`; no web search | `api_requests`; no web search | `web_search_requests` becomes nullable or moves to the extras map. |
| `cost`, `cost_list`, `currency` | credits; USD price fixed per credit | USD estimate | Add `quantities` (unit → amount) and `cost_basis`. Cost can then be "provider-billed", "provider-reported consumption" or "estimated from a price list". |
| `is_final` | metrics final after 3 UTC days; billing after month close | final once the day has closed plus a flush delay | The rule becomes per connector, stated in each spec. The field stays. |
| `cost_only` | n/a | n/a | Unchanged. |
| `ActivityRecord` counters | about a dozen Copilot counters, none of them Claude's | `successful_requests`, `failed_requests` | **Breaks.** The 13 named counters are Claude's. Move to a small generic core plus a `counters` map, or keep the named fields nullable and add the map. |
| `SeatSnapshot` | `total_seats` and pending figures from a snapshot; active counts from the aggregate report | none | Fits if `daily_active` / `weekly_active` / `monthly_active` become nullable. A per-person seat record is new, if wanted (question 7). |
| `OrgTotal` | billing per day per model, in credits | sum of the daily tables | Same changes as `UsageRecord` (nullable tokens, quantity/unit, cost basis). |
| Validation "per-user cost per day ≤ org cost" | units must match (credits vs credits) | estimates vs estimates | Applies only within one `cost_basis` and unit. |
| Money as `Decimal` | billing amounts are JSON numbers (floats) | `spend` is a float | Parse JSON numbers straight into `Decimal` (`parse_float=Decimal`), never through `float`. The v1 "no float on the cost path" rule then holds unchanged. |

## 4. Format change options

All three keep the v1 rules on files, batches, revisions, sorting and validation. They differ in how a record carries measures.

### Option A: widen the four records (recommended)

- `UsageRecord`: four disjoint, nullable token buckets: `tokens_input_uncached`, `tokens_cache_read`, `tokens_cache_write`, `tokens_output`. Optional detail: `tokens_cache_write_5m`, `tokens_cache_write_1h`, `tokens_reasoning`. Plus `quantities` (a map of billing unit → amount, for example `{"ai_credit": "12.5"}`), `cost_basis`, `model_provider`, `model_group`, `actor_login`, `actor_kind`, `scope` (in the key, empty by default), and `extras` (a flat map of name → number for anything source-specific).
- `ActivityRecord`: a generic core of nullable counters (`sessions`, `messages`, `requests`, `suggestions_shown`, `suggestions_accepted`, `lines_added`, `lines_removed`, `edits_accepted`, `edits_rejected`, `commits`, `pull_requests`) plus a `counters` map for the rest (`conversations`, `artifacts`, `tabs_shown`...).
- `SeatSnapshot` and `OrgTotal` gain `scope` in their key; `OrgTotal` gains the same measures as `UsageRecord`.
- Section 2.5 checked this shape against fifteen other tools; the adjustments it needed are already folded in here.
- **Breaks in meterline-anthropic:** one rename (`tokens_input` → `tokens_input_uncached`), the cache-write total becomes a sum, and five Claude-only activity fields (`conversations`, `artifacts`, `skills_used`, `connectors_used`, `web_searches`) move into `counters`. Normaliser mapping only; fetcher untouched.
- **Breaks in the 17 done-checks:** 5 (asserts on `tokens_input`), 13 (the expected activity numbers name the moved fields) and 16 (asserts the five token fields are 0) need their field names updated. 10, 17 and the rest read only `cost`, `org_id`, `key()` or manifests, and are unchanged. Check 12 (shapes) is unaffected. One new check proves a v1 record converts to v2 without loss.
- **Pro:** one row per fact, still readable by a spreadsheet or a dashboard. The FOCUS long form can be generated from it. **Con:** the record has more nullable fields, and every reader must handle null.

### Option B: long format, one record per measure

- A single `Measure` record keyed by (provider, org, day, actor, product, model, **measure**), with `quantity`, `unit` and optional cost. Tokens become four rows (direction × cache action), as in the FOCUS 1.5 draft.
- **Breaks:** everything. Every consumer, all of `records.md`, and checks 5, 7, 10, 13 and 16 change. Revisions (latest batch per key) still work.
- **Pro:** never needs a schema change for a new measure; close to FOCUS 1.5. **Con:** a grouped, pivoted query for the simplest question ("cost per person per month"), and a big move away from an accepted v1.

### Option C: keep v1 and add a fifth record

- Keep `UsageRecord` as it is for token-billed sources and add a `ConsumptionRecord` (quantity, unit, cost) for Copilot. Rename nothing.
- **Breaks:** nothing in meterline-anthropic.
- **Con:** LiteLLM still does not fit v1 (no 5m/1h split, different input semantics), so either it gets fake zeros or a third shape. A consumer has to union two record types to answer "cost per person". It also hard-codes the Anthropic shape as "the" usage record.

**Recommendation: A.** It is the smallest break that makes all three sources honest. The disjoint token buckets match both v1 and the FOCUS 1.5 draft, and the conversion rules from inclusive sources (LiteLLM, OpenTelemetry) are one subtraction.

## 5. Existing tools and standards

| Name | What it covers | Licence | Decision | Why |
|---|---|---|---|---|
| LiteLLM spend tracking | per-request and daily spend inside the proxy | MIT outside `enterprise/` | **source** (a connector), align names | It holds exactly the data wanted. Its field names match Anthropic's wire names. It does not read provider admin APIs. |
| Langfuse | request tracing, cost per trace | MIT outside `ee/` | ignore | Wrong grain (per call); no admin-API ingestion. |
| Helicone | proxy logging and cost | Apache-2.0 | ignore | Wrong grain; reported to be in maintenance mode [I]. |
| OpenLIT | OpenTelemetry instrumentation | Apache-2.0 | ignore | Instruments calls; no admin-API data. |
| OpenCost | Kubernetes and cloud infrastructure cost | Apache-2.0 | ignore | Infrastructure only. |
| OpenMeter | generic metering and billing | Apache-2.0 | ignore | A billing engine with no AI schema. |
| Closed FinOps tools (FOCUS ingesters) | AI provider costs, some read Anthropic and OpenAI admin APIs | proprietary | ignore; reach them through FOCUS | Closed. A FOCUS file is the bridge. Neither tool checked documents Copilot ingestion [V for the two checked]. |
| OpenTelemetry GenAI conventions | per-call token attributes: `gen_ai.usage.input_tokens` (includes cache), `output_tokens`, `cache_read.input_tokens`, `cache_write.input_tokens`, `reasoning.output_tokens`; `gen_ai.provider.name` | Apache-2.0 | **align** names in docs; no export in v2 | The only shared token vocabulary. Status Development; no cost, no user attribute; moved to its own repository in 2026. |
| FOCUS 1.4 | billing rows: `BilledCost`, `EffectiveCost`, `ListCost`, `ConsumedQuantity`, `ConsumedUnit`, `PricingUnit`, `ServiceCategory` ("AI and Machine Learning") | spec repo licence not asserted | **align** cost names; an **export** is optional (question 5) | Cost columns map cleanly. There is no user column (only Tags). |
| FOCUS 1.5 draft | `TokenDirection` (Input/Output) and `TokenCacheAction` (Uncached/Read/Write/Other) in `SkuPriceDetails`, `PrincipalId`, model columns, consumption currencies | same | align on concepts only | Unreleased (milestone due 2026-10-01, still open) and names already changed once. Its disjoint token buckets match option A. |

## 6. Effort and risk per piece

| Piece | Effort | Risk | Synthetic data alone? | Needs a live credential for |
|---|---|---|---|---|
| v2 record format in `meterline` (types, validation, writers, a v1 → v2 converter) | small | low: pure code, existing tests | yes, fully | nothing |
| Migrate meterline-anthropic and its 17 checks | small | low: normaliser mapping and check field names; byte-for-byte rebuild must hold | yes, fully | nothing new (its own live check is still open from v1) |
| `meterline-litellm` (API path) | medium | medium: page merge by date, key-owner vs end-user identity, float spend, version spread | yes, with a fake proxy built from the documented response models | confirming the live shape on the target version; real key ownership patterns |
| `meterline-litellm` (database path, if chosen) | medium | medium-high: unsupported, schema changes with every release | yes, against a local Postgres seeded with synthetic rows | read-only role; target schema version |
| `meterline-copilot` metrics (activity, credits, tokens for CLI/app) | medium | medium: two-step download, NDJSON, 3-day finality, retroactive credit fixes | yes, fake API plus fake signed-URL host | live NDJSON row shape, populated `totals_by_model_feature`, signed-URL lifetime |
| `meterline-copilot` billing (credits per model, per org per day, optionally per user) | medium | high on call budget if per user per day; documentation inconsistent on unit strings | yes | real unit strings, rate-limit behaviour, which token type the account allows |
| `meterline-copilot` seats | small | low: public preview, may change; enterprise endpoint is classic-token only | yes | nothing beyond the shape |
| FOCUS export (if in scope) | small-medium | medium: 1.5 not released | yes | nothing |

## 7. Questions for the owner

1. Copilot cost grain: per person per month (billing per user per month) with daily cost only at organization level, or per person per day (billing per user per day, users × days calls)? Or credits from the metrics report only?
2. LiteLLM access path: the admin API with a read-only admin-viewer key, or read-only database access; and which LiteLLM version the target proxy runs.
3. Identity across sources: which field joins a person across sources (email, login, LiteLLM user id), and whether meterline should carry any mapping or leave it to the consumer as in v1.
4. Is breaking the v1 record format acceptable before the first public release?
5. Is a FOCUS or OpenTelemetry export in scope for v2?
6. Build order, and what "done" means for each connector without a live credential.
7. Scope of v2 beyond Copilot and LiteLLM: whether any of the section 2.5 sources (Cursor, the OpenAI API, Augment, Kiro...) joins v2, or v2 only proves the format can hold them (a mapping table in the spec, plus one synthetic record per source in a done-check).
8. Raised by the analysis: LiteLLM's actor (key owner, end user, or both as two record sets); Copilot scope (enterprise or organization endpoints); whether a per-person seat record is wanted; how to avoid counting the same traffic twice when LiteLLM routes to a provider that has its own connector.

## 8. Sources (all read 2026-10-10)

GitHub Copilot:
- https://docs.github.com/en/rest/copilot/copilot-usage-metrics (endpoints, envelopes, auth, availability)
- https://docs.github.com/en/copilot/reference/copilot-usage-metrics/copilot-usage-metrics (per-user fields, `ai_credits_used` wording, token fields)
- https://docs.github.com/en/copilot/reference/copilot-usage-metrics/example-schema (documented examples)
- https://docs.github.com/en/copilot/reference/copilot-usage-metrics/reconciling-usage-metrics (three-day finality)
- https://docs.github.com/en/enterprise-cloud@latest/rest/billing/usage?apiVersion=2026-03-10 and https://docs.github.com/en/free-pro-team@latest/rest/billing/usage?apiVersion=2026-03-10 (billing endpoints and shapes)
- https://docs.github.com/en/rest/copilot/copilot-user-management and https://docs.github.com/en/enterprise-cloud@latest/rest/copilot/copilot-user-management?apiVersion=2026-03-10 (seats)
- https://docs.github.com/en/copilot/concepts/billing-and-usage/organizations-and-enterprises/billing (AI credits, included credits, pooling)
- https://docs.github.com/en/billing/reference/billing-reports (AI usage report, web download only)
- https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api and https://docs.github.com/en/rest/about-the-rest-api/api-versions
- https://github.blog/changelog/2026-01-29-closing-down-notice-of-legacy-copilot-metrics-apis/ , https://github.blog/changelog/2026-07-02-improved-accuracy-and-coverage-in-copilot-usage-metrics-reports , https://github.blog/changelog/2026-07-17-github-copilot-app-now-available-in-the-usage-metrics-api , https://github.blog/changelog/2026-08-11-per-model-token-breakdown-in-the-usage-report/ , https://github.blog/changelog/2026/04/27/github-copilot-is-moving-to-usage-based-billing/

LiteLLM (source at tag v1.104.2, commit fc3920bf):
- https://github.com/BerriAI/litellm/blob/v1.104.2/schema.prisma (`LiteLLM_DailyUserSpend`, `LiteLLM_SpendLogs`)
- https://github.com/BerriAI/litellm/blob/v1.104.2/litellm/proxy/management_endpoints/internal_user_endpoints.py and .../common_daily_activity.py (`/user/daily/activity`)
- https://github.com/BerriAI/litellm/blob/v1.104.2/litellm/types/proxy/management_endpoints/common_daily_activity.py (response model)
- https://github.com/BerriAI/litellm/blob/v1.104.2/litellm/proxy/spend_tracking/spend_management_endpoints.py (`/spend/logs`, `/spend/logs/v2`, licence-gated reports)
- https://github.com/BerriAI/litellm/blob/v1.104.2/litellm/llms/anthropic/chat/transformation.py (prompt tokens include cache)
- https://github.com/BerriAI/litellm/blob/v1.104.2/litellm/proxy/auth/route_checks.py (admin-viewer role)
- https://docs.litellm.ai/docs/proxy/cost_tracking , https://docs.litellm.ai/docs/proxy/customers , https://docs.litellm.ai/docs/proxy/access_control , https://docs.litellm.ai/docs/proxy/config_settings , https://docs.litellm.ai/docs/proxy/spend_logs_deletion , https://docs.litellm.ai/docs/proxy/custom_pricing , https://docs.litellm.ai/docs/proxy/db_info , https://docs.litellm.ai/docs/enterprise
- https://github.com/BerriAI/litellm/pull/38420 (10,000-row cap), https://github.com/BerriAI/litellm/pull/43408 (daily export, pre-release)

Other AI tools (section 2.5):
- https://cursor.com/docs/account/teams/admin-api (filtered usage events, daily usage data, members, spend) and the Cursor analytics API pages under https://cursor.com/docs
- https://docs.devin.ai/desktop/accounts/api-reference/get-consumption.md and https://docs.devin.ai/desktop/accounts/api-reference/analytics-api-introduction.md (Windsurf / Devin Desktop analytics)
- https://docs.cloud.google.com/gemini/docs/codeassist/sunset , https://antigravity.google/docs/enterprise , https://docs.cloud.google.com/gemini/enterprise/docs/ai-developer-tools-track-token-usage , https://docs.cloud.google.com/gemini/enterprise/docs/ai-developer-tools-metrics , https://docs.cloud.google.com/gemini/enterprise/docs/licenses , https://docs.cloud.google.com/gemini/docs/log-gemini , https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage
- https://unpkg.com/openai@7.3.0/resources/admin/organization/usage.d.ts (OpenAI usage fields; platform.openai.com refused the fetch), https://github.com/openai/openai-openapi , https://learn.chatgpt.com/docs/enterprise/workspace-analytics
- https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/q-admin-user-telemetry.html , https://kiro.dev/docs/enterprise/monitor-and-track/user-activity/ , https://kiro.dev/pricing
- https://docs.augmentcode.com/analytics/api-reference.md , https://ampcode.com/api/v2/openapi.json
- https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/iam-principal-cost-allocation.html (cost per IAM principal)
- Microsoft Graph `getMicrosoft365CopilotUsageUserDetail` on https://learn.microsoft.com

Standards and other tools:
- https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md and https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
- https://focus.finops.org/docs/specification/v1-4/ and https://api.github.com/repos/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/milestones (1.5 work: token direction and cache action, PR #2606; requester columns, PRs #2360 and #2553; model columns, PR #2442; consumption currency, PR #2617)
- https://docs.litellm.ai/docs/observability/vantage (LiteLLM's FOCUS export)
- https://www.vantage.sh/integrations/anthropic , https://docs.cloudzero.com/docs/ai-platforms
