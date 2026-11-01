---
type: spec
status: superseded
superseded_by: meterline-gold-core-SPEC-v1.1-2026-10-14.md (2026-10-14)
accepted: 2026-10-12 (owner, in chat; cards 1-8 of section 15 decided as recommended)
updated: 2026-10-12
builds_on: docs/records.md, docs/meterline-sim-SPEC-v1.0-2026-10-11.md (accepted; frozen), docs/analysis/meterline-gold-core-analysis.md
---

# meterline gold core v1: per-person, per-group and per-audience datasets from meterline silver

meterline-gold reads meterline silver (the v2 records of `docs/records.md`), a set of organisation files, a reference price list and a policy file. It writes a **gold folder**: datasets about people, groups, seats, costs and data quality, each already filtered for one audience (central, manager, person, exec), plus shadow budget scenarios and alert events. Nothing it writes enforces anything.

The facts behind this spec, with sources, are in `docs/analysis/meterline-gold-core-analysis.md` (F1-F4 there). Grounding labels: **[V]** verified on 2026-10-12 at the cited source, **[I]** inferred, **[A]** a choice this spec makes. The hand-built test fixture is `docs/gold-fixture/` and its arithmetic is Appendix A.

## 1. Purpose, scope and decisions

The owner took these decisions on 2026-10-11. They bind this spec.

| # | Decision | Where |
|---|---|---|
| GD1 | Package `packages/meterline-gold`. Generic: a governance programme is a policy file; the repo ships an invented example policy whose amounts are visibly synthetic, so that no round figure can appear (rule in 4.4); currency USD. | 2, 4.4 |
| GD2 | Inputs: a silver folder (current state via `meterline.latest_per_key`), organisation CSVs in the simulator's format, a reference price list (with `as_of` and `source`; an optional offline importer from LiteLLM's public model price file, no network at run time), an optional billed-invoice CSV, a policy file. Output: a gold folder, one subfolder per audience. | 3, 4, 9 |
| GD3 | Engine: versioned DuckDB SQL files plus a thin Python runner; no dbt snapshots. Policy and parameter files are YAML validated by Pydantic v2, JSON Schema published. Invariants are pytest plus DuckDB assertion queries. Same inputs give the same output bytes (explicit `ORDER BY` everywhere, CSV canonical outputs; Parquet optional and outside the byte check). | 2, 9 |
| GD4 | A1 people, units and identities from the CSVs: fixed person columns plus `attrs`; several unit dimensions; dated validity; identity methods including `non_person` and unresolved. `person_key` = keyed hash (HMAC, key from an environment variable, never stored; a fixed test key in tests only). Names and emails only in a separate directory dataset meant for delivery. Comparison groups declared in the policy (`compare_by`). | 5, 6.1 |
| GD5 | A2 `person_day` (person × day × provider × product × model): token buckets, credits, requests, `cost_source` with its basis, `cost_ref` from the reference price list in the reference currency (never summed with `cost_source`); UTC days; usage without a person kept. `person_day_activity` alongside. | 6.2, 6.3 |
| GD6 | A3 `person_month`: active days (total and per tool), `cost_ref` (total and per provider), tokens, premium share via a model catalogue (unknown models flagged, never guessed), cache rate, intensity band (thresholds in the policy), final flag, linear projection for a running month. | 6.4 |
| GD7 | A5 seats and credit pools per month: assigned, active, idle (idle after N days), seat cost; pooled credits used, overage, budget state. Seat cost counts in total cost, never in a person's position. | 6.5-6.7 |
| GD8 | A6 group aggregates on a fixed grid declared in the policy: counts, costs, distribution shape (P50/P75/P90/P99, top-decile cost share), no names. Privacy: minimum group size `k`; collapsing and banding first; differencing rule (including month-over-month and parent-minus-child); simple deterministic secondary suppression; no output sorted by spend, volume or band with names; individuals see only their own figures and a banded comparison to their `compare_by` group. A Hypothesis test tries to recover suppressed cells and must fail. | 7. Readings this spec makes, each with a card in section 15: the grid is one tree (7.1, card 2); GD8's sibling rule plus a stronger block rule that also counts what a unit's heads know (7.4); collapsing on a unit tree is structural (7.1); **owner decision 2026-10-12 (card 1): differencing compares cells of the same month only; the month-over-month comparison is dropped, and a deliver-once rule goes to the delivery recipe** (7.4); comparison cells publish no additive figure and stay outside differencing (7.5, card 3); the subset rule for quantiles, shares and active counts, with the top-decile share on the org cell only (7.2, card 4). |
| GD9 | Audiences central, manager, person, exec, each a separate dataset already filtered; a dashboard never recomputes. Named exceptions only through an explicit policy rule (audience, condition, threshold). | 7.6, 7.7 |
| GD10 | A8 reconciliation and data quality: source vs billed invoice, `cost_ref` vs `cost_source` gap, unallocated share, unresolved identities, freshness and lag, non-final share, unknown models. | 6.8, 6.9 |
| GD11 | B3 shadow scenarios: one policy file with N named scenarios; positions (month-to-date `cost_ref` vs a cap or envelope) and levels / unit envelopes are scenario parameters. Per scenario and period: who or which unit would have been stopped, amounts beyond cap, top-ups needed, alert crossings. The example policy shows four kinds: caps by attribute; one flat individual cap; unit envelopes plus a flat individual cap; one default cap for everyone. | 8.1 |
| GD12 | B4 + A7 alert-only: a daily safety threshold and a spike rule produce "would have tripped" events; nothing enforces. | 8.2 |
| GD13 | Oracle: a hand-built fixture whose expected outputs are computed by hand (Appendix A); then simulator data, where gold totals reconcile to silver and to the simulator's ground truth. | 14, App. A. The fixture's "2 units" are two working units under a root unit. |
| GD14 | v1 delivery = the per-audience files plus one static example dashboard (no auth code). SCIM, sender plugins, FOCUS, engagement levels, recommendations, enforcement, telemetry are deferred (listed under "Deferred by the owner"). | 11, 12 |
| GD15 | Gold schema version stamped in every output and in a manifest; rebuild from silver is the upgrade path. | 9 |

**Scope.** One package that reads files and writes files. It calls no provider API, needs no credential other than the hashing key, and never opens a network connection. It changes no other package.

## 2. Package and layout

```
packages/meterline-gold/
  pyproject.toml            name meterline-gold, version 1.0.0, script meterline-gold = meterline_gold.cli:main
                            dependencies: meterline (this repo), duckdb>=1.5,<2, pydantic>=2.6,<3, PyYAML>=6.0,<7
                            optional: parquet = ["pyarrow"]; dev = ["pytest", "hypothesis"]
  src/meterline_gold/
    __init__.py             build(), GOLD_SCHEMA_VERSION = "1.0", __version__
    params.py               Pydantic models of the policy and the price list (4), YAML loading, schema export
    load.py                 stage 0: silver (latest_per_key), organisation files, keyed hashes, price list, invoice -> DuckDB tables (3, 5)
    run.py                  executes sql/*.sql in name order, runs the privacy loop (7.4), runs the assertion queries, fetches each dataset
    write.py                canonical CSV writer, folder layout, manifest (9)
    importers/litellm.py    offline LiteLLM price importer (3.4)
    cli.py                  main(argv=None)
    sql/                    00_macros.sql, 10_org.sql, 20_person_day.sql, 30_person_month.sql, 40_seats_pools.sql,
                            50_reconciliation.sql, 60_groups.sql, 70_privacy_step.sql, 80_scenarios.sql, 85_alerts.sql,
                            90_audiences.sql, 99_assertions.sql
    schemas/                policy.schema.json, prices.schema.json (generated by params.py, committed)
    examples/               policy.example.yaml (4.4), prices.example.yaml (3.3)
    dashboard/index.html    the static example dashboard (11)
  tests/                    done-checks (14); the fixture is read from docs/gold-fixture/
```

- **Runtime.** Python 3.10+. The SQL files are the engine. The Python code loads, hashes, loops and writes; it computes no dataset figure. Exceptions: the keyed hashes (DuckDB has no HMAC, analysis F3) and the conversion of per-million prices to per-token prices at load time (exact `Decimal`).
- **Versioning.** Every SQL file starts with `-- meterline-gold sql <GOLD_SCHEMA_VERSION>`. A change to any dataset's columns or formulas raises `GOLD_SCHEMA_VERSION`.
- **Library entry point.** `build(silver, org, policy, prices, out, invoice=None, parquet=False, key=None, threads=None) -> pathlib.Path`.
  - `key` is `bytes` and defaults to the environment variable, encoded as UTF-8.
  - `threads` (an int) sets DuckDB's `threads` and exists for the determinism check only.
- **Interfaces the tests use.**
  - `run.ASSERTIONS`: a list of (name, sql) pairs.
  - `run.run_privacy(con, k, *, _disable_block_rule=False)`: 7.4 on the cell tables.
  - `write.DATASET_KEYS`: dataset name → key columns.

## 3. Inputs and their schemas

### 3.1 Silver

`--silver DIR` is a folder with `records/<kind>/<batch_id>.jsonl` for the kinds `usage`, `activity`, `seats`, `seat_assignments`, `org_totals` (`docs/records.md`). A missing kind folder means no records of that kind.

- Every `*.jsonl` of a kind is read with `meterline.read_jsonl` and that kind's class, in sorted file-name order, then reduced with `meterline.latest_per_key`. Only that current state is used, except by the data-quality metric `final_rows_revised` (6.9), which also reads earlier batches.
- A line that fails to parse, or a v1 line, is an input error (exit 2).
- `Decimal` money values may have at most 12 decimals; more is an input error.

**Build period** [A].
- `data_through` = the latest `period_start` among current `grain` `day` records of any kind.
- `period_start` = the first day of the month of the earliest `period_start` among those records.
- The **months of the build** are the calendar months from `period_start` to the month of `data_through`. A month is `complete` when its last day ≤ `data_through`, else `running`. Its **window** is [first day, min(last day, `data_through`)] and its **evaluation day** `E` is the window's last day.
- `as_of` = the latest UTC date of `pulled_at` among current records.
- The **build period** is [`period_start`, `data_through`].
- An **earlier batch** of a record key is one with a smaller `batch_id`.
- No wall clock is read anywhere.

### 3.2 Organisation files

`--org DIR` holds the five files of the simulator's spec, section 11.1 (accepted). The same columns are required here, in the same order, UTF-8, header row; an empty field is null. Dates are inclusive and `valid_to` empty means open.

| File | Columns | Gold rules |
|---|---|---|
| `units.csv` | `unit_id, valid_from, valid_to, dimension, key, name, kind, parent_id, function, cost_center, head_person_id` | The first entry of the policy's `dimensions` list (default `[line]`) is the line dimension. Rows of one `unit_id` must not overlap in validity. |
| `people.csv` | `person_id, valid_from, valid_to, unit_id, manager_id, function, level, cost_center, population, attrs` | A person is **employed** on a day when one of their rows is valid that day. Rows of one person must not overlap. `unit_id` must be a line unit valid that day. `attrs` is a JSON object of strings. |
| `memberships.csv` | `person_id, unit_id, dimension, role, valid_from, valid_to` | Read and validated. v1 publishes no group by membership (12). |
| `identities.csv` | `person_id, provider, method, actor_kind, actor_id, actor_login, actor_email, valid_from, valid_to` | `method` ∈ `email, manual, scim, derived, oidc_claim, non_person`. `person_id` is empty exactly when `method` is `non_person`. Two rows with the same (`provider`, `actor_kind`, `actor_id`) and overlapping validity are an input error. |
| `directory.csv` | `person_id, given_name, family_name, email` | Read only to write `delivery/directory.csv`. |

A broken rule is an input error (exit 2) with a one-line reason naming the file and line.

### 3.3 Price list (`--prices FILE`)

YAML, loaded with `yaml.safe_load` and validated by `params.PriceList` (`extra="forbid"`). Every amount is a **quoted** decimal string (an unquoted number is a validation error, analysis F2). Dates are `YYYY-MM-DD`.

| Key | Type | Meaning |
|---|---|---|
| `price_list_format` | int | `1`. |
| `as_of` | date | The date the prices were read or decided. |
| `source` | string | Where they come from, in words. |
| `currency` | string | ISO code; must equal the policy's `currency`. |
| `models` | list | The **model catalogue**: `{model, vendor, family, tier}`, all strings, `model` unique. |
| `token_prices` | list | `{provider, model, valid_from, per_million}`. `provider` is a provider name or `"*"` (any). `per_million` is null (unpriced) or `{input_uncached, cache_read, cache_write_5m, cache_write_1h, output}`, each a decimal string with at most 6 decimals; `cache_write_1h` may be null (priced at `cache_write_5m`). (`provider`, `model`, `valid_from`) is unique. |
| `unit_prices` | list | `{provider, unit, valid_from, price}`: the price of one unit of a `quantities` key (v1 uses `ai_credit`). |
| `included` | list | `{provider, product}`: usage whose cost is carried by another record of the same provider (Copilot CLI and app tokens, analysis F4). |
| `seats` | list | `{provider, org_id, valid_from, per_month, included_credits}`. `org_id` may be null (any org of that provider). `included_credits` is null, or the credits per billed seat pooled at the billing entity. |

**Version rule.** An entry applies to a day when it has the latest `valid_from` on or before that day among the entries of the same match key.
- A `token_prices` entry for the record's own provider wins over a `"*"` entry.
- A `seats` entry with the row's `org_id` wins over one with a null `org_id`.
- With no entry valid on the day, the record is `unpriced` (6.2.1), or the seat price is null (6.5).

The example file `examples/prices.example.yaml` holds invented values for the simulator's invented models (`model-a-light`, `model-a-standard`, `model-a-premium`, `model-b-light`, `model-b-premium`, `selfhosted-c-small`). Its token prices equal the simulator's price table before the price change, it gives `selfhosted-c-small` explicit zero prices (free, not unpriced), and its `source` reads "Invented example values; not any provider's price list or any organisation's contract."

### 3.4 Offline LiteLLM importer

