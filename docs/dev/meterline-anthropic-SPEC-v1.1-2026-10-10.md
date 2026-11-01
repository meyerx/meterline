---
type: spec
status: accepted
accepted: 2026-10-10 (owner, in chat)
updated: 2026-10-10
supersedes: meterline-anthropic-SPEC-v1.0-2026-10-09.md (on acceptance)
---

# meterline v1.1: common record format + Anthropic Enterprise Analytics connector

v1.1 changes v1.0 in four ways. (1) Section 2.1 now states the exact JSON shape of every endpoint, transcribed from Anthropic's reference pages on 2026-10-10; v1.0 named fields but not shapes, and the v1.0 build guessed some wrong. (2) Section 3 states every record field. (3) Section 4 settles the open points of the v1.0 change list. (4) Section 7 adds checks 12 to 17, which pin the real shapes. Everything not mentioned here is unchanged from v1.0.

## 1. Purpose

meterline turns AI usage data from provider APIs into one common record format: one JSON line per record, plus optional Parquet. v1 ships two packages:

- `meterline`: the record types, their validation, and the JSONL/Parquet writers. No network, no provider knowledge.
- `meterline-anthropic`: reads the Anthropic Claude Enterprise Analytics API and emits `meterline` records.

Mapping records into a consumer's own tables, resolving identities, pricing, discounts, seat-fee allocation and currency conversion are the consumer's job and out of scope.

## 2. Source: Claude Enterprise Analytics API

- Base URL `https://api.anthropic.com/v1/organizations/analytics/`. Headers: `x-api-key` (an Analytics key with scope `read:analytics`), `anthropic-version: 2023-06-01`, and a `User-Agent` naming meterline.
- Limits: 60 requests per minute per organization; data from 2026-01-01; `starting_at` at most 365 days back; report windows at most 31 days; an expired cursor returns HTTP 410; an activity date not yet available returns HTTP 400 naming the latest available day.
- Amounts are decimal strings in cents: parse as Decimal and divide by 100. No float on the cost path.
- Never group or filter by `rbac_group_id`: a user in two groups would be counted twice.
- Not covered by the API: Claude Code through Amazon Bedrock, seat fees, effort level.

### 2.1 Response shapes (normative)

Array query parameters are sent as repeated `name[]=value` pairs. A field not listed below may appear and is ignored.

**A. `user_usage_report`** (per-user tokens). Called with `starting_at`, `ending_at` (RFC 3339), `bucket_width=1d`, `group_by[]=model`, `group_by[]=product`, `limit=1000` (rows per page; each actor x day x model x product is one row), `page`.

```
{ "data": [ <row>, ... ], "has_more": bool, "next_page": str|null,
  "data_refreshed_at": str|null, "organization_id": str }
<row> = { "actor": { "type": "user_actor", "user_id": str, "email_address": str|null,
                     "name": str|null, "deleted": bool },
          "starting_at": str, "ending_at": str,          (set because bucket_width is set)
          "model": str|null, "product": str|null,
          "uncached_input_tokens": int, "output_tokens": int, "cache_read_input_tokens": int,
          "cache_creation": { "ephemeral_5m_input_tokens": int, "ephemeral_1h_input_tokens": int },
          "total_tokens": int, "requests": int|null,
          "server_tool_use": { "web_search_requests": int } }
```

Rows are FLAT: there is no bucket wrapper and no `results` array. `organization_id` is top-level only.

**B. `user_cost_report`** (per-user cost). Same parameters and same envelope as A.

```
<row> = { "actor": { ...as in A... }, "starting_at": str, "ending_at": str,
          "model": str|null, "product": str|null,
          "amount": str (cents, after discount), "list_amount": str (cents), "currency": "USD",
          "requests": int|null }
```

**C. `usage_report`** (organization tokens). Called with `starting_at`, `ending_at`, `bucket_width=1d`, no `group_by`, `limit=31` (buckets per page).

```
{ "data": [ { "starting_at": str, "ending_at": str, "results": [ <org row>, ... ] }, ... ],
  "has_more": bool, "next_page": str|null, "data_refreshed_at": str|null, "organization_id": str }
<org row> = { "uncached_input_tokens": int, "output_tokens": int, "cache_read_input_tokens": int,
              "cache_creation": { "ephemeral_5m_input_tokens": int, "ephemeral_1h_input_tokens": int },
              "requests": int|null, "server_tool_use": { "web_search_requests": int } }
```

Org reports ARE bucketed: dates sit on the bucket, rows sit in `results`. A bucket with no data has an empty `results`.

**D. `cost_report`** (organization cost). Same parameters and envelope as C; `<org row>` = `{ "amount": str, "list_amount": str, "currency": "USD", "requests": int|null }`.

