# JSON Schemas

Machine-readable contracts of meterline's file formats (JSON Schema draft 2020-12). Every file here is generated
from the code; never edit one by hand. A test fails when a committed file differs from what the code generates.

| File | Describes | Generated from | Regenerate with |
|---|---|---|---|
| `usage.schema.json`, `activity.schema.json`, `seats.schema.json`, `seat_assignments.schema.json`, `org_totals.schema.json` | one line of `records/<kind>/<batch_id>.jsonl`, the five silver record types ([`docs/records.md`](../records.md)) | `packages/meterline/src/meterline/records.py` | `python -m meterline.schema docs/schemas` |
| `gold-org-units.schema.json`, `gold-org-people.schema.json`, `gold-org-memberships.schema.json`, `gold-org-identities.schema.json`, `gold-org-directory.schema.json` | one row of each organisation CSV file `meterline-gold` reads (an empty cell is null; the header must be exactly `x-columns`) | `packages/meterline-gold/src/meterline_gold/load.py` | `python -m meterline_gold.contract docs/schemas` |
| `gold-manifest.schema.json` | the `manifest.json` of a gold folder | `packages/meterline-gold/src/meterline_gold/write.py` | `python -m meterline_gold.contract docs/schemas` |
| `gold-dataset-<name>.schema.json` | one row of each dataset file a gold build writes: types, units, descriptions, keys, joins and the cost rule (an audience form with other columns, such as `gold-dataset-exec-costs_month.schema.json`, has its own file); the page [`docs/reference/gold-datasets.md`](../reference/gold-datasets.md) is generated from the same source | `packages/meterline-gold/src/meterline_gold/write.py` and `dataset_docs.py` | `python -m meterline_gold.contract docs/schemas docs/reference/gold-datasets.md` |
| `gold-audiences.json`, `gold-audiences.schema.json` | which file of a gold folder may be served to which reader, and the schema of that file; `meterline_gold.servable(path, audience)` reads it | `packages/meterline-gold/src/meterline_gold/audiences.json` | `python -m meterline_gold.contract docs/schemas` |

The policy and price file schemas ship inside the `meterline-gold` package and are written by
`meterline-gold schemas --out DIR`.

A record schema allows fields it does not list, because a reader ignores a field it does not know (see
Compatibility in [`docs/records.md`](../records.md#compatibility)). Rules that span several fields, such as token
sums or the grain against the dates, are checked by `meterline.validate`, not by the schema.