`meterline-gold import-litellm-prices --file FILE --keys FILE --as-of YYYY-MM-DD --out FILE` reads a **local copy** of LiteLLM's `model_prices_and_context_window.json` (analysis F1) and writes the `token_prices` and `models` entries of a price list. It never opens a network connection.

- `--keys` is a text file with one LiteLLM key per line. Only those keys are imported; a key that is missing from the file is an error (exit 2).
- For each key, the importer reads only `input_cost_per_token`, `cache_read_input_token_cost`, `cache_creation_input_token_cost`, `cache_creation_input_token_cost_above_1hr`, `output_cost_per_token` and `litellm_provider`. Every other field is ignored.
- Each number is parsed from its JSON text into `Decimal` (`json.loads(..., parse_float=Decimal, parse_int=Decimal)`), multiplied by 1,000,000 and quantized to 6 decimals (`ROUND_HALF_EVEN`).
- Mapping:
  - `per_million.input_uncached` ← input, `cache_read` ← cache read, `cache_write_5m` ← cache creation, `cache_write_1h` ← cache creation above 1 hour (null if absent), `output` ← output.
  - A missing `cache_read` or `cache_write_5m` price is taken as the input price; a missing 1-hour price stays null.
  - A missing input or output price gives `per_million: null`.
- Entry fields:
  - `provider` is `"*"` and `model` is the key with everything up to its last `/` removed.
  - `valid_from` is the `--as-of` value as a string.
  - Two keys that give the same `model` are an error (exit 2).
  - The catalogue entry is `{model, vendor: litellm_provider, family: "", tier: "unclassified"}`, which the owner edits by hand.
- The output has `price_list_format: 1`, `as_of` (the `--as-of` value as a string), `source: "LiteLLM model_prices_and_context_window.json, local copy, keys listed in <keys file base name>"`, `currency: USD` and empty `unit_prices`, `included` and `seats` lists.
- `models` and `token_prices` are sorted by `model`. Prices are strings with exactly 6 decimals.
- The file is written with `yaml.safe_dump(obj, sort_keys=True, default_flow_style=False, allow_unicode=True)`.

### 3.5 Invoice (`--invoice FILE`, optional)

CSV with columns `provider, org_id, month (YYYY-MM), currency, kind, amount`, where `kind` is `usage` or `seats` and `amount` is a decimal string. (`provider`, `org_id`, `month`, `kind`) is unique. A row whose `currency` differs from the reference currency is an input error (exit 2). Without the file, the invoice columns of 6.8 are null.

### 3.6 The hashing key

Read from the environment variable `METERLINE_GOLD_KEY` and encoded as UTF-8. It must be at least 32 bytes; a missing or shorter key is exit 2. It is never written anywhere. Tests use the fixed key `meterline-gold-fixture-key-not-a-secret` (39 bytes), and only tests.

## 4. The policy file (`--policy FILE`)

YAML, loaded with `yaml.safe_load`, validated by `params.Policy` (`extra="forbid"`, strict types). Amounts, thresholds and fractions are quoted decimal strings; counts are integers. `schemas/policy.schema.json` is `Policy.model_json_schema(mode="validation")` written with `json.dumps(..., sort_keys=True, indent=2)` plus a final newline.

### 4.1 Keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `policy_format` | int | required | `1`. |
| `name` | string | required | `^[a-z][a-z0-9-]*$`. |
| `label` | string | required | What the file is, in words. |
| `currency` | string | required | Reference currency of `cost_ref`; must equal the price list's. |
| `dimensions` | list of strings | `[line]` | Unit dimensions; the first is the line dimension. |
| `people.attrs_keep` | list of strings | `[]` | `attrs` keys copied into gold `people` and usable in the grid. Other keys are dropped. |
| `privacy.k` | int ≥ 2 | required | Minimum group size. |
| `privacy.tree` | object | required | The fixed grid (7.1): `{kind: unit}` or `{kind: attributes, attributes: [a1, a2, ...]}`. |
| `privacy.collapse` | map attribute → map value → value | `{}` | Applied to person attributes before any grouping (7.1). |
| `privacy.compare_by` | list of attributes | required | Comparison groups (7.5). |
| `bands.metric` | `cost_ref` or `tokens` | `cost_ref` | What the intensity band is computed on (`tokens` = `tokens_input_total` + `tokens_output`). |
| `bands.thresholds` | list of `{band, from}` | required | Ascending `from` (decimal string; integer string when the metric is `tokens`); the first `from` is `"0"`. A value's band is the last one whose `from` ≤ value. |
| `premium_tiers` | list of strings | `[]` | Catalogue tiers that count as premium. |
| `seats.idle_after_days` | int ≥ 1 | required | N in the idle rule (6.5). |
| `pools.budgets` | list of `{provider, org_id, monthly_amount}` | `[]` | Monthly overage budget of a pool. |
| `alerts.daily_breaker` | decimal string or null | null | Daily `cost_ref` per person above which a breaker event is written (8.2). |
| `alerts.spike` | object or null | null | `{multiplier, floor, trailing_days, min_history_days}` (8.2). |
| `audiences.exec.max_depth` | int ≥ 0 | `1` | Deepest grid cell the exec audience receives (7.6). |
| `named_exceptions` | list | `[]` | `{id, audience, condition, threshold}` (7.7). |
| `scenarios` | list | `[]` | Named shadow scenarios (8.1). |

`attributes` usable in `privacy.tree`, `privacy.compare_by` and scenario `person_caps.by` are `function`, `level`, `cost_center`, `population` and `attrs.<key>` for a key in `people.attrs_keep`.

### 4.2 Scenario entries

| Key | Type | Meaning |
|---|---|---|
| `id` | string | `^[a-z][a-z0-9_]*$`, unique. |
| `label` | string | In words. |
| `person_caps` | object or null | `{default, by, values}`: `default` (decimal string or null), `by` (an attribute or null), `values` (map attribute value → decimal string). A person's cap is `values[their value of by]` if present, else `default`; no cap when both are absent. |
| `unit_envelopes` | object or null | `{values}`: map line-unit `key` → decimal string. A unit's envelope covers everyone attributed to its subtree (5.4). |
| `alert_fractions` | list of decimal strings | Fractions of a cap or envelope at which an alert event is written (e.g. `"0.8"`). |
| `top_up_amount` | decimal string or null | Size of one top-up. |

### 4.3 Validation beyond types

- Every attribute named exists in 4.1's list.
- `bands.thresholds` is strictly ascending.
- `named_exceptions[].condition` ∈ `month_cost_ref_above`, `breaker_events_at_least`, `spike_events_at_least`; `audience` ∈ `central`, `manager`.
- `month_cost_ref_above` is allowed with audience `central` only (7.7).
- A count threshold is an integer string; a money threshold is a decimal string.
- `privacy.tree.kind` `attributes` needs at least one attribute and no repeats.
- `unit_envelopes` keys exist in `units.csv` (checked after loading the organisation files).

A failure is exit 2 with the failing key path.

### 4.4 The invented example policy (`examples/policy.example.yaml`)

Every amount is invented and visibly synthetic. **Rule (GD1):** every money amount of the example policy (caps, envelopes, budgets, the breaker, the spike floor, top-ups, money thresholds of named exceptions; not band thresholds, which start at `"0"`) is written with exactly two decimals and a non-zero last digit, like `123.45`. A round figure therefore cannot appear. The check G17 holds the file to this rule.

```yaml
policy_format: 1
name: example-policy
label: "Invented example policy for meterline-gold. Every value is made up for illustration; it is not any organisation's policy."
currency: USD
dimensions: [line]
people:
  attrs_keep: [location]
privacy:
  k: 5
  tree: {kind: unit}
  collapse:
    level: {L1: L1-L3, L2: L1-L3, L3: L1-L3}
  compare_by: [function, level]
bands:
  metric: cost_ref
  thresholds:
    - {band: none, from: "0"}
    - {band: light, from: "0.01"}
    - {band: regular, from: "12.34"}
    - {band: heavy, from: "123.45"}
    - {band: very_heavy, from: "456.78"}
premium_tiers: [premium]
seats:
  idle_after_days: 21
pools:
  budgets:
    - {provider: github_copilot, org_id: "org:example-vertical", monthly_amount: "234.56"}
alerts:
  daily_breaker: "87.65"
  spike: {multiplier: "5", floor: "45.67", trailing_days: 7, min_history_days: 5}
audiences:
  exec: {max_depth: 2}
named_exceptions:
  - {id: high-month, audience: central, condition: month_cost_ref_above, threshold: "1234.56"}
  - {id: spikes, audience: manager, condition: spike_events_at_least, threshold: "2"}
scenarios:
  - id: tiered_caps
    label: "Monthly caps by level band"
    person_caps:
      default: "67.89"
      by: level
      values: {L1-L3: "456.78", L4: "234.56", L5: "67.89"}
    unit_envelopes: null
    alert_fractions: ["0.5", "0.8", "1.0"]
    top_up_amount: "23.45"
  - id: flat_cap
    label: "One flat individual safety net for everyone, no personal levels"
    person_caps: {default: "876.54", by: null, values: {}}
    unit_envelopes: null
    alert_fractions: ["0.8", "1.0"]
    top_up_amount: null
  - id: unit_caps_plus_flat
    label: "Monthly envelopes for divisions plus the flat individual cap"
    person_caps: {default: "876.54", by: null, values: {}}
    unit_envelopes:
      values: {commercial: "3456.78", operations: "2345.67", finance-legal: "1234.56", people: "987.65", product: "1876.54", central-it: "5432.19"}
    alert_fractions: ["0.5", "0.8", "1.0"]
    top_up_amount: "345.67"
  - id: default_cap
    label: "The default cap for everyone"
    person_caps: {default: "67.89", by: null, values: {}}
    unit_envelopes: null
    alert_fractions: ["0.8", "1.0"]
    top_up_amount: "23.45"
```

The unit keys are those of the simulator's `vertical` preset (invented). The fixture has its own policy (`docs/gold-fixture/input/policy.yaml`).

## 5. Identity and attribution

### 5.1 Keyed hashes