**E. `users`** (per-user activity). Called with `date=YYYY-MM-DD`, `limit=1000`, `page`.

```
{ "data": [ <user row>, ... ], "next_page": str|null }
```

No `has_more`, no `data_refreshed_at`, no `organization_id`: continue while `next_page` is not null. One row per user per day; each row carries every product's block as sibling fields:

```
<user row> = { "user": { "type": "user", "id": str, "email_address": str },
  "chat_metrics": { "distinct_conversation_count", "message_count", "distinct_artifacts_created_count",
                    "distinct_skills_used_count", "distinct_connectors_used_count", ...: int },
  "claude_code_metrics": { "core_metrics": { "distinct_session_count", "commit_count", "pull_request_count",
                                             "artifacts_created_count": int,
                                             "lines_of_code": { "added_count", "removed_count": int } },
                           "tool_actions": { "edit_tool"|"multi_edit_tool"|"notebook_edit_tool"|"write_tool":
                                             { "accepted_count", "rejected_count": int } } },
  "cowork_metrics": { "distinct_session_count", "message_count", "artifacts_created_count",
                      "distinct_skills_used_count", "distinct_connectors_used_count", ...: int },
  "design_metrics": { "distinct_session_count": int|null, "message_count": int, ... },
  "office_metrics": { "excel"|"outlook"|"powerpoint"|"word": { "distinct_session_count", "message_count",
                      "distinct_skills_used_count", "distinct_connectors_used_count", ...: int } },
  "science_metrics": { "distinct_session_count": int|null, "message_count": int, ... },
  "chat_cowork_unified_metrics": { "chat": {...as chat_metrics...}, "sessions": {...as cowork_metrics...} }
                                 (optional; absent on deployments without it),
  "web_search_count": int, "last_activity_date": str (optional) }
```

**F. `summaries`** (seats and active users). Called once per range with `starting_date=YYYY-MM-DD` (inclusive) and `ending_date=YYYY-MM-DD` (exclusive, at most 366 days); the whole series returns in one response.

```
{ "data": [ <day>, ... ], "next_page": null }
<day> = { "starting_at": str, "ending_at": str, "assigned_seat_count": int|null,
          "pending_invite_count": int|null, "daily_active_user_count": int,
          "weekly_active_user_count": int, "monthly_active_user_count": int, ... }
```

No `organization_id` and no `data_refreshed_at`.

Sources: the endpoint reference pages under https://platform.claude.com/docs/en/api/http/beta/organization/analytics/ (`user_usage_report/list`, `user_cost_report/list`, `usage_report/list`, `cost_report/list`, `users/list`, `summaries/list`), read 2026-10-10.

## 3. Record format (package `meterline`)

All dates UTC; `period_end` inclusive, so a daily record has `period_start == period_end`. Amounts are Decimal, serialised as strings with at least two decimals and no trailing zeros beyond that (`"412.80"`).

Common fields on every record: `provider` (`anthropic`), `org_id`, `period_start`, `period_end`, `pulled_at`, `data_refreshed_at` (datetime or null), `batch_id`.

| Record | Key | Own fields |
|---|---|---|
| `UsageRecord` | provider, org_id, period_start, actor_id, product, model | `actor_id`, `actor_email` (trimmed, lower-cased, or null), `actor_deleted`, `product`, `model` (as received), `tokens_input`, `tokens_output`, `tokens_cache_write_5m`, `tokens_cache_write_1h`, `tokens_cache_read`, `requests` (int or null), `web_search_requests`, `cost`, `cost_list` (Decimal or null), `currency`, `is_final`, `cost_only` (bool) |
| `ActivityRecord` | provider, org_id, period_start, actor_id, product | `actor_id`, `actor_email`, `product`, `sessions`, `conversations`, `messages`, `commits`, `pull_requests`, `lines_added`, `lines_removed`, `edits_accepted`, `edits_rejected`, `artifacts`, `skills_used`, `connectors_used`, `web_searches` (all int, 0 when the block has no such field) |
| `SeatSnapshot` | provider, org_id, period_start | `seats`, `pending_invites` (int or null), `daily_active`, `weekly_active`, `monthly_active` |
| `OrgTotal` | provider, org_id, period_start | the token, `requests`, `web_search_requests`, `cost`, `cost_list`, `currency` fields of `UsageRecord`, and `is_final` |

## 4. Connector behaviour (package `meterline-anthropic`)

Fetcher and normaliser stay separate, as in v1.0: the fetcher stores every response page as received under `raw/`; the normaliser reads only raw files. Commands `backfill --since`, `daily` and `rebuild` are unchanged.

