# Format fit inputs (spec v2 section 10, done-check F08)

Provenance: written from the field tables of spec v2 section 10, not from a provider's example response; five tools, seven files (Cursor and OpenAI have two sources each). Every value is synthetic.

One synthetic input per tool, in the field names the spec's table of section 10 gives. The date in each row is the date of that spec, because no provider page was read; the mark is [I] for every row. Invented people (`@example.com`), invented ids, invented numbers. They are not exports and were not checked against a live account; they exist to show that the v2 record format can hold each tool with the mapping the table states. The mapping is a test helper (`packages/meterline/tests/fit_mapping.py`), not a connector.

| File | Tool and source (section 10) | Read on | Mark |
|---|---|---|---|
| `cursor_filtered_usage_events.json` | Cursor, `POST /teams/filtered-usage-events` | 2026-10-10 (the date of spec v2) | [I] |
| `cursor_daily_usage_data.json` | Cursor, `POST /teams/daily-usage-data` | 2026-10-10 (the date of spec v2) | [I] |
| `openai_usage_completions.json` | OpenAI API, `/v1/organization/usage/completions` with `group_by=user_id,model`, `bucket_width=1d` | 2026-10-10 (the date of spec v2) | [I] |
| `openai_costs.json` | OpenAI API, costs, per project | 2026-10-10 (the date of spec v2) | [I] |
| `augment_analytics.json` | Augment, analytics API per user per day per model | 2026-10-10 (the date of spec v2) | [I] |
| `kiro_user_activity.csv` | Kiro, daily per-user CSV | 2026-10-10 (the date of spec v2) | [I] |
| `windsurf_consumption.json` | Windsurf, consumption v2, `granularity=daily&group_by=user,model_uid` | 2026-10-10 (the date of spec v2) | [I] |
