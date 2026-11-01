---
type: spec
status: superseded
superseded_by: meterline-anthropic-SPEC-v1.1-2026-10-10.md (2026-10-10)
accepted: 2026-10-09 (owner, in chat)
updated: 2026-10-09
---

# meterline v1: common record format + Anthropic Enterprise Analytics connector

## 1. Purpose

meterline turns AI usage data from provider APIs into one common record format: one JSON line per record, plus optional Parquet. v1 ships two packages:

- `meterline`: the record types, their validation, and the JSONL/Parquet writers. No network, no provider knowledge.
- `meterline-anthropic`: reads the Anthropic Claude Enterprise Analytics API and emits `meterline` records.

A consumer (a dashboard, a data lake, a cost report) reads the records. Mapping them into a consumer's own tables, resolving identities, applying contract prices or allocating seat fees is the consumer's job and is out of scope.

## 2. Source: Claude Enterprise Analytics API

- Base URL `https://api.anthropic.com/v1/organizations/analytics/`. Headers: `x-api-key` (an Analytics key with scope `read:analytics`, created by the organization's Primary Owner in claude.ai), `anthropic-version: 2023-06-01`, and a `User-Agent` naming meterline. An Admin API key is rejected.
- Endpoints used in v1:

| Endpoint | Gives | Called as | Fresh after | Final |
|---|---|---|---|---|
| `user_usage_report` | Tokens per user | `bucket_width=1d`, `group_by[]=model`, `group_by[]=product`, windows of at most 31 days, `limit=1000` | ~4 h (up to 24 h) | ~7 days after month end |
| `user_cost_report` | Cost per user | Same as above | ~4 h | ~7 days after month end |
| `users` | Daily activity per user (sessions, conversations, commits, lines) | One call per `date` | Next day ~13:30 UTC | Daily snapshot |
| `summaries` | Seats, pending invites, active users | Date range | Next day | Daily snapshot |
| `usage_report`, `cost_report` | Organization totals incl. API-key traffic | `bucket_width=1d` | ~4 h | ~7 days after month end |

- Limits: 60 requests per minute per organization (shared by every consumer of the key); data from 2026-01-01, `starting_at` at most 365 days back; a pagination cursor is bound to its query and expires with HTTP 410; an activity date not yet available returns 400 naming the latest available day.
- Units: `amount` and `list_amount` are decimal strings in cents. Parse as Decimal and divide by 100; no float on the cost path.
- Never group by `rbac_group_id`: a user in two groups would be counted twice.
- Not covered by the API: Claude Code through Amazon Bedrock, seat fees, effort level.

Sources: https://platform.claude.com/docs/en/manage-claude/analytics-api and the endpoint reference pages under https://platform.claude.com/docs/en/api/beta/organization/analytics

## 3. Record format (package `meterline`)

All dates are UTC. `period_end` is inclusive: a daily record has `period_start == period_end`. Amounts are Decimal, serialised as strings in JSONL.

**`UsageRecord`**: one per provider × org × actor × day × product × model.

| Field | Type | Anthropic source |
|---|---|---|
| `provider` | str | `anthropic` |
| `org_id` | str | `organization_id` |
| `period_start`, `period_end` | date | bucket `starting_at` |
| `actor_id` | str | `actor.user_id` |
| `actor_email` | str or null | `actor.email_address`, trimmed and lower-cased |
| `actor_deleted` | bool | `actor.deleted` |
| `product` | str | `product`, as received |
| `model` | str | `model`, as received |
| `tokens_input` | int | `uncached_input_tokens` |
| `tokens_output` | int | `output_tokens` |
| `tokens_cache_write_5m` | int | `cache_creation.ephemeral_5m_input_tokens` |
| `tokens_cache_write_1h` | int | `cache_creation.ephemeral_1h_input_tokens` |
| `tokens_cache_read` | int | `cache_read_input_tokens` |
| `requests` | int or null | `requests` |
| `web_search_requests` | int | `server_tool_use.web_search_requests` |
| `cost` | Decimal or null | `user_cost_report.amount` / 100 |
| `cost_list` | Decimal or null | `user_cost_report.list_amount` / 100 |
| `currency` | str | `currency` |
| `is_final` | bool | true when the period's month ended more than 7 days before the pull |
| `pulled_at`, `data_refreshed_at` | datetime | the run, the response |
| `batch_id` | str | the run |

**`ActivityRecord`**: one per actor × day × product with non-zero activity: `sessions`, `conversations`, `messages`, `commits`, `pull_requests`, `lines_added`, `lines_removed`, `edits_accepted`, `edits_rejected`, `artifacts`, `skills_used`, `connectors_used`, `web_searches`, plus the identity, date and batch fields above.

**`SeatSnapshot`**: one per org × day: `seats`, `pending_invites`, `daily_active`, `weekly_active`, `monthly_active`.

**`OrgTotal`**: one per org × day: tokens and cost from `usage_report` / `cost_report`, used to check that per-user records never exceed the organization total.

Record key, used for revisions: `UsageRecord` = (provider, org_id, period_start, actor_id, product, model); `ActivityRecord` = (provider, org_id, period_start, actor_id, product).

## 4. Connector behaviour (package `meterline-anthropic`)

Two layers, so the network part and the parsing part test separately:

1. **Fetcher**: calls the API and writes every response page as received to `raw/<endpoint>/<window>/<pulled_at>.json.gz`, with the request URL and `data_refreshed_at`. A run writes a manifest: windows, pages, row counts, totals.
2. **Normaliser**: reads raw files only (never the network) and emits records. `meterline-anthropic rebuild` re-creates every record from the raw store.

**Commands**

- `meterline-anthropic backfill --since 2026-01-01`: walks 31-day windows; resumes after a failure.
- `meterline-anthropic daily`: tokens and cost for the current month, plus the previous month until the 8th; activity for every day since the last one loaded; yesterday's summary.
- `meterline-anthropic rebuild`: re-normalise from raw, no API calls.

**Rules**

1. Pagination: follow `next_page` while `has_more`, with every other parameter unchanged; on 410, restart the window from page 1.
2. Throttle to 50 requests per minute by default (configurable). On 429 and 5xx, back off exponentially with jitter (1 s to 60 s, 6 tries) and honour `retry-after`. Any other 4xx stops the run, except the 400 "date not yet available" on activity, which ends that window cleanly.
3. Set `ending_at` no later than the last `data_refreshed_at`, so one page sequence never mixes two refreshes.
4. Revisions: a re-pull of a window writes new records with a new `batch_id`; readers take the latest batch per record key. Earlier batches stay readable.
5. Join usage and cost on (actor, day, product, model). A usage row with no cost row gets `cost` = null, never 0.
6. Checks before records are published: no future dates, no negative tokens or cost, no duplicate record keys within a batch, record totals equal raw totals, per-user cost per day not above the organization total. A failed check publishes nothing and exits non-zero.
7. The API key is read from the environment (`ANTHROPIC_ANALYTICS_KEY`). It never reaches disk, logs or raw files. Logs carry counts and statuses, never emails.

**Request budget at ~1,000 users** (estimate): backfill Jan–Oct ≈ 2,200 requests (≈ 45 min at 50/min); a daily run ≈ 200–400 requests.

## 5. Out of scope for v1

Identity resolution, pricing from tokens, contract discounts, seat-fee allocation, currency conversion, alerting, and any consumer-specific table mapping. Other providers come as sibling packages on the same record format.

## 6. Layout

```
packages/meterline/            src/meterline/{records.py, writers.py}
packages/meterline-anthropic/  src/meterline_anthropic/{client.py, fetch.py, normalise.py, cli.py}
                               tests/ + tests/fixtures/ (synthetic only)
```

Python 3.10+. Runtime dependencies: `httpx`; `pyarrow` optional (Parquet writer). Tests: `pytest`, with a fake API server built on `httpx.MockTransport` replaying synthetic fixtures (about 50 invented users, 3 months).

## 7. Done-check

v1 is done when all of these pass, run by someone other than the builder:

1. A window of 2,500 rows across three pages loads completely, with no duplicate keys.
2. A 410 on page 2 restarts the window and gives the same records.
3. 300 requests never exceed the configured per-minute rate; a scripted 429 with `retry-after` is honoured.
4. A 400 naming the latest available day ends the activity window cleanly; the next run picks the day up.
5. A fixture value changed for last month and re-run on the 5th produces a new batch; the latest batch wins and the old one is still readable.
6. Running `daily` twice on the same day produces no new records.
7. `"41280.000000"` becomes Decimal `412.80`; a static check finds no float on the cost path.
8. Unknown products and models are kept as received and listed in the run manifest.
9. `rebuild` reproduces the published records byte for byte.
10. Grouping `UsageRecord` by actor and model reproduces the fixture totals to the cent.
11. A scan of the repo finds no key, no real email domain and no non-synthetic fixture.
