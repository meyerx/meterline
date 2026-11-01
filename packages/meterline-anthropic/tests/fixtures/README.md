# Fixtures

Everything here is **synthetic**. Users, emails (only `@example.com`), ids, usage and cost are invented. Never put an export from a real account in this folder.

| File | What it is |
|---|---|
| `generate.py` | The generator. Seeded (`20261009`), no network, deterministic: running it again gives byte-identical output. |
| `data/dataset.json.gz` | About 50 invented users, 2026-07-01 to 2026-10-08: usage, cost, organization totals, activity and seat summaries. Rows follow the documented shapes in `docs/api-shapes/` (spec v1.1 section 2.1); the fake API adds the day fields and the top-level `organization_id`. |
| `data/expected_totals.json` | Cost per actor and model in major units, computed by the generator with `Decimal`. Done-check 10 compares the connector's output with it to the cent. |
| `data/SYNTHETIC.json` | The marker that done-check 11 requires: `{"synthetic": true, ...}`. |

## Deliberate edge cases

- One pinned row, user `user-001`, 2026-09-15, `claude_code`, `model-a-large`, with amount `"41280.000000"` (cents), which must become `Decimal("412.80")`.
- Four deleted users (`user-047` to `user-050`) with no email, active only through 2026-08-31.
- One user whose email comes back padded and mixed case, to exercise trim and lower-case.
- Two usage rows with no cost row, to exercise the "cost is null, never 0" rule.
- Organization rows (no `group_by`: one row per day) include API-key traffic that no user row accounts for.
- Activity rows carry every product block; some users have Cowork and Excel activity, design and science sessions are null, a few rows carry an all-zero `chat_cowork_unified_metrics`, and one `pending_invite_count` in ten is null.

## Regenerate

```sh
python packages/meterline-anthropic/tests/fixtures/generate.py     # writes into ./data next to the script
```

Commit the regenerated files together with any change to the generator.
