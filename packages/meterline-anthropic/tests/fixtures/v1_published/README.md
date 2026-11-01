# v1.1 published fixture batches

Synthetic. Written once by the v1.1 code (`meterline-anthropic` on `main` at merge of PR #4, before the v2 build) from `../data/dataset.json.gz`: a `backfill --since 2026-10-01` at 2026-10-09 06:00 UTC, then a `daily` at 18:00 UTC after one usage row was changed (a revision).

| Folder | What it is |
|---|---|
| `raw/`, `runs/` | The raw store and run manifests of those two runs: input to a v2 `rebuild` (done-check A03). |
| `records/` | The v1 records those runs published: input to `meterline.convert_v1` (done-checks F02, A03). |

Never regenerate these with v2 code: they are the frozen v1 side of the comparison.