`H(s)` = the first 16 hex digits of `HMAC-SHA256(key, s.encode("utf-8"))` (Python's `hmac` and `hashlib`).

| Key | Value |
|---|---|
| `person_key` | `"pk_" + H("person|" + person_id)` |
| `actor_key` | `"ak_" + H("actor|" + provider + "|" + actor_kind + "|" + actor_id)` |
| `manager_key`, `head_key` | the `person_key` of `manager_id`, `head_person_id` |
| key check (manifest) | `"kc_" + H("meterline-gold key check")` |

Two `person_id`s with the same `person_key` stop the build (exit 1). `person_id`, `actor_id`, `actor_login`, `actor_email` and names never reach a gold dataset; the hashes are computed in `load.py` before anything enters DuckDB.

### 5.2 Resolving a silver actor

For each current `UsageRecord` and `ActivityRecord` of `grain` `day`, with day d:

1. `actor_kind` `end_user`: the record (usage or activity) is excluded from every dataset and counted in `rows_end_user_excluded` (6.9). (`proxy_user` plus `unassigned` already cover that traffic; analysis F4.)
2. `actor_kind` `unassigned`, `service_account` or `api_key`: `identity_status` `non_person`, `identity_method` null, `actor_key` set.
3. Otherwise, the `identities.csv` row with the same `provider`, `actor_kind` and `actor_id`, valid on d:
   - with `method` `non_person`: `non_person`, `identity_method` `non_person`, `actor_key` set;
   - with a person: `resolved`, `identity_method` = its `method`, `person_key` set, `actor_key` null;
   - none: `unresolved`, `actor_key` set.

Identity validity alone decides resolution [A]. A resolved row on a day the person is not employed stays resolved and counts toward that person's month.

### 5.3 Employed days

A person's **employed days** in a month are the days of the month's window on which they are employed (3.2).

### 5.4 Attribution

Each person with at least one employed day in a month gets one `person_month` row. Their **attribution day** is their last employed day in the month's window.

Their `unit_id`, `manager_key`, `function`, `level`, `cost_center`, `population` and kept `attrs` are those of the `people.csv` row valid on that day.

Their **unit path** is that unit and its ancestors, read from the `units.csv` rows valid on the attribution day. A whole month of a person counts toward that one path: v1 does not split a month between units [A].

## 6. Datasets

### 6.0 Conventions

- **Columns.** Every dataset's first column is `schema_version` (`GOLD_SCHEMA_VERSION`, `1.0`).
  - A table that lists every column gives them in output order.
  - A table that does not list its key columns (6.5-6.9) has, after `schema_version`, the key columns in key order and then the table's columns in order.
  - "Key" is the unique key; rows are sorted by it (9.2).
- **Sums.** "Σ" over a nullable field is the sum of its non-null values, null when all are null. Where a column says "0 when none", it is never null.
- **Types.** `money` is `DECIMAL(38,12)` in the engine and written by the money rule. `ratio` is written with exactly 4 decimals. `count` is an integer. `date` is `YYYY-MM-DD`. `map` is a JSON object (9.2).
- **Exact division.** Two macros in `00_macros.sql` compute every published quotient, for n ≥ 0 and d > 0. Both are null when n or d is null or d = 0.
  - `ratio4(n, d)` = n/d rounded half up to 4 decimals.
  - `qdiv(n, d, s)` = n/d rounded half up to s decimals.
  - Reference implementation: scale n and d by 10^12, cast to `HUGEINT`, and compute `(2·N·10^s + D) // (2·D)` with integer division, then scale by 10^-s.
  - `qceil(n, d)` = the smallest integer ≥ n/d, computed the same way.
  - No `/` operator appears in any SQL file other than `00_macros.sql` (string literals such as `'/'` excepted), and no `DOUBLE` value reaches an output.
- **Quantiles.** Nearest rank, with no interpolation. Order a cell's persons by (value ascending, `person_key` ascending). The q-quantile is the value at rank `r = ceil(q·n)` (1-based). This is DuckDB's `quantile_disc` definition (analysis F3); the SQL writes it with `ROW_NUMBER()`.

### 6.1 Organisation datasets (A1; audience central)

| Dataset | Key | Columns |
|---|---|---|
| `people` | `person_key, valid_from` | `person_key, valid_from, valid_to, unit_id, manager_key, function, level, cost_center, population, attrs` (map of the kept keys) |
| `units` | `unit_id, valid_from` | `unit_id, valid_from, valid_to, dimension, key, name, kind, parent_id, function, cost_center, head_key` |
| `identity_map` | `provider, actor_kind, actor_key, valid_from` | `person_key` (null for `non_person`), `provider, method, actor_kind, actor_key, valid_from, valid_to` |

`delivery/directory.csv` (not an audience dataset): `schema_version, person_key, given_name, family_name, email`, one row per person with a `person_month` row, key `person_key`. It is the only output holding names or emails.

### 6.2 `person_day` (A2; audience central, and each person their own rows)

**Grain and key:** `day, provider, org_id, person_key, actor_key, product, model`. Resolved records merge on `person_key` (a person with two accounts of one provider gets one row); other records stay apart by `actor_key`.

| Column | Type | Null | Formula |
|---|---|---|---|
| `day` | date | no | `period_start` (day grain only). |
| `month` | text | no | `YYYY-MM` of `day`. |
| `provider`, `org_id` | text | no | From silver. |
| `person_key` | text | yes | 5.2. |
| `actor_key` | text | yes | 5.2. |
| `identity_status` | text | no | `resolved`, `non_person`, `unresolved`. |
| `identity_method` | text | yes | 5.2; `mixed` if merged records differ. |
| `actor_kind` | text | no | From silver; `mixed` if merged records differ. |
| `product`, `model` | text | no | From silver; `""` stays `""`. |
| `model_tier` | text | yes | Catalogue `tier` of `model`; null for `""` or an unknown model. |
| `requests` | count | yes | Σ `requests`. |
| `tokens_input_uncached`, `tokens_cache_read`, `tokens_cache_write`, `tokens_cache_write_5m`, `tokens_cache_write_1h`, `tokens_input_total`, `tokens_output` | count | yes | Σ of the silver field. |
| `credits` | quantity | yes | Σ `quantities.ai_credit`. |
| `cost_source` | money | yes | Σ silver `cost`. |
| `cost_source_basis` | text | yes | The silver `cost_basis` of the records with a cost; `mixed` if they differ; null if none. |
| `cost_source_currency` | text | yes | Likewise from `currency`. |
| `cost_ref` | money | yes | 6.2.1; null when any merged record's is null. |
| `cost_ref_status` | text | no | 6.2.1; over merged records the first of `unknown_model`, `unpriced`, `priced`, `included` that any record has. |
| `is_final` | bool | no | AND of the records' `is_final`. |
| `source_records` | count | no | Number of silver records merged. |

#### 6.2.1 Reference cost of one silver record

For a record on day d, with prices valid on d (3.3), take the first rule that applies:

1. (`provider`, `product`) is in `included`: `cost_ref` = 0, status `included`.
2. `quantities.ai_credit` is set:
   - with a `unit_prices` entry for (`provider`, `ai_credit`): `cost_ref` = credits × `price`, status `priced`;
   - otherwise null, status `unpriced`.
3. `model` is `""`: null, `unpriced`.
4. `model` is not in the catalogue: null, `unknown_model`.
5. The `token_prices` entry for (`provider`, `model`), else for (`"*"`, `model`):
   - absent, or `per_million` null: null, `unpriced`;
   - otherwise status `priced`, with `cost_ref` = Σ over buckets of tokens × (per-million price / 1,000,000). The per-token prices are exact decimals computed at load time. The buckets:
     - `tokens_input_uncached` × `input_uncached`;
     - `tokens_cache_read` × `cache_read`;
     - the cache writes: `tokens_cache_write_5m` × `cache_write_5m` + `tokens_cache_write_1h` × `cache_write_1h` (or `cache_write_5m` when that price is null) when either split field is set; otherwise `tokens_cache_write` × `cache_write_5m`;
     - `tokens_output` × `output`.
   - A null token field counts 0.
   - If `tokens_input_uncached`, `tokens_cache_read` and `tokens_cache_write` are all null and `tokens_input_total` is set, `tokens_input_total` is priced at `input_uncached`.

`cost_ref` is exact. No rounding happens before output.

### 6.3 `person_day_activity` (A2; audience central)

Key `day, provider, org_id, person_key, actor_key, product`. Built from current `ActivityRecord`s of `grain` `day`, resolved by 5.2.

Columns:
- `day`, `month`, `provider`, `org_id`, `person_key`, `actor_key`, `identity_status`, `product`;
- `sessions`, `messages`, `requests`, `suggestions_shown`, `suggestions_accepted`, `lines_added`, `lines_removed`, `edits_accepted`, `edits_rejected`, `commits`, `pull_requests` (each a Σ count, nullable);
- `source_records`.

The `counters` map is not carried in v1 (telemetry and value metrics are deferred).

### 6.4 `person_month` (A3; audience central, and each person their own rows)

**Grain and key:** `month, person_key`. One row per person with at least one employed day in the month (5.4), whether or not they used anything. Only `resolved` rows of `person_day` and `person_day_activity` count toward a person.

| Column | Type | Null | Formula |
|---|---|---|---|
| `month` | text | no | `YYYY-MM`. |
| `person_key` | text | no | |
| `month_status` | text | no | `complete` or `running` (3.1). |
| `attribution_day` | date | no | 5.4. |
| `unit_id`, `manager_key`, `function`, `level`, `cost_center`, `population` | text | `manager_key` yes | 5.4 (values before `collapse`). |
| `employed_days` | count | no | 5.3. |
| `active_days` | count | no | Distinct days of the month with any `person_day` or `person_day_activity` row of the person. |
| `active_days_by_tool` | map | no | Tool → distinct days, where the tool is `provider` when `product` is `""`, else `provider + "/" + product`, over both datasets. `{}` when none. |
| `requests`, `tokens_input_total`, `tokens_output`, `tokens_cache_read` | count | yes | Σ over the person's `person_day` rows of the month. |
| `credits` | quantity | yes | Σ `credits`. |
| `cost_ref` | money | no | Σ `cost_ref`; 0 when none. |
| `cost_ref_by_provider` | map | no | provider → Σ `cost_ref`, for providers with at least one non-null `cost_ref`. |
| `rows_unpriced`, `rows_unknown_model` | count | no | Rows with that `cost_ref_status`. |
| `premium_share` | ratio | yes | `ratio4`(Σ `cost_ref` of rows whose `model_tier` is in `premium_tiers`, 0 when none; `cost_ref`); null when `cost_ref` is 0. |
| `cache_rate` | ratio | yes | `ratio4`(Σ `tokens_cache_read`, Σ `tokens_input_total`) over rows where both are non-null; null when there are none or the denominator is 0. |
| `intensity_band` | text | no | 4.1 `bands`, on `cost_ref` (or tokens). |
| `is_final` | bool | no | `month_status` is `complete` and every `person_day` row of the person in the month is final. |
| `projected_cost_ref` | money | yes | Null when `complete`. When `running`: `qdiv(cost_ref × days_in_month, days_elapsed, 2)`, where `days_elapsed` = `data_through` − first day + 1. |

### 6.5 `seats_month` (A5; audiences central, exec)

**Grain and key:** `month, provider, org_id`.
- Rows exist only for providers with a `seats` entry in the price list.
- For such a provider, there is one row per `org_id` that has `SeatAssignment` records in current silver.
- If it has none, there is one row from identities, as below.

**Seat holders on day d** [A]:
- **From assignments.** When current silver holds `SeatAssignment` records for (`provider`, `org_id`), the holders are the actors in the latest snapshot day ≤ d. A snapshot day is a `period_start` with at least one assignment. If no snapshot day is ≤ d, there are none. `seat_source` is `assignments`.
- **From identities.** Otherwise, the persons with an `identities.csv` row of that provider (method other than `non_person`) valid on d who are employed on d. `seat_source` is `identities`. `org_id` is that provider's only `org_id` among current silver records, or `""` if it has none or several; with `""`, the `seats` entry is matched on provider alone.
- A holder is matched to its usage by `person_key` when its actor resolves (5.2), else by `actor_key`.

| Column | Type | Formula |
|---|---|---|
| `seat_source` | text | As above. |
| `eval_day` | date | The month's `E`. |
| `seats_billed` | count | Distinct holders on any day of the window (no proration). |
| `seats_on_eval_day` | count | Holders on `E`. |
| `active_seats` | count | Holders with at least one day in the window that has a `person_day` or `person_day_activity` row of this provider. |
| `idle_seats` | count | Holders on `E` with no such day in [`E` − `idle_after_days` + 1, `E`]. Days before the build period count as inactive. |
| `seats_reported_max` | count, null | Highest `SeatSnapshot.seats` of (`provider`, `org_id`) in the window. |
| `seat_price` | money, null | `per_month` of the `seats` entry valid on the window's first day. |
| `seat_cost` | money, null | `seats_billed × seat_price`. |
| `currency` | text | The price list's. |

### 6.6 `pools_month` (A5; audiences central, exec)

**Key:** `month, provider, org_id`. A row exists for each `seats_month` row whose `seats` entry has `included_credits`. The billing records are the current `OrgTotal`s of that (`provider`, `org_id`), `grain` `day`, in the window, with `cost_basis` `billed`.

| Column | Type | Formula |
|---|---|---|
| `seats_billed` | count | From `seats_month`. |
| `included_per_seat` | quantity | From the `seats` entry. |
| `pool_credits` | quantity | `seats_billed × included_per_seat`. |
| `gross_credits` | quantity | Σ `quantities.ai_credit` of the billing records. |
| `pool_used_credits` | quantity | Σ `extras.discount_quantity` when every billing record has it; else `min(gross, pool)`. |
| `overage_credits` | quantity | Σ `extras.net_quantity` when every record has it; else `gross − pool_used`. |
| `overage_amount` | money | Σ `cost` of the billing records. |
| `pool_used_share` | ratio, null | `ratio4(pool_used_credits, pool_credits)`. |
| `budget_amount` | money, null | `pools.budgets` entry for (`provider`, `org_id`). |
| `budget_state` | text | `none` without a budget; `crossed` when `budget_crossed_on` is set; else `within`. |
| `budget_crossed_on` | date, null | The first day d of the window on which Σ `cost` of the billing records dated ≤ d is > `budget_amount`. |
| `currency` | text | |
| `is_final` | bool | AND of the billing records' `is_final`. |

### 6.7 `costs_month` (A5; audiences central, exec)

**Key:** `month, provider, org_id`, for every (provider, org) with `person_day` rows or a `seats_month` row.

| Column | Formula |
|---|---|
| `currency` | The reference currency. |
| `usage_cost_source` | Σ `person_day.cost_source` (all identity statuses). |
| `cost_source_basis` | As in `person_day`, over those rows. |
| `usage_cost_ref` | Σ `person_day.cost_ref`; 0 when none. |
| `seat_cost` | From `seats_month`; null without one. |
| `total_cost_ref` | `usage_cost_ref + seat_cost` (a null seat cost counts 0). |

Seat cost appears here and in `seats_month` only; no person's `cost_ref` or scenario position includes it (GD7).

### 6.8 `reconciliation_month` (A8; audience central)

**Key:** `month, provider, org_id`, for every (provider, org) with day-grain usage or org totals in the month. The org figures come from current `OrgTotal`s of `grain` `day` in the window.

| Column | Formula |
|---|---|
| `currency` | The reference currency. |
| `actor_cost_source` | Σ `person_day.cost_source`. |
| `org_cost_source` | Σ `OrgTotal.cost`. |
| `org_cost_basis` | Their `cost_basis`; `mixed` if they differ. |
| `unallocated_cost_source` | `org_cost_source − actor_cost_source`, when `org_cost_basis` equals `cost_source_basis` of the actor rows; else null. |
| `unallocated_share` | `ratio4(unallocated_cost_source, org_cost_source)`; null when either is null or the unallocated amount is negative. |
| `actor_credits`, `org_credits` | Σ `credits` / Σ `OrgTotal.quantities.ai_credit`. |
| `unallocated_credits` | `org_credits − actor_credits`; null if either is null. |
| `cost_ref` | Σ `person_day.cost_ref`. |
| `ref_vs_source_gap` | `cost_ref − actor_cost_source` (may be negative). |
| `ref_vs_source_share` | `ratio4(|gap|, actor_cost_source)`. |
| `invoice_usage`, `invoice_usage_gap` | Invoice amount of kind `usage`; `invoice_usage − org_cost_source`. |
| `invoice_seats`, `invoice_seats_gap` | Invoice amount of kind `seats`; `invoice_seats − seat_cost`. |

**Nulls.**
- `actor_cost_source`, `actor_credits` and `cost_ref` are Σ over the (provider, org, month)'s `person_day` rows: null when no row has a value.
- A difference or a share is null when either input is null.
- `org_*` figures are null when the org has no `OrgTotal` in the window.

### 6.9 `data_quality` (A8; audience central)

A long table. **Key:** `month, provider, org_id, metric`. Columns: `month` (null for freshness rows), `provider`, `org_id`, `metric`, `value` (text, formatted by the metric's type).

Per (month, provider, org_id) with `person_day` rows, every one of these metrics is written (0 when none):

| Metric | Type | Definition |
|---|---|---|
| `rows_total` | count | `person_day` rows. |
| `rows_unresolved`, `actors_unresolved`, `cost_ref_unresolved` | count, count, money | Rows with `unresolved`; distinct `actor_key`s among them; their Σ `cost_ref` (0 when none). |
| `rows_non_person`, `cost_ref_non_person` | count, money | The same for `non_person`. |
| `unallocated_share` | ratio | `ratio4(cost_ref_unresolved + cost_ref_non_person, Σ cost_ref)`; `0.0000` when Σ `cost_ref` is 0. |
| `rows_unknown_model`, `models_unknown` | count | Rows with `unknown_model`; distinct models among them. |
| `rows_unpriced` | count | Rows with `unpriced`. |
| `rows_non_final`, `non_final_share` | count, ratio | Rows not final; `ratio4(Σ cost_ref of non-final rows, Σ cost_ref)`, `0.0000` when the denominator is 0. |
| `rows_end_user_excluded` | count | Silver `end_user` records excluded (5.2). |
| `final_rows_revised` | count | Current day-grain usage records whose key also has a record in an earlier batch that was `is_final` true and differs in any field other than `pulled_at`, `data_refreshed_at` and `batch_id`. |
| `month_credit_mismatches` | count | Actors with a current `grain` `month` usage record for the month whose Σ `ai_credit` over models ≠ their Σ daily `ai_credit` in the month. |

Freshness, per (provider, org_id) with any current day-grain record, `month` null:
- `latest_day` (date): the latest `period_start` of that (provider, org_id)'s day-grain records of any kind;
- `lag_days` (count): `as_of − latest_day` in days, with the build's single `as_of` (3.1) [A].

`final_rows_revised` and `month_credit_mismatches` are Copilot-shaped (12, fixed in v1). They are written for every provider and are 0 where the provider has no such records.

## 7. Groups and privacy

### 7.1 The fixed grid

The grid is **one tree**, declared by `privacy.tree` [A; analysis "Why one tree"; card 2].

- **`kind: unit`.**
  - The nodes are the org cell (everyone attributed in the month) and one cell per line unit on someone's unit path (5.4). A unit cell holds everyone whose unit path contains that unit.
  - The parent of a unit cell is the cell of its parent unit; the org cell is the parent of a root unit.
  - The **direct members** of a unit cell are the persons whose own unit is that unit. The org cell has no direct members, because 3.2 requires every person to have a line unit.
- **`kind: attributes`, `[a1, ..., an]`.**
  - Level j holds one cell per distinct tuple (a1, ..., aj) among attributed persons. Its parent is the tuple without its last value, and the org cell is the root.
  - Direct members are always empty.

**Collapsing and banding first (GD8).**
- `privacy.collapse` maps attribute values before any cell, comparison group or scenario cap is formed (for example `L1`, `L2`, `L3` → `L1-L3`). A null value becomes `(none)`.
- With `kind: unit`, collapsing is structural: a unit below `k` is suppressed, and its people stay counted in every ancestor cell.
- Persons are compared to each other only through bands (6.4 `intensity_band`, 7.5 `comparison_band`), never by rank with a name.

**Cell ids.**

| Cell | `cell_id` | `group_id` | `cell_key` | `depth` |
|---|---|---|---|---|
| org | `org:` | `org` | `""` | 0 |
| unit | `unit:<unit_id>` | `unit` | unit id | 1 for a root unit, +1 per level |
| attribute level j | `attr:<a1>=<v1>|...|<aj>=<vj>` | `attr` | text after `attr:` | j |

An attribute name in a cell id is written `attrs.<key>` for kept attrs.

**Laminarity.** In each month, any two cells are either disjoint or one contains the other. Otherwise the build stops (exit 1). Only a reorganisation that inverts a parent and child inside one month can break this.

**Receivers.** The **receivers** of a unit cell on a month are the heads (`head_person_id` of the rows valid on that month's `E`) of its unit and of every ancestor unit. These are the people who get the cell in a manager folder (7.6) and who know their own figures. Other cells have no receivers.

### 7.2 `groups_month` (central; filtered copies for manager and exec)

**Key:** `month, cell_id`.

**Columns:** `month, cell_id, group_id, cell_key, depth, parent_cell_id` (`""` for the org cell), `suppressed, suppression_reason, persons, active_persons, active_days, cost_ref, p50_cost_ref, p75_cost_ref, p90_cost_ref, p99_cost_ref, top_decile_share, is_final`.

Per cell and month, over the attributed persons' `person_month` rows:

| Column | Type | Formula |
|---|---|---|
| `persons` | count | Count. |
| `active_persons` | count, null | Persons with `active_days` > 0, decided after 7.4 in its own pass until nothing changes. A cell's value is null unless the active and the inactive persons of the cell each number at least `k`. It is also null when the cell's **active remainder** fails: the persons of the cell outside its nearest descendants whose `active_persons` is published, if any, must have at least `k` active and at least `k` inactive persons, both with and without the cell's receivers. |
| `active_days` | count | Σ `active_days`. |
| `cost_ref` | money | Σ `cost_ref`. |
| `p50_cost_ref`, `p75_cost_ref`, `p90_cost_ref`, `p99_cost_ref` | money, null | Nearest-rank quantiles of per-person `cost_ref` (6.0), under the subset rule. |
| `top_decile_share` | ratio, null | **Org cell only**; null on every other cell. `ratio4`(Σ `cost_ref` of the m = `ceil(n/10)` persons ranked highest, cell `cost_ref`), under the subset rule; null when the cell's `cost_ref` is 0. |
| `is_final` | bool | AND of the persons' `is_final`. |

**Subset rule.** A figure computed from a subset of a cell's persons is published only when that subset and the rest of the cell each hold at least `k` persons. For a quantile the subset is the persons at ranks ≤ r; for the top-decile share, the top m. Otherwise the figure is null.

### 7.3 Suppression: what is published

A suppressed row keeps `month`, `cell_id`, `group_id`, `cell_key`, `depth`, `parent_cell_id`, `suppressed` (`true`) and `suppression_reason` (`k`, `differencing` or `secondary`). Every other column is null.

A cell's state is decided once per build. Every audience gets the same state (A07).

### 7.4 The algorithm

**Input.** All cells of all months of the build, with their person sets (`person_key`s) and receivers.

**Steps.**
1. **Primary.** A cell is suppressed, reason `k`, when it has fewer than `k` persons, or when it has fewer than `k` persons once its receivers are left out. The second clause covers a bottom-level unit of exactly `k` people whose head could subtract their own figure.
2. **Iterate.** Repeat until an iteration suppresses nothing. One iteration is one execution of `70_privacy_step.sql`, and `run.py` stops when an iteration's `UPDATE` changes no row. Within an iteration, step a is applied first. Step b's decisions are then taken together from the state after a, and applied together.
   - **a. Differencing (GD8).**
     - Take every pair of unsuppressed cells (A, B) of the same month. Cells of different months are never compared (owner decision 2026-10-12, card 1), so each month's state depends on that month's data only.
     - Let s = |A Δ B|, the number of persons in exactly one of the two.
     - If 1 ≤ s < `k`, both are suppressed, reason `differencing`.
   - **b. Secondary suppression (GD8), two triggers.**
     - **b1. GD8 as written.** For every P with children, suppressed or not (GD8 is a sibling rule): if exactly one child of P is suppressed and at least one is not, the unsuppressed child with the fewest persons is suppressed (ties: the lowest `cell_id`).
     - **b2. The block rule** (a strengthening; analysis). For every unsuppressed P, with or without children:
       - Its **pieces** are its direct members (one piece, if any), plus one piece per suppressed child C: C's **uncovered part**, meaning C's persons outside C's nearest unsuppressed descendants (if non-empty).
       - Its **block** is the union of its pieces. Its **unknown block** is the block minus P's receivers.
       - P **fails** in three cases: 1 ≤ |block| < `k`, which protects against every viewer; 1 ≤ |unknown block| < `k`, which protects against a receiver who knows their own figures; or the block is non-empty and is exactly one piece that is a suppressed child's uncovered part.
       - A failing P gets the same action as b1: its smallest unsuppressed child is suppressed (ties: the lowest `cell_id`). If it has none, P itself is suppressed.
     - Reason `secondary` for both triggers.
3. **Figures.** Unsuppressed cells get the figures of 7.2.

**Termination.** Each continued iteration suppresses at least one more cell, so the loop ends.

**Manifest.** The manifest's `suppression` counts all cell-months (`cells`) and the suppressed ones by reason.

**Why this is enough on a tree.** This is argued in the analysis. Within one build and one month:
- the only exact facts derivable by adding and subtracting published cells are sums over unions of blocks;
- b2 keeps every block, with or without its receivers, at 0 or at least `k` persons, and never exactly one suppressed cell's part.

G11 tests this with an exact linear attacker. **Limits** (analysis): a retroactive correction to the inputs can make the same cell differ between two builds by one person's figure, which the deliver-once rule of the delivery recipe closes (card 1); outside knowledge is not modelled.

**Worked example** (k = 5). Unit R, headed by h, has three child teams A (6 persons), B (2) and C (7). The org cell equals R (16 persons). h receives R, A, B and C.

Month 1:
1. Primary: B (2 < 5) is suppressed, reason `k`.
2. Iteration 1.
   - a. org vs R gives s = 0; R vs A gives 10; R vs C gives 9; A vs C gives 13. No suppression.
   - b1. R has exactly one suppressed child (B), so its smallest unsuppressed child A (6 < 7) is suppressed.
   - b2. R's block is {h} ∪ B, 3 persons; minus the receiver h, 2 < 5, so R fails and A is suppressed again. The decision is the same; reason `secondary`.
3. Iteration 2.
   - a. Nothing new.
   - b1. Two children of R are suppressed, so it does not fire.
   - b2. R's block is {h} ∪ B ∪ A: 9 persons in 3 pieces, 8 without h. It passes. The org cell's block is empty.
   - The loop ends.
4. Published in month 1: org, R, C.

Month 2: one joiner j enters C (8 persons); nothing else changes. Month 2 is computed on its own:
- Primary: B is suppressed (`k`).
- b1 suppresses A (6 < 8).
- b2 passes at R: its block is {h} ∪ B ∪ A, 9 persons, 8 without h.
- Published in month 2: org, R, C. Month 1 is unchanged by month 2's arrival.

G22 runs this example as a test.

### 7.5 Comparison groups (`compare_by`)

**Cells.** Per month, the attributed persons are grouped by the `compare_by` attributes, after collapsing. A comparison cell id is `<a1>=<v1>|...|<an>=<vn>`.

**What a cell publishes.**
- `persons`, which is structural (from the organisation files, not usage).
- The cut points `p50_cost_ref`, `p75_cost_ref`, `p90_cost_ref`, each under the subset rule of 7.2.
- No sum, no active count and no share, and the cells are not part of the tree. Comparison cells can therefore cut across the tree without enabling linear differencing [A; card 3]. They take no part in 7.4.

**Comparison band.** Each person's band, against the published cut points (P50 ≤ P75 ≤ P90; the published ones always form a prefix):
- `up_to_p50` when value ≤ P50;
- `p50_to_p75` when P50 < value ≤ P75;
- `p75_to_p90` when P75 < value ≤ P90;
- otherwise `above_pXX`, where XX is the highest published cut;
- null when no cut is published.

| Dataset | Key | Columns |
|---|---|---|
| `comparison_groups` (central) | `month, compare_cell` | `month, compare_cell, persons, p50_cost_ref, p75_cost_ref, p90_cost_ref` |
| `comparison` (each person) | `month` | `month, compare_cell, own_cost_ref, comparison_band` |

A person sees their own cost and their band, never another person's figure or a cut-point amount (GD8).

### 7.6 Audiences and the folder layout

| Folder | Holds | Rule |
|---|---|---|
| `central/` | `people`, `units`, `identity_map`, `person_day`, `person_day_activity`, `person_month`, `seats_month`, `pools_month`, `costs_month`, `reconciliation_month`, `data_quality`, `groups_month`, `comparison_groups`, `named_exceptions`, `scenario_persons`, `scenario_units`, `scenario_summary`, `alert_events` | Pseudonymous person-level data, no names. Every grid cell, suppressed ones as 7.3. `named_exceptions` holds the rows of every rule. |
| `manager/<manager_key>/` | `groups_month`, `named_exceptions` | One folder per person who heads a line unit on some month's `E`, or who is the `manager_key` of an attributed person. `groups_month` holds the rows (same values as central) of the unit cells they head on that month's `E`, and of every unit cell below them. It never includes the org cell, and it is empty when the tree kind is `attributes`. `named_exceptions` holds the rows of `manager` rules for persons whose `manager_key` is this manager. |
| `person/<person_key>/` | `person_day`, `person_month`, `comparison` | One folder per person with a `person_month` row, holding only that person's rows. `person_day_activity` is central only. |
| `exec/` | `groups_month`, `seats_month`, `pools_month`, `costs_month`, `scenario_summary` | Filtered by 7.6.1. |
| `delivery/` | `directory.csv` | Not an audience dataset; for the deferred sender. It is never served by the dashboard. |

Every listed file is written even when it has no rows (header only), and a folder holds exactly the files listed. No dataset is sorted by a measure (9.2). A dashboard reading these files shows them as they are and computes nothing (GD9).

#### 7.6.1 Exec filtering

Exec receives organisation-level figures. Some of them are sums over small populations that cut across the tree (the people who use one provider), so the k rule applies to them too [A; card 7]. Here the **users** of a row are the distinct resolved persons with `person_day` rows of that row's own (provider, org_id, month).

| Dataset | Exec form |
|---|---|
| `groups_month` | The rows with `depth` ≤ `audiences.exec.max_depth`. |
| `costs_month` | Same columns. A row's usage figures (`usage_cost_source`, `cost_source_basis`, `usage_cost_ref`, `total_cost_ref`) are **hidden** when its users < `k`. Then, per month, so that the org cell minus the published rows cannot isolate the hidden ones: when the users of all hidden rows together number 1 to `k` − 1, the published row with the fewest users is also hidden (ties: the lowest provider, then `org_id`). This repeats until the hidden rows' users number 0 or at least `k`. Hidden figures are null. |
| `pools_month` | Same columns. `gross_credits`, `pool_used_credits`, `overage_credits`, `overage_amount`, `pool_used_share`, `budget_state` and `budget_crossed_on` are null when the row's (provider, org_id) is hidden in exec `costs_month`. |
| `seats_month` | Same columns. `active_seats` is null unless `active_seats` ≥ `k` and `seats_billed − active_seats` ≥ `k`. `idle_seats` is null unless `idle_seats` ≥ `k` and `seats_on_eval_day − idle_seats` ≥ `k`. |
| `scenario_summary` | Columns `scenario_id, month, persons, persons_stopped, units`. `persons_stopped` is null unless it is ≥ `k` and `persons − persons_stopped` ≥ `k`. |

### 7.7 Named exceptions

Each `named_exceptions` rule selects persons per month:

| `condition` | Selected when | `value` | Allowed audiences |
|---|---|---|---|
| `month_cost_ref_above` | `person_month.cost_ref` > threshold | that `cost_ref` (money) | `central` only, because a cost given to a head would let them subtract it from a block (analysis) |
| `breaker_events_at_least` | the person's breaker events in the month (8.2) ≥ threshold | the count | `central`, `manager` |
| `spike_events_at_least` | the person's spike events in the month ≥ threshold | the count | `central`, `manager` |

Dataset `named_exceptions`:
- **Key:** `month, rule_id, person_key`.
- **Columns:** `month, rule_id, audience, person_key, manager_key, condition, threshold` (the policy string as written), `value`.

A named exception carries a `person_key`, never a name. Names are joined only at delivery, from `delivery/directory.csv`.

## 8. Shadow scenarios and alert events

Nothing in this section stops, slows or notifies anyone. It writes what would have happened.

### 8.1 Scenarios (B3, with B1 and B2 as parameters)

For each scenario, each month and each subject:
- **Position on day d.** The month-to-date `cost_ref`: Σ of the subject's resolved `person_day.cost_ref` dated from the window's first day to d.
  - A unit's position adds up everyone attributed to its subtree that month (5.4).
  - Seat cost never counts.
  - Positions are evaluated on every day of the window.
- **Persons.** Every attributed person with a cap (4.2). The cap uses the collapsed attribute value (7.1).
- **Units.** Every unit whose `key` is in `unit_envelopes.values` and whose cell exists that month, suppressed or not.

| Output | Formula |
|---|---|
| `stopped_on` | The first day d of the window with position(d) > cap (strictly); null if none. |
| `excess` | max(0, position at the window's end − cap) (money; `0.00` when not exceeded). |
| `top_ups` | `qceil(excess, top_up_amount)` (0 when `excess` is 0); null without `top_up_amount`. |
| `top_up_total` | `top_ups × top_up_amount`; null without `top_up_amount`. |

| Dataset | Key | Columns |
|---|---|---|
| `scenario_persons` (central) | `scenario_id, month, person_key` | `scenario_id, month, person_key, cap, cost_ref` (position at the window's end), `stopped_on, excess, top_ups, top_up_total` |
| `scenario_units` (central) | `scenario_id, month, unit_id` | `scenario_id, month, unit_id, envelope, position, stopped_on, excess, top_ups, top_up_total` |
| `scenario_summary` (central; exec form in 7.6.1) | `scenario_id, month` | `scenario_id, month, persons, persons_stopped, persons_excess, persons_top_up_total, units, units_stopped, units_excess, units_top_up_total` |

**Summary columns.**
- `persons` and `units` count the subjects (0 when none). `*_stopped` counts those with `stopped_on` set.
- `*_excess` sums the subjects' `excess` (`0.00` when none).
- `*_top_up_total` sums `top_up_total` (`0.00` when none; null without `top_up_amount`).

### 8.2 Alert events (B4 and A7, alert-only)

Dataset `alert_events` (central).
- **Key:** `day, event_type, scenario_id, subject_kind, subject_key, threshold`.
- **Columns:** `day, event_type, scenario_id` (`""` when none), `subject_kind` (`person` or `unit`), `subject_key` (`person_key` or `unit_id`), `value` (money), `threshold` (money; sorted by value), `trailing_sum` (money), `trailing_days` (count).

A person's **day cost** is Σ `person_day.cost_ref` of their resolved rows that day, over all providers.

- **`breaker`.** One event per person-day whose day cost > `alerts.daily_breaker`, whether or not the person is employed that day. `value` = day cost, `threshold` = the breaker.
- **`spike`.**
  - **Window.** The days d − `trailing_days` to d − 1 that are employed days of the person and lie inside the **build period** [`period_start`, `data_through`] (3.1).
  - **Fires** when all three hold: the window has at least `min_history_days` days; day cost > `floor`; and day cost × (days in window) > `multiplier` × (Σ day cost over the window). Days with no row count 0.
  - `value` = day cost, `threshold` = `floor`, `trailing_sum` and `trailing_days` = the window's sum and length.
- **`scenario_alert`.** One event per scenario, subject, month and fraction f of `alert_fractions`, on the first day d of the window with position(d) ≥ f × cap (or envelope). `value` = position(d), `threshold` = f × cap.
- `trailing_*` are null on breaker and scenario events.

## 9. Outputs, manifest and determinism

### 9.1 Folder

`--out DIR` must not exist or be empty (exit 2 otherwise). The build writes into `DIR.tmp` and renames it to `DIR` at the end. A failed build leaves neither.

### 9.2 Canonical CSV

- **File format.** UTF-8 without BOM. LF line ends. `csv.writer(lineterminator="\n", quoting=csv.QUOTE_MINIMAL)`. A header row of the column names.
- **Null** is an empty field. Text columns that silver allows to be `""` (`product`, `model`, `org_id` of identity-based seats) are never null in gold, so an empty field there means `""`.
- **Rows** are sorted by the dataset's key, ascending:
  - text by Unicode code point, with null first;
  - dates, counts, money and ratios by value (a `threshold` of `6.17` sorts before `12.34`);
  - booleans with `false` first.

  The sort is done by the SQL `ORDER BY`; the runner writes rows in the order fetched.
- **Value formatting** (by the runner, from the fetched Python values; DuckDB's own text conversion is never used):

| Type | Written as |
|---|---|
| bool | `true` / `false` |
| count | decimal digits |
| money | `meterline.amount_str`: no exponent, trailing zeros removed, at least two decimals (`0.27`, `9.872`, `0.00`) |
| quantity (credits) | `meterline.quantity_str` (`40`, `12.5`) |
| ratio | exactly 4 decimals (`0.5882`, `1.0000`) |
| date | `YYYY-MM-DD` |
| map | `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, money values as money strings, counts as integers |

### 9.3 Manifest (`manifest.json`)

`json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)` plus a final newline. It has no timestamp.

```
{"gold_schema_version": "1.0", "generator": {"name": "meterline-gold", "version": "1.0.0"},
 "as_of": "YYYY-MM-DD", "data_through": "YYYY-MM-DD", "months": ["YYYY-MM", ...],
 "key_check": "kc_...",
 "inputs": {"policy_sha256", "prices_sha256", "invoice_sha256" (or null),
            "org": [{"path", "sha256"}], "silver": [{"path", "sha256"}]},
 "suppression": {"cells": n, "k": n, "differencing": n, "secondary": n},
 "outputs": [{"path", "rows", "bytes", "sha256"}]}
```

- `policy_sha256` and `prices_sha256` hash `model.model_dump(mode="json")` of the validated object in canonical JSON (`sort_keys`, `separators=(",", ":")`). Input files hash their bytes.
- `rows` counts data rows, header excluded.
- Paths are POSIX, relative to the output folder or to each input root, and sorted. `outputs` excludes the manifest itself.
- The schema version is stamped in every dataset (`schema_version` column) and here (GD15). A gold folder of another version is never updated in place: the upgrade path is a rebuild from silver.

### 9.4 Determinism rules

1. **Same bytes.** The same inputs and key give the same bytes in every output file, on any platform, with any `threads` setting.
2. **No wall clock.** No `datetime.now`, `date.today`, `time.time`, `uuid`, `random` or `os.urandom` in the package.
3. **Order.** Inputs are read in sorted path order. Every dataset query ends in `ORDER BY` over its full key. No Python loop iterates over a `set` or an unsorted `dict`.
4. **No floats.** No `DOUBLE`/`FLOAT` column, `AVG`, `quantile_cont` or `/` outside `00_macros.sql` (analysis F3).
5. **Parquet.** With `--parquet`, a `.parquet` file is written beside each CSV. It is listed in the manifest and excluded from the byte check.

### 9.5 Assertions

Assertions A01-A08 are DuckDB queries in `99_assertions.sql`, listed in `run.ASSERTIONS` as (name, sql) pairs. Each returns zero rows when it holds and uses `IS DISTINCT FROM` and `coalesce(x, 0)` wherever a side can be null. A09 is a Python check in `write.py` over the written files.

Any failure stops the build (exit 1) and prints the assertion's name and the first offending row.

| Name | Holds when | Phase |
|---|---|---|
| A01 | Every dataset key is unique (null equal to null). | 1 |
| A02 | Per (provider, org, month), Σ `person_day` measures (tokens, requests, credits, `cost_source`) equal the Σ of the current silver usage records they came from, excluding `end_user`. | 1 |
| A03 | For every `person_month` row, `cost_ref` = Σ of that person's resolved `person_day.cost_ref` in the month. Persons with resolved rows but no employed day in the month have no `person_month` row; their rows still count in `costs_month` and `reconciliation_month`. | 1 |
| A04 | In the internal cell table, before suppression, each month's org cell `cost_ref` = Σ `person_month.cost_ref`. | 2 |
| A05 | Each month's cells are laminar (7.1). | 2 |
| A06 | No unsuppressed cell fails step 1, b1 or b2 of 7.4, and no published `active_persons` fails 7.2. | 2 |
| A07 | In every audience folder, every `groups_month` row equals the central row of the same (`month`, `cell_id`), except the nulls of 7.6.1. | 2 |
| A08 | `costs_month.total_cost_ref` = `usage_cost_ref` + `coalesce(seat_cost, 0)`. | 1 |
| A09 | No whole field of an identifier or key column (every text column except `data_quality.value`, `named_exceptions.threshold` and `named_exceptions.value`) in any output file other than `delivery/directory.csv` equals a `person_id`, `actor_id`, `actor_login` or `actor_email` of the inputs. Values that are also an `org_id` or a `unit_id` are exempt. | 1 |

## 10. CLI

```
meterline-gold build --silver DIR --org DIR --policy FILE --prices FILE --out DIR [--invoice FILE] [--parquet]
meterline-gold schemas --out DIR
meterline-gold import-litellm-prices --file FILE --keys FILE --as-of YYYY-MM-DD --out FILE
```

- `build` reads `METERLINE_GOLD_KEY` (3.6). On success it prints the output folder, the months, and the row count per central dataset.
- `schemas` writes `policy.schema.json` and `prices.schema.json`.
- **Exit codes:**
  - **0:** done.
  - **2:** bad arguments; an input, policy or price list that fails validation; a missing or short key; an output folder that is not empty.
  - **1:** an assertion or invariant failure during the build (9.5, 7.1, 5.1). Nothing is written.
- `cli.main(argv=None)` returns the exit code.

## 11. The example dashboard

`dashboard/index.html` is one static file, with inline CSS and JavaScript and no external script, font or request.

**Serving.** Serve **one audience folder** as the web root, with `index.html` copied into it, for example `python3 -m http.server` run inside `gold/exec/`. That folder can be `central/`, `exec/`, one `manager/<key>/` or one `person/<key>/`. Never serve the gold root or `delivery/`: serving the root would expose every audience to every viewer.

**What it does.**
- It detects the audience from the files it finds.
- It fetches only relative paths inside its own folder and parses the CSVs as text.
- It shows each file as a table, in the file's own row order.
- It offers no sort on any measure column and computes no figure: totals, shares and bands are shown only as read. Suppressed cells show as "suppressed (reason)".
- Charts are inline SVG bars of one column of one file, in file order.
- It has no authentication code. Protecting it is the deferred delivery recipe.

**Views by audience.**
- **central:** costs, seats and pools, data quality, groups, scenarios, alert events.
- **exec:** groups, costs, seats, pools, scenario summary.
- **manager:** their cells and named exceptions.
- **person:** their months, their days and their comparison band.

## 12. Out of scope

Deferred by the owner:
- "Deferred: gold engagement levels (A4)"
- "Deferred: gold FOCUS export (A9)"
- "Deferred: gold recommendations engine (C)"
- "Deferred: gold enforcement mode for breaker and alerts"
- "Deferred: SCIM 2.0 input adapter for people and units"
- "Deferred: delivery recipes and sender plugins"
- "Deferred: gold session telemetry and value metrics"
- "Deferred: pre-publication checks"

Also not in v1, with no row:
- differential privacy;
- a second, cross-cutting additive hierarchy (card 2);
- grid cells from matrix memberships;
- splitting a person's month between units;
- Copilot's monthly model mix per person;
- activity `counters`;
- carrying a month's suppression state from one build to the next, and the deliver-once rule (both belong to the deferred delivery recipe; card 1);
- network access during a build;
- dashboard authentication.

**Fixed in v1, not parameters** [A]:
- **Calendar and arithmetic.** Calendar months with UTC days; nearest-rank quantiles at 0.50, 0.75, 0.90 and 0.99; the top decile at 10%; comparison cuts at P50, P75 and P90 with the band names of 7.5; linear projection; strict `>` for stops and breakers, `≥` for alert fractions; money with at most 12 decimals.
- **Hashing.** 16 hex digits with the `pk_`, `ak_` and `kc_` prefixes; a 32-byte minimum key.
- **Provider-shaped rules.** These follow silver as the connectors write it (analysis F4). `pools_month` reads `extras.discount_quantity` and `extras.net_quantity` and the `billed` basis. `month_credit_mismatches` reads `ai_credit`. Credits are the `ai_credit` quantity.

## 13. Phases

| Phase | Builds | Usable alone as | Done-checks |
|---|---|---|---|
| 1 | Package, parameter files and schemas, importer, loading and hashing, `people`, `units`, `identity_map`, `person_day`, `person_day_activity`, `person_month`, `seats_month`, `pools_month`, `costs_month`, `reconciliation_month`, `data_quality`, the `central/` and `delivery/` folders, manifest, assertions A01-A03, A08, A09, CLI | Central pseudonymous datasets, costs and data quality | G01-G05, G08, G09, G10 (except c), G12, G13, G14, G16, G17, G18, G19, G20, G21, G23 |
| 2 | Grid, privacy algorithm, comparison groups, the `manager/`, `person/` and `exec/` folders, assertions A04-A07, the example dashboard | Per-audience datasets | G06 (except its layout list), G11, G15, G22, plus every phase 1 check rerun |
| 3 | Scenarios, alert events, named exceptions, and the remaining files of the layout | Shadow scenarios and alerts | G06 layout list, G07, G10 (c), plus every earlier check rerun |

**How phases add files.**
- A phase writes only the datasets it builds. A later phase's files are absent, not empty: the manager folders' `named_exceptions.csv` appears in phase 3.
- G08, G12 and G13 cover every file present in their phase.
- Each phase ends with all of its checks passing.

## 14. Done-checks

**How the checks run.**
- Each check is a pytest test named `test_done_check_G<NN>_*` in `packages/meterline-gold/tests/`.
- Each runs on synthetic data only: the fixture of `docs/gold-fixture/`, or simulator output.
- Each fails before the build, because `meterline_gold` does not exist (`ModuleNotFoundError`).
- **Negative controls.**
  - Every byte comparison has one: the same comparison against a copy of the expected file with one value changed must fail.
  - Every scan has one: a copy with one planted violation must be flagged.
  - G23 gives each assertion a broken input that must fire it.
- "Fixture build" means `build(silver="docs/gold-fixture/input/silver", org="docs/gold-fixture/input/org", policy="docs/gold-fixture/input/policy.yaml", prices="docs/gold-fixture/input/prices.yaml", invoice="docs/gold-fixture/input/invoice.csv", key=b"meterline-gold-fixture-key-not-a-secret", out=tmp)`.
- "Tiny sim" and "vertical sim" mean `meterline_sim.generate(preset=..., seed=7, months=6)` built with `examples/policy.example.yaml`, `examples/prices.example.yaml` and the test key.

**Interfaces the checks use** (section 2):
- `run.ASSERTIONS`;
- `run.run_privacy(con, k, *, _disable_block_rule=False)`, which runs 7.4 on the DuckDB tables `cells(month, cell_id, parent_cell_id, persons_list, receivers_list)`;
- `write.DATASET_KEYS`, a dict from dataset name to its key columns, equal to this spec's keys.

**The checks.**

- **G01 Fixture `person_day`.** `central/person_day.csv` is byte-identical to `docs/gold-fixture/expected/central/person_day.csv`.
- **G02 Fixture `person_month` and `person_day_activity`.** Both are byte-identical to their expected files.
- **G03 Fixture seats, pools and costs.** `seats_month`, `pools_month` and `costs_month`, in `central/` and in `exec/`, are byte-identical to the expected files.
- **G04 Fixture reconciliation and data quality.** `reconciliation_month` and `data_quality` are byte-identical.
- **G05 Fixture running month.** Use a copy of the fixture with every silver record whose `period_start` is after 2026-05-15 removed.
  - 2026-05 is `running`.
  - `projected_cost_ref` is `1.84` for p03 and `13.43` for p18 (Appendix A.7).
  - Negative control: the same assertion against `1.85` for p03 fails.
- **G06 Fixture privacy and audiences.**
  - `groups_month` (central, exec, and each manager folder) and `central/comparison_groups.csv` are byte-identical to the expected files.
  - The sorted list of output paths equals `expected/layout.txt`.
  - Each person's `comparison.csv` band equals `expected/person_comparison.csv`.
- **G07 Fixture scenarios, alerts and named exceptions.** `scenario_persons`, `scenario_units`, `scenario_summary` (central and exec), `alert_events` and `named_exceptions` (central, and each manager's file) are byte-identical to the expected files.
- **G08 Determinism.**
  - Two fixture builds and two tiny-sim builds give byte-identical folders, Parquet aside.
  - The second build of each pair runs in a subprocess, through `cli.main`, with another `PYTHONHASHSEED` and `threads=1`; the first uses `threads=4`.
  - Negative control: a build with another key differs.
- **G09 Silver reconciles (tiny and vertical sim).** Group the current silver usage records (day grain, actor kind not `end_user`) by (provider, `cost_basis`, month). Their Σ `cost`, Σ token fields, Σ `requests` and Σ `ai_credit` equal the same groups of `central/person_day.csv`.
- **G10 Ground truth (tiny and vertical sim).**
  - **a.** The ground truth's `totals` rows of kind `usage`, grain `day` equal the `person_day` groups of G09. Money is compared after rounding both sides to the cent (`ROUND_HALF_EVEN`).
  - **b.** Every Anthropic `person_day` row dated before the price change has `cost_ref` within `0.00000002 × source_records` of `cost_source`. On and after the change, the changed model's rows differ. Precondition, asserted first: the example prices equal the simulator's prices before the change.
  - **c.** The runaway target (ground-truth event `runaway_agent`) has a `spike` event on the first runaway day. Preconditions, asserted first: that day's day cost is > the example floor, and the target has at least `min_history_days` employed days before it.
  - **d.** In the tiny sim, `data_quality` has `final_rows_revised` > 0 for `github_copilot` in the corrected month.
  - **e.** Every joiner's first `person_month` has `employed_days` = the days from their start to the window's end.
  - **f.** No `person_month` exists for a leaver after their last month.
- **G11 Privacy property test.**
  - **Settings.** Hypothesis, `@settings(max_examples=300, deadline=None, derandomize=True)`, with the fixture's tree, persons and costs added as an `@example`.
  - **Generated data**, one month:
    - either a unit tree of depth ≤ 3, with 1-4 children per unit and 0-6 direct members per unit (heads included), or an attribute chain of 1-2 attributes with 1-4 values each (`kind: attributes`, no receivers);
    - at most 25 persons in all, so that assertion b always runs;
    - `k` in 2..6;
    - a random integer cost per person;
    - a random 0/1 active flag per person.
  - **The run.** `run.run_privacy` runs on the generated cells. Then the attacker builds the exact rational linear system over one unknown per person:
    - one equation per published cell and per published measure (`cost_ref`, `active_days`, `active_persons`);
    - for each published cell P in turn, the attacker sees the cells P's receivers get (7.6) and substitutes the values of P's receivers only, as known constants. An attacker without receivers (exec, central) sees every published cell and substitutes nothing.
  - **Assertions.**
    - **a.** No suppressed cell's non-empty uncovered part has its indicator vector in the row space.
    - **b.** For a universe of at most 25 unknown persons, an exact search finds no 0/1 vector with 1 to `k` − 1 ones in the row space.
    - **c. Coverage**, recorded with Hypothesis `event`s: at least half of the examples have a suppressed cell under a published ancestor and at least 3 published cells.
  - **Negative control:** with `_disable_block_rule=True`, the cells of one unit with a head and two published children (6 and 7 persons) are all published, and the attacker recovers the head's value as parent minus children.
- **G12 No identifying data.** In a fixture build and a tiny-sim build, A09 holds. In addition, no file other than `delivery/directory.csv` contains any email address, or any `given_name` or `family_name` of `directory.csv` as a whole word (case-sensitive).
- **G13 Sorted by key, never by a measure.** For every dataset present in the phase, in a fixture build and a tiny-sim build:
  - `write.DATASET_KEYS` holds the key this spec states for the dataset;
  - the rows are strictly ascending in the typed order of 9.2 over that key.

  The check fails if it finds no dataset. Negative control: a copy of `person_month.csv` sorted by `cost_ref` fails.
- **G14 Schema version and manifest.**
  - Every dataset's first column is `schema_version`, with value `1.0` on every row.
  - The manifest lists every output with the right `bytes`, `sha256` and `rows` (header excluded).
  - `key_check` equals the fixture value of Appendix A.1.
- **G15 Audience isolation.**
  - Each `person/<key>/` file holds only rows with that `person_key`.
  - Each `manager/<key>/groups_month.csv` holds only cells of units that manager heads, or units below them, and no org cell.
  - `exec/` follows 7.6.1.
  - Suppressed rows have null figures in every folder.
  - A07 holds.
- **G16 Parameter files.**
  - `schemas/*.json` equal what `params.py` generates with the installed pydantic.
  - The example policy and price list validate.
  - Each of the following exits 2 through `cli.main`:
    - an unknown key;
    - an unquoted number for an amount;
    - `k: 1`;
    - an unknown `compare_by` attribute;
    - a `month_cost_ref_above` rule for audience `manager`;
    - currencies that differ between the policy and the price list;
    - an invoice row in another currency;
    - an `identities.csv` with two persons on one account and overlapping dates;
    - two LiteLLM keys that give the same model;
    - a missing key, or a 31-byte key;
    - a non-empty output folder.
- **G17 The example policy is visibly synthetic.**
  - Every money amount covered by the 4.4 rule, parsed as `Decimal`, has exactly two decimals and a non-zero last digit.
  - `currency` is `USD`.
  - The label says the values are invented.

  Negative control: a copy with a cap of `"50.00"` is flagged.
- **G18 LiteLLM importer.**
  - `import-litellm-prices` on `docs/gold-fixture/litellm/sample.json`, with `keys.txt` and `--as-of 2026-10-01`, writes a file byte-identical to `docs/gold-fixture/litellm/expected-prices.yaml` (Appendix A.10).
  - A key missing from the file exits 2.
  - The importer module imports none of `urllib`, `http`, `socket` or `requests`.
- **G19 CLI.**
  - `build` on the fixture returns 0.
  - The same call again returns 2, because the output folder is not empty.
  - Without `METERLINE_GOLD_KEY`, it returns 2.
  - With `run.ASSERTIONS` extended by a query that always returns a row, it returns 1 and leaves no output folder.
- **G20 Scans.**
  - No name of 9.4 rule 2 appears in `src/meterline_gold/`.
  - In `sql/*.sql`, outside `00_macros.sql`, comments and string literals, there is no `/`.
  - No `DOUBLE`, `FLOAT`, `AVG(`, `quantile_cont` or `median(` appears in `sql/`.
  - Every dataset named in `write.DATASET_KEYS` is produced by a query that ends with `ORDER BY`, and at least one such query is found per dataset.
- **G21 Speed.** A vertical-sim build of 6 months finishes in under 60 seconds on the developer machine. The test prints the time, is marked `timing`, and is run in the done gate but not in CI.
- **G22 Privacy worked example.** The cells of 7.4's worked example, built directly, go through `run.run_privacy`, which gives exactly the states and reasons stated there for both months.
- **G23 Assertions fire.** For each of A01-A09, a test corrupts one table or file of a fixture build so that the assertion must fail, and checks that it reports its own name.

## 15. Decisions taken at acceptance (cards)

The owner took all eight on 2026-10-12, as recommended. They are recorded here, and the sections above already follow them.

1. **Month-over-month differencing (GD8 as written) — decided: dropped.**
   - **Cost.** One joiner can suppress a whole month and its neighbours, up to the org cell (7.4 example).
   - **Inconsistent builds.** A month's state depends on the months next to it. Two builds of different dates can therefore publish different cells for the same month, and their union can leak. Example: in build N, May's A is suppressed and R, C are published; in build N+1, June suppresses May's R and C, and May's A becomes published. The two builds together give R − C − A.
   - A linear attacker gains nothing from the cross-month comparison itself (analysis).
   - **Options.**
     - (a) Keep it as written, and publish a month to non-central audiences only once (a delivery rule, deferred).
     - (b) Drop it, so each month's state depends on that month only.
     - (c) Keep it and add a `--previous GOLD_DIR` input that freezes earlier published states.
   - **Decided 2026-10-12: (b), plus a deliver-once rule in the deferred delivery recipe.** (b) removes the cost and the cross-month instability at no linear cost. It does not close the correction case: a retroactive change to `people.csv` or silver makes the same cell differ between two builds by one person's figure. Only (c), or delivering each complete month once, closes that.
2. **One tree for additive figures.**
   - Two cross-cutting additive hierarchies (unit and function, both with costs) can reveal a person through margins: the central IT example in the analysis. Exact protection needs an audit that is neither simple nor SQL.
   - Decided: one tree in v1. A row for a second hierarchy with an exact audit is filed only if an executive view by function is wanted.
3. **Comparison groups.**
   - Persons see only their own cost and their band. Comparison cells publish only their size and cut points, to central, and stay outside the tree and the differencing rule.
   - Decided: accepted.
4. **The subset rule.**
   - With `k` = 5, P50 needs 10 persons, P75 20, P90 50 and P99 500.
   - The top-decile share is published for the org cell only, since across nested cells two shares can isolate one person (analysis).
   - `active_persons` needs at least `k` active and at least `k` inactive persons.
   - Small units therefore show few distribution figures. Decided: accepted.
5. **One unit per person per month.** A person who moves mid-month counts entirely toward the unit of their last employed day (5.4). Decided: accepted for v1.
6. **Seat holders without a seat list.** For a provider without per-person seat records (Anthropic), the holders come from `identities.csv`. Decided: accepted.
7. **Exec filtering (7.6.1).**
   - Provider-level usage and pool figures are hidden from exec when fewer than `k` people use that provider.
   - Seat activity counts and the stopped count follow the subset rule.
   - Scenario excess amounts are central only.
   - Decided: accepted.
8. **Cost-valued named exceptions go to central only** (7.7). A head who receives one person's cost could subtract it from a block. Managers get count-valued rules only. Decided: accepted.

## Appendix A. The hand-built fixture

The fixture lives in `docs/gold-fixture/`:
- `input/`: silver, organisation files, `policy.yaml`, `prices.yaml`, `invoice.csv`;
- `expected/`: the expected outputs, at the paths the build writes;
- `litellm/`: the importer's sample;
- `README.md`.

Everything is invented, and every email ends in `@example.com`. The fixture has a root unit and two working units, one month, all three providers, one unresolved actor, two non-person actors, one joiner, one unknown model and one unit below `k`.

The expected outputs were computed by hand from the tables below. The 24 keyed hashes of A.1 are the only values computed by code, with Python's standard `hmac`; they are not gold logic. During the review, an independent rebuild from `input/` and the spec's rules matched every expected file byte for byte. G01-G07 re-establish that on every build.

### A.1 Organisation, identities and keys

One month, 2026-05 (31 days). Units: `u01` Company (root, function `executive`, head p01), `u02` Alpha (team, `sales`, head p02), `u03` Beta (team, `engineering`, head p17).

| Person | Unit | Level | Function | Manager | Notes | `person_key` |
|---|---|---|---|---|---|---|
| p01 | u01 | L1 | executive | — | | `pk_c34d136a96085b79` |
| p02 | u02 | L4 | sales | p01 | | `pk_6c98dbb3f11e256b` |
| p03 | u02 | L5 | sales | p02 | | `pk_ddbd6ef63cbb380d` |
| p04 | u02 | L5 | sales | p02 | | `pk_218a96726a169e60` |
| p05 | u02 | L5 | sales | p02 | Copilot seat | `pk_d635f3102d80d6c5` |
| p06 | u02 | L5 | sales | p02 | LiteLLM user (`oidc_claim`) | `pk_b8d788be7648bb13` |
| p07 | u02 | L5 | sales | p02 | | `pk_8038bf136df4350d` |
| p08 | u02 | L5 | sales | p02 | | `pk_355a3e8974491b6b` |
| p09 | u02 | L5 | sales | p02 | uses an unknown model | `pk_745d1f9a98e70c61` |
| p10 | u02 | L5 | sales | p02 | | `pk_1dc6250768ae69f2` |
| p11 | u02 | L5 | sales | p02 | no use | `pk_d71f30b29cd2aec0` |
| p12 | u02 | L5 | sales | p02 | activity only | `pk_5339008b9a085e94` |
| p13 | u02 | L5 | sales | p02 | no use | `pk_e917ad4b1f8007c9` |
| p14 | u02 | L5 | sales | p02 | no use | `pk_aa23e50c76c17e29` |
| p15 | u02 | L5 | sales | p02 | no use | `pk_1a0631e29b611e26` |
| p16 | u02 | L5 | sales | p02 | contractor, no use | `pk_67259c423d1ca51a` |
| p17 | u03 | L4 | engineering | p01 | Copilot seat | `pk_ba750aaeaf88d602` |
| p18 | u03 | L5 | engineering | p17 | Copilot seat; runaway day | `pk_6e534a982f382221` |
| p19 | u03 | L5 | engineering | p17 | Copilot seat | `pk_db7e50de9a66fb87` |
| p20 | u03 | L5 | engineering | p17 | Copilot seat, never used | `pk_40d7d4acb9ffafbb` |
| p21 | u02 | L5 | sales | p02 | **joiner** from 2026-05-18 | `pk_36ea9c039e2ad682` |

**Non-person and unresolved actors.**

| Actor | Case | `actor_key` |
|---|---|---|
| Anthropic `user_f99` | **unresolved** (in no identity row) | `ak_115c773940c44688` |
| LiteLLM `proxy_user` `bot-ci` | **non_person** by an identity row | `ak_b97081e445e8c626` |
| LiteLLM `unassigned` | **non_person** by actor kind | `ak_8f1a3956589b5a8c` |

Key check: `kc_79afd616f30de074`.

**Pulls.**

| Provider | `pulled_at` | Effect |
|---|---|---|
| Anthropic | 2026-06-10 06:00 UTC | All final. |
| Copilot | 2026-06-03 06:10 UTC | The day record of 05-30 is not final; the month and billing records are not final. |
| LiteLLM | 2026-06-10 06:20 UTC | All final. |

Copilot seat lists were pulled on 05-01 and 05-31.
- `data_through` = 2026-05-31 (the seat snapshots of that day).
- `as_of` = 2026-06-10.
- May is `complete`, and the build period is 05-01..05-31.

### A.2 Prices and token shapes

The fixture price list puts every model under provider `"*"`. Prices are invented: each is a round base value times 1.1, so that no value is any vendor's list price. Per million tokens (U = uncached input, R = cache read, W5 / W1 = 5-minute / 1-hour cache write, O = output):

| Model | Tier | U | R | W5 | W1 | O |
|---|---|---|---|---|---|---|
| `model-x-light` | light | 1.10 | 0.11 | 1.375 | 2.20 | 5.50 |
| `model-x-premium` | premium | 11.00 | 1.10 | 13.75 | 22.00 | 55.00 |
| `model-y-standard` | standard | 2.20 | 0.22 | 2.75 | 2.75 | 8.80 |
| `model-z-std` | standard | no token price | | | | |

Other entries:
- unit price `github_copilot` `ai_credit` = 0.01;
- `included`: `github_copilot` `copilot_cli` and `copilot_app`;
- seats: `anthropic` 21.50 per month, with no pool; `github_copilot` 13.75 per month, with 73 included credits;
- `model-x-experimental` is **not in the catalogue**.

| Shape | Model | Req. | U | R | W5 | W1 | O | input_total | `cost_ref` (per bucket) | Silver `cost` |
|---|---|---|---|---|---|---|---|---|---|---|
| L1 | light | 10 | 100,000 | 200,000 | 40,000 | 0 | 20,000 | 340,000 | 0.110 + 0.022 + 0.055 + 0 + 0.110 = **0.297** | 0.27 |
| L2 | light | 20 | 200,000 | 400,000 | 80,000 | 0 | 40,000 | 680,000 | 0.220 + 0.044 + 0.110 + 0 + 0.220 = **0.594** | 0.54 |
| P1 | premium | 10 | 100,000 | 200,000 | 40,000 | 0 | 20,000 | 340,000 | 1.10 + 0.22 + 0.55 + 0 + 1.10 = **2.97** | 2.65 |
| PC | premium | 200 | 500,000 | 8,000,000 | 400,000 | 100,000 | 200,000 | 9,000,000 | 5.50 + 8.80 + 5.50 + 2.20 + 11.00 = **33.00** | 29.40 |
| C1 | light | 120 | 175,000 | 3,000,000 | 100,000 | 50,000 | 60,000 | 3,325,000 | 0.1925 + 0.330 + 0.1375 + 0.110 + 0.330 = **1.10** | 1.00 |
| C20 | light | 2,400 | 3,500,000 | 60,000,000 | 2,000,000 | 1,000,000 | 1,200,000 | 66,500,000 | 3.85 + 6.60 + 2.75 + 2.20 + 6.60 = **22.00** | 20.00 |
| X | experimental | 5 | 50,000 | 0 | 0 | 0 | 10,000 | 50,000 | null (`unknown_model`) | 0.12 |
| Y1 | y-standard | 30 | 300,000 | 100,000 | 0 (write) | | 50,000 | 400,000 | 0.66 + 0.022 + 0 + 0.44 = **1.122** | 1.00 |
| Y2 | y-standard | 100 | 1,000,000 | 500,000 | 100,000 (write) | | 200,000 | 1,600,000 | 2.20 + 0.11 + 0.275 + 1.76 = **4.345** | 3.95 |
| Y3 | y-standard | 50 | 500,000 | 0 | 0 (write) | | 100,000 | 500,000 | 1.10 + 0 + 0 + 0.88 = **1.98** | 1.80 |

LiteLLM shapes have no 5m/1h split, so their cache write is priced at W5 (6.2.1).

### A.3 `person_day` (32 rows)

| Day | Provider | Subject | Product | Shape or credits | `cost_ref` | Final |
|---|---|---|---|---|---|---|
| 05-04 | anthropic | p04, p01 | chat | L1 each | 0.297 each | yes |
| 05-05 | anthropic | p03 | chat | L1 | 0.297 | yes |
| 05-05 | github_copilot | p17 | copilot | 40 credits × 0.01 | 0.40 | yes |
| 05-06 | anthropic | p04 | claude_code | PC | 33.00 | yes |
| 05-06 | anthropic | p02 | chat | P1 | 2.97 | yes |
| 05-07 | github_copilot | p19 | copilot | 80 credits | 0.80 | yes |
| 05-11 | anthropic | p18 | claude_code | C1 | 1.10 | yes |
| 05-11 | anthropic | p05 | chat | L1 | 0.297 | yes |
| 05-12 | anthropic | p18, p17 | claude_code | C1 each | 1.10 each | yes |
| 05-12 | anthropic | p03 | chat | L2 | 0.594 | yes |
| 05-12 | github_copilot | p18 | copilot | 100 credits | 1.00 | yes |
| 05-13 | anthropic | p18 | claude_code | C1 | 1.10 | yes |
| 05-13 | anthropic | p06 | chat | L2 | 0.594 | yes |
| 05-13 | litellm | p06 | `""` | Y1 | 1.122 | yes |
| 05-14 | anthropic | p08, p07 | chat | L1 each | 0.297 each | yes |
| 05-14 | anthropic | p18 | claude_code | C1 | 1.10 | yes |
| 05-15 | anthropic | p18 | claude_code | C1 | 1.10 | yes |
| 05-18 | anthropic | p18 | claude_code | C20 | 22.00 | yes |
| 05-19 | anthropic | p21, p03 | chat | L1 each | 0.297 each | yes |
| 05-20 | anthropic | p09 | chat | X | null (`unknown_model`) | yes |
| 05-20 | github_copilot | p17 | copilot | 60 credits | 0.60 | yes |
| 05-20 | litellm | unassigned, bot-ci | `""` | Y3, Y2 | 1.98, 4.345 | yes |
| 05-21 | anthropic | p01 | chat | L1 | 0.297 | yes |
| 05-25 | anthropic | p10 | chat | L2 | 0.594 | yes |
| 05-26 | github_copilot | p18 | copilot | 150 credits | 1.50 | yes |
| 05-27 | anthropic | user_f99 (unresolved) | chat | L1 | 0.297 | yes |
| 05-30 | github_copilot | p05 | copilot | 50 credits | 0.50 | **no** |

The full rows, in key order, are in `expected/central/person_day.csv`. Copilot `model` is `""`, and its `model_tier` is null.

**`person_day_activity` (4 rows).** Each row is one silver activity record, resolved:

| Day | Provider | Person | Product | Counters |
|---|---|---|---|---|
| 05-04 | anthropic | p01 | chat | `messages` 10; the other core counters 0; `requests` and suggestions null |
| 05-20 | anthropic | p09 | chat | `messages` 5; the same pattern |
| 05-20 | github_copilot | p17 | code_completion | `suggestions_shown` 40, `suggestions_accepted` 10, `lines_added` 25, `lines_removed` 0, `messages` 0; `sessions`, `requests`, edits, commits and pull requests null |
| 05-22 | anthropic | p12 | chat | `messages` 3; the same pattern as 05-04 |

### A.4 `person_month` (21 rows, all `complete`, attribution day 05-31)

Fixture bands: none from 0, light from 0.01, regular from 1.23, heavy from 12.34. Premium tiers: `[premium]`.

| Person | Employed | Active days | `cost_ref` = sum | Premium share | Cache rate | Band |
|---|---|---|---|---|---|---|
| p01 | 31 | 2 (05-04, 05-21) | 0.297 + 0.297 = **0.594** | 0 / 0.594 = 0.0000 | 400,000 / 680,000 = 0.5882 | light |
| p02 | 31 | 1 | **2.97** | 2.97 / 2.97 = 1.0000 | 0.5882 | regular |
| p03 | 31 | 3 | 0.297 + 0.594 + 0.297 = **1.188** | 0.0000 | 800,000 / 1,360,000 = 0.5882 | light |
| p04 | 31 | 2 | 0.297 + 33.00 = **33.297** | 33.00 / 33.297 = 0.99108... → 0.9911 | 8,200,000 / 9,340,000 = 0.87794... → 0.8779 | heavy |
| p05 | 31 | 2 (05-11, 05-30) | 0.297 + 0.50 = **0.797** | 0.0000 | 0.5882 | light, **not final** |
| p06 | 31 | 1 (05-13, two tools) | 0.594 + 1.122 = **1.716** | 0.0000 | 500,000 / 1,080,000 = 0.46296... → 0.4630 | regular |
| p07, p08, p21 | 31 (p21: **14**, 05-18..31) | 1 | **0.297** | 0.0000 | 0.5882 | light |
| p09 | 31 | 1 (05-20) | **0.00** (X's `cost_ref` is null; `rows_unknown_model` 1) | null (`cost_ref` 0) | 0 / 50,000 = 0.0000 | none |
| p10 | 31 | 1 | **0.594** | 0.0000 | 0.5882 | light |
| p11, p13, p14, p15, p16, p20 | 31 | 0 | **0.00** | null | null | none |
| p12 | 31 | 1 (05-22, activity only) | **0.00** | null | null | none |
| p17 | 31 | 3 (05-05, 05-12, 05-20) | 1.10 + 0.40 + 0.60 = **2.10** | 0.0000 | 3,000,000 / 3,325,000 = 0.90225... → 0.9023 | regular |
| p18 | 31 | 7 (05-11..15, 05-18, 05-26) | 5 × 1.10 + 22.00 + 1.00 + 1.50 = **30.00** | 0.0000 | 75,000,000 / 83,125,000 = 0.9023 | heavy |
| p19 | 31 | 1 | **0.80** | 0.0000 | null (no tokens) | light |

**Totals.**
- Persons: 0.594 + 2.97 + 1.188 + 33.297 + 0.797 + 1.716 + 3 × 0.297 + 0.594 + 2.10 + 30.00 + 0.80 = **74.947**.
- Check by provider: Anthropic persons 69.025 (9 × 0.297 + 3 × 0.594 + 2.97 + 33.00 + 6 × 1.10 + 22.00) + Copilot 4.80 + LiteLLM (p06) 1.122 = 74.947.

### A.5 Seats, pools and costs

**Anthropic seats** (from identities).
- All 21 persons hold a seat in May: `seats_billed` 21, `seats_on_eval_day` 21.
- Active: p01-p10, p12, p17, p18, p21 = **14**.
- Idle on 05-31 (`idle_after_days` 14, window 05-18..05-31): 21 − 7 = **14**. The 7 active in that window are p01, p03, p09, p10, p12, p18 and p21.
- Seat cost: 21 × 21.50 = **451.50**.

**Copilot seats** (from assignments).
- Holders: p05 and p17-p20, so 5.
- Active: 4 (all but p20).
- Idle on 05-31: p19 (last used 05-07) and p20, so **2**.
- Seat cost: 5 × 13.75 = **68.75**.

**Copilot pool.** 5 × 73 = **365** credits. Walking the billed days in order:

| Day | Gross | Discount | Net | Cumulative overage |
|---|---|---|---|---|
| 05-05 | 40 | 40 | 0 | 0.00 |
| 05-07 | 80 | 80 | 0 | 0.00 |
| 05-12 | 100 | 100 | 0 | 0.00 |
| 05-20 | 60 | 60 | 0 | 0.00 |
| 05-26 | 150 | 85 | 65 | 0.65 |
| 05-30 | 50 | 0 | 50 | 1.15 |

- Gross is 480, pool used 365, and overage 115 credits = **1.15**. The pool used share is 1.0000.
- Budget 0.98: 0.65 is still under it on 05-26, and 1.15 is over it, so the budget is **crossed on 05-30**.

**Costs.**

| Provider | `usage_cost_source` | `usage_cost_ref` | `seat_cost` | Total |
|---|---|---|---|---|
| anthropic | 62.49 | 69.322 (69.025 + unresolved 0.297) | 451.50 | 520.822 |
| github_copilot | 4.80 | 4.80 | 68.75 | 73.55 |
| litellm | 6.75 | 1.122 + 4.345 + 1.98 = 7.447 | null | 7.447 |

Anthropic `cost_source` = 10 × 0.27 + 3 × 0.54 + 2.65 + 29.40 + 6 × 1.00 + 20.00 + 0.12 = **62.49**.

**Exec form (7.6.1, k = 5).**
- Anthropic has 13 users, so its costs are published. Copilot has 4 users (p05, p17, p18, p19) and LiteLLM has 1 (p06), so their usage figures are hidden in `exec/`. The seat costs stay.
- The hidden rows' users together are {p05, p06, p17, p18, p19}: 5 ≥ 5, so Anthropic stays published.
- Copilot's pool usage figures are null in `exec/` (4 users). `seats_billed`, `included_per_seat`, `pool_credits` and `budget_amount` stay.
- Seats:
  - Anthropic: active 14 / inactive 7 and idle 14 / non-idle 7 are all ≥ 5, so both counts are published.
  - Copilot: active 4 < 5 and idle 2 < 5, so both are null.

### A.6 Reconciliation and data quality

**Anthropic.**
- Org totals are 62.99: the actors' 62.49 plus 0.50 on 05-12 that no actor accounts for. Unallocated = **0.50**; share 0.50 / 62.99 = 0.00793... → 0.0079.
- Gap 69.322 − 62.49 = **6.832**; share 6.832 / 62.49 = 0.10932... → 0.1093.
- Invoice: usage 63.50 − 62.99 = **0.51**; seats 451.50 − 451.50 = 0.00.

**Copilot.**
- The org basis `billed` differs from the actor basis `consumption`, so the unallocated cost is null.
- Credits: 480 − 480 = 0.
- Gap 0.00.
- Invoice: usage 1.15 − 1.15 = 0.00; seats 0.00.

**LiteLLM.**
- Org total 1.00 + 5.75 = 6.75 = the actors' total, so unallocated = 0.00.
- Gap 7.447 − 6.75 = 0.697; share 0.697 / 6.75 = 0.10325... → 0.1033.

**Data quality (May).**
- **Anthropic.** 23 rows: 1 unresolved (1 actor, 0.297), 1 unknown-model row (1 model). Unallocated share 0.297 / 69.322 = 0.00428... → 0.0043. Non-final 0.
- **Copilot.** 6 rows: 1 non-final, so non-final share 0.50 / 4.80 = 0.10416... → 0.1042. Month credit mismatches 0 (100 = 40 + 60; 250 = 100 + 150; 80; 50).
- **LiteLLM.** 3 rows: 2 non-person (4.345 + 1.98 = 6.325). Unallocated share 6.325 / 7.447 = 0.84933... → 0.8493.
- **Freshness** (global `as_of` 06-10):

  | Provider | Latest day | Lag |
  |---|---|---|
  | anthropic | 05-31 | 10 |
  | github_copilot | 05-31 | 10 |
  | litellm | 05-20 | 21 |

### A.7 Running month (G05)

Records with `period_start` after 05-15 are removed, so `data_through` = 05-15 and `days_elapsed` = 15.
- p03: 0.297 + 0.594 = 0.891, so 0.891 × 31 / 15 = 27.621 / 15 = 1.8414 → **1.84**.
- p18: 5 × 1.10 + 1.00 (Copilot 05-12) = 6.50, so 6.50 × 31 / 15 = 201.5 / 15 = 13.433... → **13.43**.

### A.8 Suppressed group table (`groups_month`, k = 5)

**Cells.**

| Cell | Persons | Who |
|---|---|---|
| org | 21 | everyone |
| u01 | 21 | everyone (the root unit); receivers {p01} |
| u02 Alpha | 16 | p02-p16, p21; receivers {p02, p01} |
| u03 Beta | 4 | p17-p20; receivers {p17, p01} |

**Steps.**
1. **Primary.** u03 has 4 < 5 persons, so it is suppressed, reason `k`.
2. **Iteration 1.**
   - a. org vs u01 gives s = 0; org vs u02 and u01 vs u02 give s = 5 ({p01, p17-p20}), which is not < 5. No suppression.
   - b1. u01 has exactly one suppressed child (u03), so its other child u02 is suppressed, reason `secondary`.
   - b2. u01's block is {p01} ∪ u03, 5 persons. Without the receiver p01 that leaves 4 < 5, so u01 fails as well, with the same action. This b2 failure is the leak that lens 4 of the review demonstrated: p01 would have subtracted their own cost and recovered Beta's.
3. **Iteration 2.**
   - b1. u01 has two suppressed children, so it does not fire.
   - b2. u01's block is {p01} ∪ u02 ∪ u03: 21 persons in three pieces, 20 unknown. The org cell's block is empty. Nothing changes.
4. **Published:** org and u01.

**Figures** (sorted values; rank r = `ceil(q·n)`).

| Cell | `cost_ref` | P50 | P75 | P90, P99, top decile |
|---|---|---|---|---|
| org, u01 (n = 21) | **74.947** | r = 11 → **0.297**: 8 zeros (p09, p11-p16, p20), then 0.297 × 3 at ranks 9-11 | r = 16 → **1.188**: ranks 12-13 are 0.594, 14 is 0.797, 15 is 0.80, 16 is p03's 1.188; 5 above, ≥ 5 | null: P90 r = 19 leaves 2 above; P99 r = 21 leaves 0; top decile m = 3 < 5 |
| u02, u03 | suppressed | | | |

**Activity.**
- `active_persons`: 15 active and 6 inactive in the org cell and in u01. The remainders are as follows, all ≥ 5, so **15** is published:
  - u01's remainder is all 21 persons, because its children are suppressed: 15 / 6 active / inactive, and 14 / 6 without p01;
  - the org cell's remainder is empty, because u01's value is published. The active persons are p01-p10, p12, p17, p18, p19 and p21.
- `active_days` = 2 + 1 + 3 + 2 + 2 + 1 + 1 + 1 + 1 + 1 + 1 + 3 + 7 + 1 + 1 = **28**.

**`is_final`.** False, because p05 is not final.

**Audience copies.**
- `exec/` (`max_depth` 1): org and u01.
- `manager/` for p01: u01, u02 and u03.
- `manager/` for p02: u02, suppressed.
- `manager/` for p17: u03, suppressed.

**Comparison groups.**
- sales/L5 (n = 15): P50 r = 8 → 0.297. Ranks 1-7 are zeros, so 8 persons are at or below the cut and 7 are above, both ≥ 5. P75: r = 12 leaves 3 above, so it is null.
- Bands: `up_to_p50` for values ≤ 0.297 (p07, p08, p09, p11-p16, p21); `above_p50` for the rest (p03, p04, p05, p06, p10).
- The other four groups have 1 to 3 persons and publish no cut.

### A.9 Shadow scenarios and alerts

**Fixture policy.**
- `tiered_caps`: caps L1 34.56, L4 23.45, default 12.34; top-up 4.56.
- `unit_caps_plus_flat`: individual cap 29.87; envelopes alpha 34.56 and beta 23.45; top-up 4.56.
- Alert fractions 0.5, 0.8 and 1.0.
- Breaker 17.89. Spike: multiplier 5, floor 12.34, trailing 7 days, minimum history 5.
- Named exceptions:
  - NX1: central, `month_cost_ref_above` 23.45;
  - NX2: manager, `spike_events_at_least` 1.

**Positions (month to date).**
- p04: 0.297 on 05-04, 33.297 from 05-06.
- p18:

  | Day | Change | Position |
  |---|---|---|
  | 05-11 | +1.10 | 1.10 |
  | 05-12 | +1.10 (C1) + 1.00 (Copilot) | 3.20 |
  | 05-13 | +1.10 | 4.30 |
  | 05-14 | +1.10 | 5.40 |
  | 05-15 | +1.10 | 6.50 |
  | 05-18 | +22.00 | 28.50 |
  | 05-26 | +1.50 | 30.00 |
- Alpha (u02):

  | Day | Change | Position |
  |---|---|---|
  | 05-04 | +0.297 | 0.297 |
  | 05-05 | +0.297 | 0.594 |
  | 05-06 | +2.97 + 33.00 | 36.564 |
  | 05-11 | +0.297 | 36.861 |
  | 05-12 | +0.594 | 37.455 |
  | 05-13 | +0.594 + 1.122 | 39.171 |
  | 05-14 | +0.594 | 39.765 |
  | 05-19 | +0.594 | 40.359 |
  | 05-25 | +0.594 | 40.953 |
  | 05-30 | +0.50 | **41.453** |
- Beta (u03):

  | Day | Change | Position |
  |---|---|---|
  | 05-05 | +0.40 | 0.40 |
  | 05-07 | +0.80 | 1.20 |
  | 05-11 | +1.10 | 2.30 |
  | 05-12 | +3.20 | 5.50 |
  | 05-13 | +1.10 | 6.60 |
  | 05-14 | +1.10 | 7.70 |
  | 05-15 | +1.10 | 8.80 |
  | 05-18 | +22.00 | 30.80 |
  | 05-20 | +0.60 | 31.40 |
  | 05-26 | +1.50 | **32.90** |

**`tiered_caps`.**
- p04 (cap 12.34): **stopped 05-06**. Excess 33.297 − 12.34 = **20.957**. Top-ups: 20.957 / 4.56 = 4.60, so **5**, totalling 22.80.
- p18 (cap 12.34): **stopped 05-18**. Excess 30.00 − 12.34 = **17.66**. Top-ups: 17.66 / 4.56 = 3.87, so **4**, totalling 18.24.
- Everyone else stays under their cap.
- Summary: 21 persons, 2 stopped, excess 38.617, top-ups 41.04.

**`unit_caps_plus_flat`.**
- p04: stopped 05-06 (33.297 > 29.87). Excess **3.427**, 1 top-up (4.56).
- p18: 28.50 on 05-18 is under 29.87, and 30.00 on 05-26 is over, so **stopped 05-26**. Excess **0.13**, 1 top-up.
- u02: stopped 05-06 (36.564 > 34.56). Excess 41.453 − 34.56 = **6.893**: 6.893 / 4.56 = 1.51, so **2** top-ups (9.12).
- u03: stopped 05-18 (30.80 > 23.45). Excess 32.90 − 23.45 = **9.45**: 9.45 / 4.56 = 2.07, so **3** top-ups (13.68).
- Summary: 21 persons, 2 stopped, excess 3.557, top-ups 9.12; 2 units, 2 stopped, excess 16.343, top-ups 22.80.
- Exec form: `persons_stopped` 2 < 5, so null.

**Alert events (22).**
- **Breaker.** p04 on 05-06 (day cost 33.00); p18 on 05-18 (22.00).
- **Spike.**
  - p04 on 05-06: the window is 05-01..05-05 (5 days in the build period), sum 0.297. The day is 33.00 > 12.34, and 33.00 × 5 = 165 > 5 × 0.297 = 1.485.
  - p18 on 05-18: the window is 05-11..05-17 (7 days), sum 6.50. The day is 22.00 × 7 = 154 > 5 × 6.50 = 32.5.
- **Scenario alerts**, as (scenario, subject, day, thresholds, value):

  | Scenario | Subject | Day | Thresholds | Value |
  |---|---|---|---|---|
  | `tiered_caps` | p04 | 05-06 | 6.17, 9.872, 12.34 | 33.297 |
  | `tiered_caps` | p18 | 05-15 | 6.17 | 6.50 |
  | `tiered_caps` | p18 | 05-18 | 9.872, 12.34 | 28.50 |
  | `unit_caps_plus_flat` | p04 | 05-06 | 14.935, 23.896, 29.87 | 33.297 |
  | `unit_caps_plus_flat` | p18 | 05-18 | 14.935, 23.896 | 28.50 |
  | `unit_caps_plus_flat` | p18 | 05-26 | 29.87 | 30.00 |
  | `unit_caps_plus_flat` | u02 | 05-06 | 17.28, 27.648, 34.56 | 36.564 |
  | `unit_caps_plus_flat` | u03 | 05-18 | 11.725, 18.76, 23.45 | 30.80 |

**Named exceptions.**
- NX1: p04 (33.297) and p18 (30.00).
- NX2: p04 (1 spike), in manager p02's folder; p18 (1 spike), in manager p17's folder.

### A.10 The LiteLLM importer sample

`docs/gold-fixture/litellm/sample.json` has LiteLLM's shape:
- an invented `sample_spec` entry;
- `vendor-q/model-q-one`: input 1.5e-06, output 6e-06, cache read 1.5e-07, cache creation 1.875e-06, no 1-hour price; also an above-200k price, a `mode` and a `max_input_tokens`, all ignored;
- `model-q-two`: input 2e-07, output 8e-07, no cache prices;
- `model-q-three`: output only;
- `model-q-unused`: not in `keys.txt`, so ignored.

Expected entries (× 1,000,000, 6 decimals):
- `model-q-one`: 1.500000 / 0.150000 / 1.875000 / null / 6.000000;
- `model-q-two`: 0.200000 / 0.200000 / 0.200000 / null / 0.800000 (the missing cache prices take the input price; the missing 1-hour price stays null);
- `model-q-three`: `per_million: null`, because it has no input price.
