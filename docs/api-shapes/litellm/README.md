---
type: reference
status: transcribed
updated: 2026-10-31
---

# LiteLLM response shapes (synthetic values)

Provenance: transcribed from the public LiteLLM source at tag `v1.104.2`, file and line in the table below, read on 2026-10-11; marks and the folder overview are in [the parent README](../README.md). Every value is synthetic.

One JSON file per endpoint read by `meterline-litellm`. Keys and nesting are transcribed from the public LiteLLM source at tag `v1.104.2`; every value is invented (ids such as `user-a`, emails at `example.com`). The shape check (done-check L01) compares the fake proxy's answers with these files by keys and nesting only. A map whose keys are data (`breakdown.*`, `api_key_breakdown`) holds one example entry here and is compared by the shape of its values.

| File | Endpoint | Source | Read on | Mark |
|---|---|---|---|---|
| `user_daily_activity.json` | `GET /user/daily/activity` (A) | Route: `litellm/proxy/management_endpoints/internal_user_endpoints.py` L2872-2991 (`get_user_daily_activity`). Response model: `litellm/types/proxy/management_endpoints/common_daily_activity.py` L19-108 (`SpendMetrics`, `KeyMetadata`, `KeyMetricWithMetadata`, `MetricWithMetadata`, `BreakdownMetrics`, `DailySpendData`, `DailySpendMetadata`, `SpendAnalyticsPaginatedResponse`). Row grouping and the "Unassigned" entity key: `litellm/proxy/management_endpoints/common_daily_activity.py` L286-446. | 2026-10-11 (reply shapes checked again on 2026-10-29) | [V] |
| `customer_daily_activity.json` | `GET /customer/daily/activity` (B) | Route: `litellm/proxy/management_endpoints/customer_endpoints.py` L865-934 (`get_customer_daily_activity`; admin and admin-viewer only, 401 otherwise; entity metadata `{"alias": ...}`). Same response model as A. | 2026-10-11 (reply shapes checked again on 2026-10-29) | [V] |
| `user_list.json` | `GET /user/list` (C) | Route: `internal_user_endpoints.py` L2208-2378 (`get_users`; `page_size` 1-100; return at L2372-2378). Envelope: `UserListResponse` in `litellm/types/proxy/management_endpoints/internal_user_endpoints.py` L31-40 (`users`, `total`, `page`, `page_size`, `total_pages`). A user is `LiteLLM_UserTableWithKeyCount` (`litellm/proxy/_types.py` L3671-3672, `key_count`) over `LiteLLM_UserTable` (`litellm/models/user.py` L19-50; `password` is excluded from the dump). | 2026-10-11 (reply shapes checked again on 2026-10-29) | [V] |

Base URL of all sources: `https://github.com/BerriAI/litellm/blob/v1.104.2/`. Read on 2026-10-11.

## Checked again on 2026-10-29 against v1.106.6

On 2026-10-29 the route functions of LiteLLM v1.106.6 (the latest stable tag then) were run with a fake database, and their reply shapes were compared with these files. This covers the shape of the replies. It does not cover the SQL, the sign-in or the timing of the daily tables, and no proxy was started. The transcription above is still the one of v1.104.2, read on 2026-10-11; the files were not changed.

What changed between v1.104.2 and v1.106.6 on the three routes the connector uses, and why none of it affects the connector:

- **Three new fields in `metadata`** (`api_key_limit`, `total_api_keys`, `entity_total_api_keys`, null on these routes). The connector reads only `has_more` and `page` from `metadata`.
- **Dates are checked.** v1.106.6 answers 400 to a date that is not a zero-padded `YYYY-MM-DD`. The connector sends `datetime.date.isoformat()`, which is zero-padded.
- **The customer route checks paging.** It answers 400 to a `page` or `page_size` below 1. The connector sends `page` from 1 and a `page_size` of 1000 for the activity routes and 100 for the user list. That is the maximum of the user activity route and of the list; the customer route has no maximum.
- **`exclude_end_user_ids` keeps rows with no end user.** The connector never sends `exclude_end_user_ids`.
- **Customer metadata is fetched another way** (all aliases when `end_user_ids` is omitted). The output shape is the same.
- **New sibling routes** (aggregated, export and others) exist. The connector does not call them. The path and method of the three routes it calls are unchanged.
- **Code was reorganised** (a repository class for the reads, another function for non-admin scoping) with the same results and the same 403 texts.
- **Unchanged:** every field of the spend and breakdown models, the paging arithmetic, the default page sizes, the two daily tables' schema, and the `/user/list` handler and models.

One change is in values, not shapes: LiteLLM's release notes for v1.106.2 say spend on failed requests is now priced. How far that changes the daily rows was not traced; it is unverified. See the connector README.

## Notes on the transcription

- A and B share one response model, so their files are identical; they are kept apart so each endpoint has its own pinned file.
- In A, `breakdown.entities` is keyed by user id and its metadata holds `user_email` and `user_alias` (`_resolve_user_email_metadata`, `internal_user_endpoints.py` L2858-2869). The spec reads emails from C; the entity metadata would also do. The shape file leaves `metadata` as `{}` because the model types it `dict[str, Any]`.
- `MetricWithMetadata.metadata` and the optional `SpendMetrics` fields: the shape file lists every field the model declares; the check treats every `SpendMetrics` field except `spend`, `prompt_tokens`, `completion_tokens` and `api_requests` as optional (spec section 8, L01).
- `include_current_utc_day` and `timezone` are never sent.
