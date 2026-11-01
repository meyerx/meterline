---
type: reference
status: transcribed
updated: 2026-10-31
---

# GitHub Copilot response shapes (synthetic values)

Provenance: transcribed from GitHub's public REST documentation, pages and dates in the table below; marks and the folder overview are in [the parent README](../README.md). Every value is synthetic.

One file per endpoint read by `meterline-copilot` (spec v2 section 6.2). Keys and nesting are transcribed from the spec, which took them from GitHub's public documentation on 2026-10-10; every value is invented (ids `1`, `2`, logins `login1` and `octocat`, hosts at `example.com`). The shape check (done-check C01) compares what the connector's own client receives from the fake API with these files by keys and nesting only, never by value.

| File | Endpoint | Source | Read on | Mark |
|---|---|---|---|---|
| `report_envelope.json` | Envelope of A and B: `GET /orgs/{org}/copilot/metrics/reports/users-1-day?day=` and `organization-1-day` (enterprise: `/enterprises/{enterprise}/copilot/metrics/reports/users-1-day` and `enterprise-1-day`) | https://docs.github.com/en/rest/copilot/copilot-usage-metrics | 2026-10-10 | [V] |
| `users_1_day_row.ndjson` | One line of a file behind A's `download_links` (one object per user and day) | https://docs.github.com/en/copilot/reference/copilot-usage-metrics/example-schema (the per-user example, trimmed to one entry per array) and the field table at https://docs.github.com/en/copilot/reference/copilot-usage-metrics/copilot-usage-metrics | 2026-10-10 | [V] |
| `aggregate_1_day_row.ndjson` | One line of a file behind B's `download_links` | GitHub's documentation of the `organization-1-day` and `enterprise-1-day` reports: one flat record, with `day`, `daily_active_users`, `weekly_active_users`, `monthly_active_users` and the activity totals at the top level. The keys are transcribed from the enterprise-level example at https://docs.github.com/en/copilot/reference/copilot-usage-metrics/example-schema (read 2026-10-11), where the same fields sit inside `day_totals` for a 28-day report. | 2026-10-11 | [I] |
| `billing_ai_credit_usage_organization.json`, `billing_ai_credit_usage_enterprise.json` | C: `GET /organizations/{org}/settings/billing/ai_credit/usage` and `/enterprises/{enterprise}/settings/billing/ai_credit/usage` | https://docs.github.com/en/enterprise-cloud@latest/rest/billing/usage?apiVersion=2026-03-10 | 2026-10-10 | [V] |
| `seats.json` | D: `GET /orgs/{org}/copilot/billing/seats` and the enterprise path | https://docs.github.com/en/rest/copilot/copilot-user-management | 2026-10-10 | [V] |
| `seat_summary.json` | E: `GET /orgs/{org}/copilot/billing` (organization level only) | https://docs.github.com/en/rest/copilot/copilot-user-management | 2026-10-10 | [V] |

## Notes on the transcription

- **The aggregate row (B).** A `*-1-day` aggregate record is flat: the active-user counts and the activity totals sit at the top level of the row. Only the 28-day report, which the connector does not read, wraps its days in a `day_totals` array. The normaliser reads `daily_active_users`, `weekly_active_users` and `monthly_active_users` from the top of the row and, as a fallback, from the `day_totals` entry whose `day` is the requested day; it ignores every other field. If the live layout differs, save it here and C01 shows where.
- **Optional fields in the per-user row (A).** `organization_id`, `totals_by_cli`, `totals_by_copilot_app`, `totals_by_vscode_agent`, `totals_by_3rd_party_agent`, `used_vscode_agent`, the `distinct_*_use_count` fields and the `totals_by_<name>` top-five arrays may be absent (C01). Organization-level rows carry `organization_id`.
- **Billing.** `timePeriod` carries `year`, plus `month` and `day` when they were sent. The organization reply has `organization`, the enterprise reply `enterprise`; each has its own file. Items carry no user and no date.
- **Seats.** `plan_type` and `assigning_team` may be absent (C01). The assignee object is trimmed to `login`, `id`, `node_id`, `type`, `site_admin`.
- **Download hosts.** `download_links` are signed URLs to NDJSON files; the shape file uses `downloads.example.com`.
