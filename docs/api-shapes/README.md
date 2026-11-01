# Response shapes

Provenance: every file here was transcribed by hand from a provider's public documentation or public source
code, on the date given below. None was captured from a live account.

Synthetic data: every value in every file is invented (ids such as `user-a`, addresses at `example.com`,
round or made-up numbers). Only the keys, the nesting and the types follow the provider. Never commit a real
response here: when a live response differs, copy its structure and replace every value, as the connector
READMEs describe under Response shapes.

Each connector's fake API must answer in exactly these shapes, and a shape check per connector compares them
by keys and nesting, never by value. A shape check keeps the fake equal to these files; it cannot show that
the files equal what the live API sends. That is why a connector stays at the evidence level "built against
documented shapes" ([glossary](../glossary.md#evidence-levels)) until it has run against a live account.

## Marks

Each row below carries one mark:

- **[V]** the shape was read from the named page or source file on the date given, and transcribed as shown
  there;
- **[I]** the shape is inferred: the source did not show this exact layout, and the file holds the connector's
  reading of it (the note says what was inferred).

## Shapes go stale

A shape file records what one source said on one day. It does not say what the API sends today: a provider can
add a field or rename one the day after. Each row therefore names its source and the date it was read, and a
reader who needs the shape today reads the source again. When a re-read shows a difference, update the file and
the date in the same change. The rules for a new provider's shape folder are in the
[connector contract](../reference/connector-contract.md#shape-files).

## What is here

The six files at the top level are the Anthropic shapes; each subfolder has its own README with a source per
file.

| File or folder | Provider and endpoint | Source and date read | Mark |
|---|---|---|---|
| `user_usage_report.json` | Anthropic, per-user tokens | Anthropic API reference, `organization/analytics/user_usage_report/list`, read on 2026-10-10 | [V] |
| `user_cost_report.json` | Anthropic, per-user cost | the same reference, `user_cost_report/list`, read on 2026-10-10 | [V] |
| `usage_report.json` | Anthropic, organisation tokens | the same reference, `usage_report/list`, read on 2026-10-10 | [V] |
| `cost_report.json` | Anthropic, organisation cost | the same reference, `cost_report/list`, read on 2026-10-10 | [V] |
| `users.json` | Anthropic, per-user activity | the same reference, `users/list`, read on 2026-10-10 | [V] |
| `summaries.json` | Anthropic, seats and active users | the same reference, `summaries/list`, read on 2026-10-10 | [V] |
| `litellm/` | LiteLLM proxy, daily activity and user list | the LiteLLM source at tag `v1.104.2`, file and line per shape, read on 2026-10-11; reply shapes checked again against v1.106.6 on 2026-10-29 (its README says how far) | [V] |
| `copilot/` | GitHub Copilot usage reports, billing and seats | GitHub's REST documentation, page per shape, read on 2026-10-10 and 2026-10-11 | [V], except the one-day aggregate row: [I] |
| `anthropic-admin/` | Anthropic Admin API, usage report (messages) and cost report | the Anthropic API reference pages named in its README, read on 2026-10-17 | [V] |
| `fit/` | Five other tools (Cursor, OpenAI, Augment, Kiro, Windsurf), seven files | the field tables of spec v2 section 10, written on 2026-10-10 (the date of that spec), not a provider example | [I] |

Status on 2026-10-29: Anthropic declared the usage and cost report endpoints legacy that day. In this folder that
is `user_usage_report.json`, `user_cost_report.json`, `usage_report.json` and `cost_report.json`, and the two files
in `anthropic-admin/`. They are still supported and no sunset date is known. `users.json` and `summaries.json` are
not legacy. The files describe the legacy endpoints; the new endpoints answer in another shape and have no file
here. The move to them is tracked separately.

The Anthropic reference pages are under
<https://platform.claude.com/docs/en/api/http/beta/organization/analytics/>; spec v1.1 section 2.1 holds the
same shapes in its text. The `fit/` files are not used by any connector: they only show that the record format
can hold those tools (done-check F08).
