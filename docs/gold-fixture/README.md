# meterline-gold hand-built fixture

The first oracle for the gold core (decision GD13 of `docs/dev/history/meterline-gold-core-SPEC-v1.0-2026-10-12.md`). One month (2026-05) of an invented 21-person organisation, with all three providers, one unresolved actor, two non-person actors (one by an identity row, one by actor kind), one joiner, one unknown model and one unit below `k`. The token prices are invented: each is a round base value times 1.1, so that no value is any vendor's list price. Everything here is invented; every email ends in `@example.com`.

| Folder | Holds |
|---|---|
| `input/silver/records/<kind>/<batch_id>.jsonl` | Silver v2 records. They were built with meterline's record classes and pass `meterline.batch_failures` and `meterline.actor_cost_failures`. |
| `input/org/` | `units.csv`, `people.csv`, `memberships.csv` (empty), `identities.csv`, `directory.csv`, in the simulator's format. |
| `input/policy.yaml`, `input/prices.yaml`, `input/invoice.csv` | The fixture's policy, price list and invoice. |
| `expected/` | The expected gold outputs, computed by hand (spec Appendix A). Files sit at the paths the build writes. `expected/person_comparison.csv` and `expected/layout.txt` are check helpers, not build outputs. |
| `expected-v1.1-sha256.txt`, `tiny-sim-v1.1-sha256.txt` | The sha256 of every CSV the v1.1 package (gold schema version 1.0) wrote for the fixture and for the tiny sim (seed 7, 6 months, example policy and prices), recorded before the `hidden` marker was built. Check G26 d compares against them. The `person/<key>/person_month.csv` lines are the one exception: they were regenerated when person folders stopped carrying `manager_key` (owner decision of 2026-10-16; `docs/dev/change-list-meterline-gold.md`, finding 30); every other line is as first recorded. |
| `litellm/` | A synthetic file in the shape of LiteLLM's public price file, the key list, and the importer's expected output. `sample.json` holds one model with no input price (`model-q-three`); `keys.txt` leaves it out, because the importer refuses a listed model without an input or output price (`docs/dev/change-list-meterline-gold.md`, finding 36). |

**Values computed by code:**
- The `person_key` and `actor_key` values. They were made with Python's standard `hmac` (SHA-256), using the fixed test key `meterline-gold-fixture-key-not-a-secret` (spec 3.6, 5.1).
- `litellm/expected-prices.yaml`. Its values were converted by hand; PyYAML only wrote the file, in the importer's specified dump format.

Every other figure in `expected/` was computed by hand. The arithmetic is in the spec's Appendix A.

Expected outputs not provided here, and checked instead by G12, G14 and the assertions: `central/people.csv`, `central/units.csv`, `central/identity_map.csv`, `delivery/directory.csv`, the person folders' files, and the manifest.