**Mapping rules (new in v1.1)**

1. `org_id` is the top-level `organization_id` of the response. Endpoints E and F have none: their records take the `organization_id` of the report responses of the same run; if the run has none yet, the fetcher first makes one `usage_report` call for a single day to learn it. A record never carries `"None"` or an empty `org_id`.
2. A/B rows: the day is the row's `starting_at`. C/D rows: the day is the bucket's `starting_at`, one `OrgTotal` per bucket.
3. Usage and cost rows join on (actor `user_id`, day, product, model). A usage row with no cost row gets `cost` = null and `cost_list` = null. A cost row with no usage row becomes a `UsageRecord` with zero tokens, `cost_only` = true, and is listed in the manifest; it never fails the run.
4. Each E row yields one `ActivityRecord` per product block with at least one non-zero count. Product names: `chat` (chat_metrics), `claude_code` (claude_code_metrics), `cowork` (cowork_metrics), `claude_design` (design_metrics), `office_agent.excel` / `.outlook` / `.powerpoint` / `.word` (office_metrics), `science` (science_metrics), `chat_cowork_unified.chat` and `chat_cowork_unified.sessions`. `web_search_count` goes on its own record with product `all`, only when non-zero. Field mapping: `sessions` = `distinct_session_count`; `conversations` = `distinct_conversation_count`; `messages` = `message_count`; `commits` = `commit_count`; `pull_requests` = `pull_request_count`; `lines_added` / `lines_removed` = `lines_of_code.added_count` / `removed_count`; `edits_accepted` / `edits_rejected` = the sum over the four `tool_actions`; `artifacts` = `distinct_artifacts_created_count` or `artifacts_created_count`; `skills_used` = `distinct_skills_used_count`; `connectors_used` = `distinct_connectors_used_count`. A null count is 0.
5. F rows: one `SeatSnapshot` per day; `seats` = `assigned_seat_count`, `pending_invites` = `pending_invite_count`, `daily_active` / `weekly_active` / `monthly_active` = the three `*_active_user_count` fields.
6. Products and models are recorded exactly as received. There is no list of known values: each run's manifest lists every product and model value that no earlier published batch contained.

**Fetch rules (v1.0 rules kept; these change)**

7. Pagination: A-D continue while `has_more` is true; E continues while `next_page` is not null; F is one request. On 410, restart the window from page 1.
8. Stable reads for A-D: the first request of a window is sent without `ending_at`, `limit=1`; its `data_refreshed_at` sets the cap. The window is then fetched from page 1 with `ending_at` = the earlier of the window end and UTC midnight of the cap's day, so only whole days are read. If `data_refreshed_at` is null, the window is skipped and listed in the manifest as "no data yet".
9. `data_refreshed_at` is optional everywhere: a missing or null value never raises. Records from E and F carry null.
10. "Until the 8th" reads "through the 8th": `daily` re-pulls the previous month on days 1 to 8 inclusive; a period is `is_final` when its month ended more than 7 days before the pull.
11. With no saved state, `daily` starts activity on the first day of the current month.
12. The store-generated `receipts/` folder is gitignored.

Rules 1-7 of v1.0 section 4 (throttle 50/min, back-off, checks before publish, revisions as new batches, key from `ANTHROPIC_ANALYTICS_KEY` only, no emails in logs) are unchanged, except that the "record totals equal raw totals" check now counts cost-only records.

## 5. Out of scope

Unchanged from v1.0.

## 6. Layout

Unchanged from v1.0, plus `docs/api-shapes/`: one JSON file per endpoint (A-F), each holding the documented example response, reduced to synthetic values. The fake API in the tests must produce responses of exactly these shapes.

## 7. Done-check

Checks 1 to 11 of v1.0 still apply, run against fixtures in the real shapes. New:

12. For each endpoint A-F, the fake API's response has exactly the top-level keys, nesting and field names of its `docs/api-shapes/` file (a structural comparison, values ignored). Run against the v1.0 fake, this check must fail.
13. A user row with non-zero Claude Code, chat, Cowork and Excel blocks yields exactly four `ActivityRecord`s with the right products and numbers.
14. 2,500 users across three `users` pages, where pages carry `next_page` and no `has_more`, are all loaded.
15. A run where `users` and `summaries` carry no `data_refreshed_at`, and one report window returns `data_refreshed_at: null`, completes, publishes, and lists the skipped window in the manifest.
16. A cost row with no matching usage row produces a cost-only `UsageRecord`, the run publishes, and the manifest lists it.
17. No published record has an empty or `"None"` `org_id`, and every record's `org_id` equals the fixture's `organization_id`.
