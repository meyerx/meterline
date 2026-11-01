---
type: spec
status: accepted
accepted: 2026-10-11 (owner, in chat)
updated: 2026-10-11
builds_on: docs/meterline-v2-SPEC-v1.0-2026-10-10.md (accepted; frozen), docs/records.md, docs/analysis/meterline-sim-analysis.md
---

# meterline-sim v1: a simulated organisation and its AI usage, written as silver records with a ground truth

meterline-sim invents an organisation (units, people, managers, seats) and six months of its AI usage. It writes three things:

- **silver**: the records meterline's connectors would have written for that usage, in the v2 record format;
- the **organisation files** the gold layer reads;
- a **ground truth**: everything the simulator knows, such as each person's profile over time, every injected event and the expected totals.

The gold layer can then be tested against data whose correct answers are known in advance. The facts behind this spec, with sources, are in `docs/analysis/meterline-sim-analysis.md`. Grounding labels: **[V]** verified on 2026-10-11 at the cited source, **[I]** inferred, **[A]** a choice this spec makes.

## 1. Purpose, scope and decisions

The owner took these decisions on 2026-10-11. They bind this spec.

| # | Decision | Where |
|---|---|---|
| D1 | v1 writes silver records directly, through meterline's own record classes, writer and validators, plus a ground-truth file and the organisation files. No raw API responses and no replay server. | 2, 10, 11 |
| D2 | Two presets: `vertical` (~400 people; 4 levels: company → divisions → departments → teams; ~45 teams; mostly business functions plus a central IT) and `tiny` (~20 people, 2 units, every profile present, for fast tests), plus a scale parameter (same shape at any size). The preset format already allows several unit dimensions and matrix memberships. Names are invented and generic; nothing resembles a real organisation. | 3 |
| D3 | Providers: Claude Enterprise (chat, Cowork, Office, Claude Code); GitHub Copilot Business for central IT (~40 seats); a small LiteLLM stream of 2-3 service accounts so that unassigned usage exists, including one self-hosted model with null cost. | 4 |
| D4 | Five profiles, with default shares for `vertical`: dormant 30%, curious 30%, office worker 30%, developer 8%, power user 2%. Profiles drift over time. Each profile's distributions are stated exactly. | 5 |
| D5 | Default 6 months of daily data from a configurable start date, batch mode only. Provider revisions happen inside the batch: a late correction writes a later batch that supersedes earlier records, and `is_final` follows each provider's finality rule relative to the simulated pull date. | 6, 7 |
| D6 | Weekends are low (developers ~10% of a weekday; service accounts unchanged). The holiday list is configurable and neutral by default (a year-end break and a ~30% summer dip). | 5.2, 6.2 |
| D7 | Five events. Each can be switched on or off, and each is written to the ground truth with dates and actors: joiners and leavers at ~1.5% a month; one reorganisation (a department split in two mid-period); one runaway agent (×20 for 2 days); one late provider correction; one price change mid-month. | 7 |
| D8 | One editable price table, in USD, with invented values labelled as examples. Claude Enterprise: price per seat plus usage, no discount. Copilot: flat price per seat by plan, included credits pooled at the billing entity, metered overage beyond the pool, optional budget. No preset reproduces a real organisation's contract. | 8 |
| D9 | The same preset, seed and parameters give the same bytes. There is one random stream per component, derived from the seed and the component name. No iteration over unordered collections, no wall clock. Names come from a bundled invented word list (no Faker). | 5.1, 9 |
| D10 | Outputs per preset and seed: organisation CSVs, silver records in `records/<kind>/<batch_id>.jsonl`, and a ground-truth file. | 11 |
| D11 | CLI: `meterline-sim generate --preset NAME --seed N --months N`. | 12 |
| D12 | The done-checks of section 15. | 15 |

**Scope.** One package, `meterline-sim`, that generates data. It reads no provider API and needs no credential. It computes no gold figures; the gold layer is a separate spec (a separate spec, `meterline-gold-core`).

## 2. Package and layout

```
packages/meterline-sim/
  pyproject.toml                       name meterline-sim, version 1.0.0, script meterline-sim = meterline_sim.cli:main
  src/meterline_sim/
    __init__.py                        generate(), verify(), __version__
    draws.py                           the draw kit (5.1) and stream derivation (9.1)
    params.py                          defaults, preset loading, merging, validation, scaling (3)
    names.py                           the invented name lists (9.3)
    org.py                             units, people, memberships, identities (3, 7.1, 7.2, 11.1)
    behaviour.py                       profiles, drift, calendar, the usage ledger (5, 6)
    events.py                          the five events (7)
    prices.py                          price table and pricing of usage rows (8.1, 8.2)
    billing.py                         Copilot pool, discounts, overage, seat fees (8.3, 8.4)
    render.py                          ledger -> silver records per provider, batches (4, 6.3)
    truth.py                           ground truth and manifest (11.3, 11.4)
    verify.py                          the invariants of section 15, as functions
    cli.py                             main(argv=None)
    data/defaults.json                 every parameter's default (3.6, values from the tables of sections 4-8)
    data/presets/vertical.json, data/presets/tiny.json
    data/prices.json                   the example price table (8.1)
    data/names.json                    the name lists of 9.3, generated once and committed
  tests/                               done-checks (15); test-only presets under tests/presets/
```

- **Runtime.** Python 3.10+. The only runtime dependency is `meterline` (this repo). Tests add `pytest` and `hypothesis`.
- **Records.** Every record is built with meterline's classes (`UsageRecord`, `ActivityRecord`, `SeatSnapshot`, `SeatAssignment`, `OrgTotal`). Each is checked with meterline's validators before anything is written (4.5) and written with `meterline.write_jsonl`.
- **No other package changes.** Nothing under `packages/meterline` or the connectors changes.

`generate(preset, seed, months=6, start="2026-04-01", scale="1", out="./sim-out", params=None) -> pathlib.Path` runs the pipeline of section 10 and returns the output folder. `preset` is a bundled name or a path; `params` is a dict or a path.

## 3. Presets and parameters

### 3.1 Two kinds of content

- **Structure**: the organisation's shape, which only a preset file holds. Keys: `preset_format`, `name`, `org`, `dimensions`, `units`, `matrix_units`, `profiles`, `developer_functions` (plus `parameters`, which holds overrides).
- **Parameters**: everything else, listed in 3.6 with their defaults (`data/defaults.json`). A preset may override any parameter under its top-level key `parameters`. A run may then override any parameter again with `--params`.

**Resolution.** Resolved parameters = defaults, deep-merged with the preset's `parameters` object, then with `--params`. A merge combines objects key by key and replaces lists and scalars. A key that is not in the defaults is an error, except new entries under `behaviour.service_accounts`.

**Value types.**
- Probabilities, rates, shares, factors, medians, sigmas and prices are decimal strings (`"0.30"`).
- Counts of days, `multiplier`, `days`, `invite_lead_days`, `min_per_period`, `month_index`, `window_end_offset_days`, `window_days`, `after_days`, `lag_days`, the two `active_windows` lengths, `seat_age_days` and integer ids are JSON integers.
- A JSON number with a fraction, anywhere, is an error (exit 2).

### 3.2 Structure keys

| Key | Type | Meaning |
|---|---|---|
| `preset_format` | int | `1`. |
| `name` | string | `^[a-z][a-z0-9-]*$`. Also used in the output folder name. |
| `org` | object | `name` (display name), `slug` (`^[a-z][a-z0-9-]*$`), `email_domain` (default `example.com`; only `example.com`, `example.org`, `example.net`, or a domain ending in `.test`, `.invalid` or `.example` is accepted). |
| `dimensions` | list of strings | Unit dimensions. The first is the **line** dimension (reporting lines; every employed person has exactly one line unit on each day). Further dimensions hold matrix units (3.4). |
| `units` | object | The root unit of the line dimension, nested (3.3). |
| `matrix_units` | list | Units of the other dimensions, with their members (3.4); `[]` when none. |
| `profiles` | object | `{"shares": {profile: decimal string}}` (summing to exactly 1 in `Decimal`), or `{"counts": {profile: int}}`. |
| `developer_functions` | list of strings | The functions whose people may hold the developer profile (5.3). |
| `parameters` | object | Parameter overrides (3.1, 3.6); `{}` when none. |

Any other top-level key is an error.

### 3.3 Line units

Each unit object has these keys:

- `key`: unique, `^[a-z][a-z0-9-]*$`;
- `name`;
- `kind`: `company`, `division`, `department` or `team`;
- `function`;
- exactly one of the following:
  - `children`: a list of unit objects, on company, division and department units;
  - `teams` and `team_size`: on a `department` only. The department gets `teams` team units. Their keys are `<dept key>-<n>` and their names `<dept name> <n>`, with n counting from 1. Each team draws its size as `uniform_int(min, max)` from `team_size: [min, max]`, using stream `org`, teams in creation order;
  - `size`: on a `team` only; a fixed size.

A department with both `children` and `teams` is an error.

**Heads and members.**
- Every company, division and department unit has one head, who belongs to that unit and is counted in the headcount.
- A team's size includes its lead. The lead is the team's first member and its head.

**Scale.** With scale `s` (decimal string, > 0, default `"1"`), computed in `Decimal`:
- a department's `teams` becomes `max(1, floor(teams × s + 0.5))`;
- a fixed `size` becomes `max(1, floor(size × s + 0.5))`.

`team_size` ranges, units above teams, service accounts and matrix units do not scale.

**Building order.** Units and people are created in one walk.
- **Unit ids.** Units are numbered `u0001`, `u0002`, ... in preorder: a parent before its children, children in listed order, and a department's generated teams in n order.
- **Person ids.** At each unit the walk creates the head first; for a team it creates the lead and then the other members. Person ids are `p00001`, `p00002`, ... in creation order.
- **Ids after the initial walk.** Matrix units take the next unit ids in listed order, then units created by the reorganisation (7.2). Joiners continue the person numbering.
- **`level`.** `L1` is the company head. `L2` is the head of a `division` unit and `L3` the head of a `department` unit. `L4` is a team lead and `L5` is everyone else.
- **`manager_id`.** It is the head of the closest unit above the person's own that has a head. For a team member who is not the lead, it is the team lead. The company head has none.
- **`function`.** It is the unit's `function`.
- **`cost_center`.** One counter, starting at 1, runs in preorder over the company, the divisions, the departments, and the teams that have no department above them. Each of these units gets `CC-<3-digit number>`. A team under a department takes the department's. Each person takes their unit's. A new unit created by the reorganisation takes the next number after the highest one in use.

### 3.4 Matrix units and memberships

Each entry is `{"key", "name", "dimension", "kind": "group", "members": {"from_units": [line unit keys], "count": n}}`.

- **Members.** Among the people employed on `start` whose line unit is one of `from_units` or below them, the first `n` by person id become members.
- **Membership file.** Each membership is written to `org/memberships.csv` (11.1) with `role` `member`. It is valid from `start` until the member's last day, or open.
- **Unit row.** A matrix unit appears in `org/units.csv` with its `dimension`. Its `parent_id`, `function`, `cost_center` and `head_person_id` are empty.

Neither bundled preset uses matrix units (`matrix_units: []`). The test-only preset `tests/presets/matrix.json` does: it is the `tiny` preset plus dimension `community` and one group of 3 members from `beta` (check S12).

### 3.5 The two presets

**`vertical`.**
- **Organisation.** `org` is `{"name": "Example Vertical Co", "slug": "example-vertical"}`, with dimensions `["line"]`.
- **Shape.** The root is `Company` (key `company`, function `executive`). It has six divisions (function `executive`), 18 departments and 45 teams.

| Division (key) | Department (key) | Teams | Team size | Function |
|---|---|---|---|---|
| Commercial (`commercial`) | Sales (`sales`) | 4 | 7-11 | sales |
| | Customer Service (`customer-service`) | 4 | 7-11 | service |
| | Marketing (`marketing`) | 3 | 7-11 | marketing |
| Operations (`operations`) | Supply (`supply`) | 3 | 7-11 | operations |
| | Logistics (`logistics`) | 3 | 7-11 | operations |
| | Quality (`quality`) | 2 | 7-11 | operations |
| Finance and Legal (`finance-legal`) | Accounting (`accounting`) | 2 | 7-11 | finance |
| | Controlling (`controlling`) | 2 | 7-11 | finance |
| | Legal (`legal`) | 2 | 7-11 | legal |
| People (`people`) | Human Resources (`human-resources`) | 3 | 7-11 | hr |
| | Learning (`learning`) | 1 | 7-11 | hr |
| Product (`product`) | Product Management (`product-management`) | 3 | 7-11 | product |
| | Design (`design`) | 2 | 7-11 | design |
| Central IT (`central-it`) | Platform (`platform`) | 3 | 5-5 | engineering |
| | Applications (`applications`) | 3 | 5-5 | engineering |
| | Data (`data`) | 2 | 4-4 | engineering |
| | Workplace (`workplace`) | 2 | 7-11 | it_ops |
| | Security (`security`) | 1 | 7-11 | security |

- **Headcount.** The expected headcount at scale 1 is 25 heads above teams + 38 engineering team members + 37 teams × 9 = 396 [I: mean of the team-size draws].
- **Profiles and developers.** `profiles.shares` is dormant `"0.30"`, curious `"0.30"`, office_worker `"0.30"`, developer `"0.08"`, power_user `"0.02"`. `developer_functions` is `["engineering"]`.
- **Parameter overrides** (under `parameters`).
  - `providers.github_copilot.granted_units` is `["platform", "applications", "data"]`: 41 seat holders at scale 1 (38 team members and 3 department heads), matching D3's ~40.
  - `events.reorganisation.unit` is `"customer-service"`.
  - `providers.litellm.accounts` is `["sa01", "sa02", "sa03"]`.

**`tiny`.**
- **Organisation.** `org` is `{"name": "Example Tiny Co", "slug": "example-tiny"}`, with dimensions `["line"]`.
- **Shape.** The root is `Company` (key `company`, function `executive`, one person: its head). Below it are two units: team `Alpha` (`alpha`, function `sales`, size 10) and team `Beta` (`beta`, function `engineering`, size 9). That makes 20 people and cost centres `CC-001` to `CC-003`.
- **Profiles and developers.** `profiles.counts` is dormant 4, curious 4, office_worker 5, developer 5, power_user 2. `developer_functions` is `["engineering"]`.
- **Parameter overrides** (under `parameters`).
  - `providers.github_copilot.granted_units` is `["beta"]`.
  - `providers.github_copilot.included_credits_per_seat` is `"100"`, low on purpose so that overage happens.
  - `events.reorganisation.unit` is `"alpha"`. `tiny` has no department, so a team is split; see 16.4.
  - `events.joiners_leavers.min_per_period` is 1.
  - `providers.litellm.accounts` is `["sa01", "sa03"]`.

**Counts and headcount.** A `counts` preset uses its counts as the 5.3 targets when they sum to the headcount at the period start. Otherwise each count becomes the share `count / Σ counts`, as at any scale other than 1.

### 3.6 Parameters and their defaults

`data/defaults.json` holds exactly the keys below, with the values the cited tables give. All paths are relative to the resolved parameter object.

| Key path | Default |
|---|---|
| `behaviour.profiles.<profile>.p_active`, `.weekend_factor` | 5.2 |
| `behaviour.profiles.<profile>.products.<product>` | 5.2 (probability on an active day) |
| `behaviour.volume.<profile>.<product>.req_median`, `.req_sigma` | 5.4 |
| `behaviour.volume.<profile>.<product>.model_weights.<model>` | 5.4 |
| `behaviour.tokens.<product>.in_median`, `.in_sigma`, `.out_median`, `.out_sigma`, `.f_read`, `.f_write`, `.f_1h`, `.web_rate` | 5.4 |
| `behaviour.copilot.<profile>.shown_median`, `.ask_median`, `.agent_median`, `.cli_median`, `.app_median` | 5.7 |
| `behaviour.copilot.<profile>.model_weights.<model>` | 5.7 |
| `behaviour.drift.<from>.<to>` | 5.8 |
| `behaviour.service_accounts.<id>.model`, `.req_median`, `.req_sigma`, `.in_median`, `.out_median`, `.f_read`, `.f_write`, `.failure_rate` | 4.3 |
| `providers.anthropic.enabled` | `true` |
| `providers.anthropic.active_windows` | `[7, 30]` |
| `providers.github_copilot.enabled` | `true` |
| `providers.github_copilot.plan` | `"business"` |
| `providers.github_copilot.granted_units` | `[]` |
| `providers.github_copilot.included_credits_per_seat` | `null` (take the price table's) |
| `providers.github_copilot.seat_age_days` | `[30, 365]` |
| `providers.github_copilot.active_windows` | `[7, 28]` |
| `providers.litellm.enabled` | `true` |
| `providers.litellm.proxy_name` | `"example-proxy"` |
| `providers.litellm.accounts` | `[]` |
| `providers.litellm.unpriced_cost` | `"null"` (or `"zero"`; 4.3, 16.2) |
| `calendar.holidays` | 6.2 |
| `events.joiners_leavers.enabled`, `.leaver_rate`, `.joiner_rate`, `.invite_lead_days`, `.min_per_period` | `true`, `"0.015"`, `"0.015"`, 3, 0 |
| `events.reorganisation.enabled`, `.unit`, `.month_index` | `true`, `null`, `null` (7.2) |
| `events.runaway_agent.enabled`, `.multiplier`, `.days`, `.model`, `.start` | `true`, 20, 2, `"model-a-standard"`, `null` (7.3) |
| `events.late_correction.enabled`, `.window_end_offset_days`, `.window_days`, `.share`, `.factor`, `.after_days` | `true`, 7, 3, `"0.25"`, `"1.10"`, 5 |
| `events.price_change.enabled`, `.provider`, `.model`, `.factor`, `.date` | `true`, `"anthropic"`, `"model-a-standard"`, `"0.8"`, `null` (7.5) |
| `pull.lag_days` | 3 (≥ 1) |
| `contractor_share` | `"0.05"` |
| `locations.values`, `.weights` | `["site-a", "site-b", "site-c"]`, `["0.5", "0.3", "0.2"]` |
| `price_table` | `null` (the bundled `data/prices.json`), or a path |

**Disabled providers.** A disabled provider writes no records. An event that needs it (the correction needs Copilot; the price change needs its `provider`) is recorded with `applied: false` (11.3).

**Fixed constants.** The activity ratios of 5.6 and the Copilot ratios of 5.7 that are not listed above are fixed in v1 (16.8).

## 4. Providers and the silver they produce

The simulator keeps one **ledger** of truth rows (5.9). The rendering step turns ledger rows into records by the rules below, which are the connectors' rules (analysis F4). Where a connector has a case the simulator never meets (for example `cost_only`), the simulator does not produce it.

**Common to every record.**
- `record_version` is 2 and `scope` is `""`.
- `pulled_at` comes from the pull plan (6.3), written with `+00:00`. `batch_id` is `pulled_at` formatted `%Y%m%dT%H%M%SZ`.
- A field this section does not name takes the class default: null, `{}`, or `false` for `cost_only`.
- Money (`cost`, `cost_list`) is a `Decimal`, serialised by meterline (`amount_str`: the exact value, trailing zeros removed, at least two decimals).
- Quantities and `Decimal` extras are serialised by `quantity_str`. Integer extras stay integers.

### 4.1 Claude Enterprise (`provider` `anthropic`)

**Identifiers.**
- `org_id` is built from the first 32 hex digits of `sha256("anthropic-org|<seed>")`, split 8-4-4-4-12 with `-`. The seed is written in decimal.
- Every person holds one seat from their start date to their last day.
- `actor_id` is `"user_" + sha256("anthropic|<seed>|<person_id>").hexdigest()[:24]`, and `actor_email` is the person's email (11.1).

**`UsageRecord`.** One per person × day × usage product × model with ledger usage.
- `grain` `day`; `actor_kind` `user`; `actor_login` null.
- `actor_deleted` is true if the person's last day is on or before the UTC date of `pulled_at`, else false.
- `product` is one of `chat`, `cowork`, `office_agent`, `claude_code` [A: invented values in the shape of the usage report]. `model` comes from the ledger, and `model_provider` is `anthropic`.
- Every token field is an integer: `tokens_input_uncached`, `tokens_cache_read`, `tokens_cache_write` (= 5m + 1h), `tokens_cache_write_5m`, `tokens_cache_write_1h`, and `tokens_input_total` (= the three input buckets).
- `tokens_reasoning` is null; `requests` is an integer; `quantities` is `{}`.
- `extras` is `{"web_search_requests": n}`, an integer with 0 included.
- `cost` = `cost_list` = the priced amount (8.2); `currency` `USD`; `cost_basis` `consumption`.
- `cost_only` false; `is_final` by the month rule (4.6); `data_refreshed_at` = `pulled_at` − 30 minutes.

**`ActivityRecord`.** One per person × day × activity product with at least one non-zero count.
- `grain` `day`; `actor_kind` `user`; `actor_id` and `actor_email` as above; `actor_login` null.
- Products: `chat`, `cowork`, `office_agent.excel`, `office_agent.word`, `office_agent.powerpoint`, `office_agent.outlook`, `claude_code`, and `all` (web searches, only when non-zero).
- The eight core counters `sessions`, `messages`, `commits`, `pull_requests`, `lines_added`, `lines_removed`, `edits_accepted` and `edits_rejected` are integers, 0 where the product has none. `requests`, `suggestions_shown` and `suggestions_accepted` are null.
- `counters` always holds the five keys `conversations`, `artifacts`, `skills_used`, `connectors_used` and `web_searches`, 0 included. `web_searches` is non-zero only on `all`.
- Values come from 5.6. `data_refreshed_at` is null.

**`SeatSnapshot`.** One per day of the period.
- `seats` = people holding a seat that day.
- `pending_invites` = joiners whose invite day (7.1) is on or before that day and whose start date is after it. It is an integer, 0 when none.
- `pending_cancellations` is null.
- `daily_active` = people with an Anthropic usage ledger row that day. `weekly_active` and `monthly_active` = distinct such people over the last `active_windows[0]` and `active_windows[1]` days ending that day, counting only days inside the period [A: window lengths].
- `data_refreshed_at` is null.

**`OrgTotal`.** One per day of the period, also on days with no usage.
- `product` `""`, `model` `""`, `model_provider` `anthropic`.
- Every token field, `requests` and `extras.web_search_requests` is the integer sum of that day's `UsageRecord`s (0 on an empty day). `tokens_reasoning` is null.
- `cost` = `cost_list` = the sum (`Decimal("0")` on an empty day, written `0.00`); `currency` `USD`; `cost_basis` `consumption`.
- `is_final` by the month rule; `data_refreshed_at` = `pulled_at` − 30 minutes.

There is no `SeatAssignment`: the API has no per-person seat list.

### 4.2 GitHub Copilot Business (`provider` `github_copilot`)

**Identifiers and seats.**
- `org_id` is `"org:" + org.slug` (organization level). The plan is `providers.github_copilot.plan`.
- **Seat holders.** A person holds a seat if their line unit is in `granted_units` or below one of them on their seat start day. The seat start is the period start for people present then, otherwise the start date. A new unit created by the reorganisation counts as granted if the split unit was granted or below a granted unit. The seat lasts until the day before the seat's `pending_cancellation_date` (7.1), or to the end of the period.
- `actor_id` is `str(100000 + n)` for person `p<n>` (`p00042` → `"100042"`). `actor_login` is the person's login (11.1). `actor_email`, `actor_deleted`, `model_provider` and `data_refreshed_at` are null on every Copilot record.
- A **Copilot-active day** is a day on which the person's Copilot gate passes (5.5).

**Daily credits.** A `UsageRecord`, product `copilot`, one per seat holder × Copilot-active day, written even when credits are 0.
- `grain` `day`; `actor_kind` `user`; `model` `""`.
- Every token field and `requests` are null.
- `quantities` is `{"ai_credit": credits}`. `cost` = `cost_list` = credits × `Decimal("0.01")`; `currency` `USD`; `cost_basis` `consumption`. `extras` is `{}`.
- `is_final` by the Copilot day rule (4.6).

**CLI and app tokens.** A `UsageRecord` with product `copilot_cli` or `copilot_app` (model `""`), on Copilot-active days when that surface was used (5.7).
- `tokens_input_total`, `tokens_output` and `requests` are set. The three input buckets, the 5m/1h split and `tokens_reasoning` are null.
- `quantities` is `{}`. There is no cost: `currency` `""`, `cost_basis` null.
- `is_final` by the day rule.

**Activity by feature.** One `ActivityRecord` per feature used that day (5.7), with product = the feature.
- `messages`, `suggestions_shown`, `suggestions_accepted`, `lines_added` and `lines_removed` are integers.
- `sessions` and `requests` are integers on `copilot_cli` and `copilot_app` and null elsewhere.
- `commits`, `pull_requests`, `edits_accepted` and `edits_rejected` are null.
- `counters` holds `lines_suggested_to_add` and `lines_suggested_to_delete`.

**The `copilot` activity record.** Also on every Copilot-active day, one `ActivityRecord` with product `copilot`. All its core counters are null. Its `counters` hold the five keys `custom_agents_used`, `mcp_servers_used`, `plugins_used`, `skills_used` and `slash_commands_used`, 0 included.

**Monthly person × model.** A `UsageRecord`, one per seat holder × month × model with gross credits > 0 that month.
- `grain` `month`; `period_start` / `period_end` are the calendar month's first and last day, even where they fall outside the period. `product` is `copilot`.
- `quantities` is `{"ai_credit": gross}`. `cost_list` = gross × 0.01 and `cost` = (gross − person discount) × 0.01; `cost_basis` `consumption`.
- `extras` has the `Decimal` values `discount_quantity`, `net_quantity`, `net_amount` (= (gross − person discount) × 0.01) and `price_per_unit` (`Decimal("0.01")`) (8.3).
- Tokens and `requests` are null. `is_final` by the month rule.

**Organization per day × model.** An `OrgTotal`, one per day × model with gross credits > 0.
- `product` `copilot`; `model` = the model.
- `quantities` is `{"ai_credit": gross}`. `cost_list` = gross × 0.01 and `cost` = the net amount; `cost_basis` `billed`.
- `extras` as above, with the organization's discount (8.3). Tokens and `requests` are null.
- `is_final` by the month rule.

**`SeatSnapshot`.** One per day.
- `seats` = seat holders that day. `pending_invites` is 0.
- `pending_cancellations` = seat holders whose last day (7.1) is on or before that day.
- `daily_active`, `weekly_active` and `monthly_active` = seat holders with a Copilot-active day, that day and over the last `active_windows` days ending that day, counting only days inside the period [A].

**`SeatAssignment`.** One per seat holder per day, written in that day's seat-list batch (6.3).
- `plan` is the plan.
- `assigned_at` is the seat start at `T00:00:00+00:00`. For seat holders present at the period start, it is the period start minus `uniform_int(seat_age_days[0], seat_age_days[1])` days, drawn from stream `seats` in person-id order over those holders only.
- `last_active_at` is `T12:00:00+00:00` of the latest Copilot-active day strictly before this day within the period, else null. `last_authenticated_at` = `last_active_at`.
- `pending_cancellation_date` is set from the person's last day on (7.1), else null.
- `granted_by` is the key of the outermost granted unit that contains the person's line unit at seat start. It does not change afterwards.

### 4.3 LiteLLM service accounts (`provider` `litellm`)

- `org_id` is `providers.litellm.proxy_name`.
- Service accounts are API keys with no owner. Their traffic reaches silver only as `unassigned` (v2 spec 5.4 rule 4); the ground truth keeps each account apart.
- The accounts used are `providers.litellm.accounts`. Account definitions (defaults):

| Id | `model` | `req_median`, `req_sigma` | `in_median` | `out_median` | `f_read` | `f_write` | `failure_rate` |
|---|---|---|---|---|---|---|---|
| `sa01` | `model-a-standard` | `"400"`, `"0.3"` | `"2000"` | `"300"` | `"0.30"` | `"0.05"` | `"0.01"` |
| `sa02` | `model-b-light` | `"900"`, `"0.3"` | `"6000"` | `"400"` | `"0.10"` | `"0.00"` | `"0.01"` |
| `sa03` | `selfhosted-c-small` | `"2000"`, `"0.2"` | `"1500"` | `"200"` | `"0.00"` | `"0.00"` | `"0.01"` |

**`UsageRecord`.** One per day × model with non-zero traffic.
- `grain` `day`; `actor_kind` `unassigned`; `actor_id` `(unassigned)`; `product` `""`.
- `tokens_input_total`, `tokens_cache_read`, `tokens_cache_write`, `tokens_input_uncached` (= total − read − write) and `tokens_output` are set. The 5m/1h split and `tokens_reasoning` are null.
- `requests` is set; `quantities` is `{}`.
- `extras` is `{"successful_requests": int, "failed_requests": int}`.
- `cost` = the priced amount (8.2), with `cost_list` null, `currency` `USD`, `cost_basis` `estimate`.
- **Unpriced model.** A model whose price entry is null (the self-hosted model, D3) gets `cost` null, `currency` `""` and `cost_basis` null. If `unpriced_cost` is `"zero"`, it gets instead `cost` `Decimal("0")`, `currency` `USD` and `cost_basis` `estimate` (16.2).
- `is_final` by the LiteLLM day rule.

**`OrgTotal`.** One per day × model with the same measures; per-model rows only, never an all-models row.

LiteLLM writes no activity, seat or seat-assignment records.

### 4.4 What v1 never produces

- `proxy_user` and `end_user` records;
- `cost_only` records;
- `SeatAssignment` for Anthropic;
- Copilot `premium_request` units, unknown units and the `(no model)` billing case;
- enterprise-level Copilot.

### 4.5 Validation before writing

Every batch file is checked with `meterline.batch_failures(records, today)`, where `today` is the UTC date of the batch's `pulled_at`. The result must be `[]`. Each Anthropic and LiteLLM main batch is also checked with `meterline.actor_cost_failures(usage, org_totals)`, which must return `[]`.

`render.py` imports the batch check by name (`from meterline import batch_failures`) and calls it through that name. Check S15 depends on this.

A failure stops the run, writes nothing (11.5) and exits 1.

### 4.6 Finality

`P` is a batch's `pulled_at`, and `ended(d)` is midnight UTC at the start of day d + 1.

| Provider, records | `is_final` |
|---|---|
| Anthropic, all | `(P.date() − last day of the month of period_start).days > 7` |
| LiteLLM, day records | `P >= ended(day) + 2 days` |
| Copilot, day records (credits, CLI, app) | `P >= ended(day) + 4 days` |
| Copilot, month records and organization billing | `(P.date() − last day of the month of period_start).days > 7` |

These are the connectors' rules (analysis F4: `meterline_anthropic/normalise.py:148-152`, `meterline_litellm/normalise.py:74-77`, `meterline_copilot/normalise.py:93-102`).

## 5. Profiles, behaviour and drift

### 5.1 The draw kit and arithmetic

Each function reads one stream `r` (9.1); `u()` is `r.random()`.

| Function | Definition | Draws used |
|---|---|---|
| `bern(p)` | `u() < float(p)` | 1 |
| `stoch_round(x)` (x ≥ 0) | `floor(x) + (1 if u() < float(x − floor(x)) else 0)` | 1, always |
| `normal()` | Marsaglia polar method. Loop: `v1 = 2.0·u() − 1.0`; `v2 = 2.0·u() − 1.0`; `s = v1·v1 + v2·v2`; repeat while `s >= 1.0` or `s == 0.0`. Return `v1 · math.sqrt(−2.0 · ln(s) / s)`. `v2`'s value is discarded. | 2 per loop |
| `lognorm(median, sigma)` | `float(median) · exp(float(sigma) · normal())` | as `normal()` |
| `count(median, sigma, minimum)` | `max(minimum, floor(lognorm(median, sigma) + 0.5))` | as `normal()` |
| `uniform_int(a, b)` (a ≤ b) | `a + floor(u() · (b − a + 1))` | 1 |
| `pick(items, weights)` | `t` = left-to-right `sum` of `float(w)`; `x = u() · t`. Return the first item whose running sum (left to right, in listed order) is greater than `x`. Items with weight 0 are never returned. If rounding leaves no item, return the last item with a non-zero weight. | 1 |
| `shuffle(items)` | Fisher-Yates: for `i` from `len − 1` down to 1, `j = floor(u() · (i + 1))`, swap `i` and `j` | `len − 1` |

**`ln` and `exp`.** In the kit, `ln(s)` is `float(Decimal(s).ln())` and `exp(y)` is `float(Decimal(y).exp())`, in Decimal's default context (28 digits, correctly rounded). `math.sqrt` is correctly rounded by IEEE 754. The kit therefore gives the same floats on every platform.

**Arithmetic rule.** Every expression in this spec is computed in one of two ways:
1. **In `Decimal`**, default context, when all its inputs are integers or parameters (decimal strings). A conversion to an integer then uses `ROUND_HALF_UP` (`to_integral_value(ROUND_HALF_UP)`).
2. **In `float`**, left to right as written, when it contains a float from the kit. A conversion to an integer is then `floor(x + 0.5)`. Parameters enter as `float(Decimal-string)`.

A probability passed to `bern` or used as a gate is computed in `Decimal`, then converted with `float()` once. Money and credits are always `Decimal`. Every bare numeric constant written in 5.6 and 5.7 (`0.2`, `0.3`, `0.28`, `2.5`, `15`, `0.8`, ...) is a `Decimal` literal (`Decimal("0.3")`). So `stoch_round(7 × 0.3)` rounds `Decimal("2.1")`, unless the expression also contains a kit float, as `requests * x` does.

### 5.2 Profiles

There are five profiles. Canonical order: `dormant`, `curious`, `office_worker`, `developer`, `power_user`.

| Profile | Meaning | `p_active` (weekday) | `weekend_factor` | `products` (probability on an active day) |
|---|---|---|---|---|
| dormant | has a seat, almost no use | `"0.01"` | `"0.05"` | chat `"1.0"` |
| curious | chat 1-2 days a week | `"0.30"` | `"0.05"` | chat `"1.0"` |
| office_worker | chat plus Cowork or Office most days | `"0.80"` | `"0.05"` | chat `"0.9"`, cowork `"0.5"`, office_agent `"0.4"` |
| developer | Claude Code and/or Copilot daily | `"0.92"` | `"0.10"` | chat `"0.5"`, claude_code `"0.85"`, copilot `"0.9"` |
| power_user | very high volume, premium models, cost outliers | `"0.97"` | `"0.20"` | chat `"0.9"`, cowork `"0.6"`, claude_code `"0.8"`, copilot `"0.9"` |

- **Copilot needs a seat.** Copilot is a product only for people who hold a Copilot seat on that day. Without a seat, it is not in their product list that day and draws nothing. A developer without a seat therefore uses Claude Code only; this is the "and/or" of D4.
- **Idle seats.** Seat holders with another profile keep an idle seat.

### 5.3 Initial assignment

**Targets.** Let N be the people employed on `start`. In `Decimal`, `q = share × N`. Each profile gets `floor(q)`. The remaining people go one each to the profiles with the largest `q − floor(q)`, ties broken in canonical order. A `counts` preset uses its counts (3.5).

**Assignment**, from stream `profiles`:
1. **`developer`.** The candidates are the people whose `function` is in `developer_functions`, in person-id order, shuffled. The first `n_developer` become developers. If there are fewer candidates than the target, all of them become developers and the shortfall is added to `office_worker`.
2. **`power_user`.** All remaining people in person-id order, shuffled; the first `n_power_user`.
3. **The rest.** The remaining people in person-id order, shuffled once. The first `n_office_worker` become office workers, the next `n_curious` curious, and the rest dormant.

### 5.4 Volume per Anthropic product

On a day the person is active and the product's gate passes, the draws are made in this order from stream `use|<person_id>|<product>`:

1. `requests = count(req_median, req_sigma, 1)`
2. `model = pick(["model-a-light", "model-a-standard", "model-a-premium"], model_weights)`
3. `in_per_req = lognorm(in_median, in_sigma)`
4. `out_per_req = lognorm(out_median, out_sigma)`
5. `web = stoch_round(requests × web_rate)` when `web_rate` > 0; otherwise `web` = 0 and nothing is drawn
6. the activity draws of 5.6

Tokens follow, with no draws. The first two lines are float; the rest are `Decimal` with `ROUND_HALF_UP`:

```
input_total = max(1, floor(requests * in_per_req + 0.5))           (float)
output      = floor(requests * out_per_req + 0.5)                   (float)
cache_read  = round_half_up(input_total × f_read)                   (Decimal)
cache_write = min(round_half_up(input_total × f_write), input_total − cache_read)
write_1h    = round_half_up(cache_write × f_1h)
write_5m    = cache_write − write_1h
uncached    = input_total − cache_read − cache_write
```

`req_median`, `req_sigma` and `model_weights` (light, standard, premium):

| Profile | chat | cowork | office_agent | claude_code |
|---|---|---|---|---|
| dormant | `2`, `0.5`; (`0.3`, `0.7`, `0`) | - | - | - |
| curious | `6`, `0.6`; (`0.2`, `0.75`, `0.05`) | - | - | - |
| office_worker | `15`, `0.6`; (`0.15`, `0.75`, `0.10`) | `8`, `0.6`; (`0`, `0.85`, `0.15`) | `5`, `0.6`; (`0.2`, `0.8`, `0`) | - |
| developer | `10`, `0.6`; (`0.1`, `0.8`, `0.1`) | - | - | `120`, `0.7`; (`0`, `0.8`, `0.2`) |
| power_user | `40`, `1.0`; (`0`, `0.3`, `0.7`) | `25`, `1.0`; (`0`, `0.3`, `0.7`) | - | `400`, `1.0`; (`0`, `0.3`, `0.7`) |

`behaviour.tokens.<product>`, the same for every profile:

| Product | `in_median` | `in_sigma` | `out_median` | `out_sigma` | `f_read` | `f_write` | `f_1h` | `web_rate` |
|---|---|---|---|---|---|---|---|---|
| chat | `3000` | `0.4` | `500` | `0.5` | `0.40` | `0.10` | `0.00` | `0.05` |
| cowork | `12000` | `0.4` | `1500` | `0.5` | `0.60` | `0.10` | `0.20` | `0` |
| office_agent | `4000` | `0.4` | `600` | `0.5` | `0.30` | `0.10` | `0.00` | `0` |
| claude_code | `25000` | `0.4` | `900` | `0.5` | `0.85` | `0.05` | `0.30` | `0` |

### 5.5 The daily gate

**Person gate.** For each person and each day they are employed inside the period, in date order, stream `active|<person_id>` gives one draw: `bern(p)`.
- `p = min(1, p_active × weekend_factor (Saturday and Sunday only) × holiday factor (6.2))`, computed in `Decimal`.
- The draw is made on every employed day, whether or not the person turns out active.

**Product gates.** On an active day, for each product in the order `chat`, `cowork`, `office_agent`, `claude_code`, `copilot`, the product is tried if it is in the person's product list that day (5.2).
- The product's stream gives its gate draw first: `bern(products.<product>)`.
- If the gate passes, the volume draws follow.
- A product stream is read only when its product is tried.

### 5.6 Anthropic activity draws

These continue the product stream after step 5 of 5.4. All ratios are fixed (3.6). Each product gets one record per day; `office_agent` is attributed to one app per day.

**chat.**
- Draws, in order: `conversations = max(1, stoch_round(requests / 5))`; `artifacts = stoch_round(conversations × 0.2)`; `skills = stoch_round(conversations × 0.1)`; `connectors = stoch_round(conversations × 0.1)`.
- Record (product `chat`): `messages` = requests, `sessions` 0, other core counters 0. Counters: `conversations`, `artifacts`, `skills_used`, `connectors_used`; `web_searches` 0.

**cowork.**
- Draws, in order: `sessions = max(1, stoch_round(requests / 6))`; `artifacts = stoch_round(sessions × 0.5)`; `skills = stoch_round(sessions × 0.3)`; `connectors = stoch_round(sessions × 0.2)`.
- Record (product `cowork`): `sessions`, `messages` = requests, other core counters 0. Counters: `artifacts`, `skills_used`, `connectors_used`; `conversations` and `web_searches` 0.

**office_agent.**
- Draws, in order: `app = pick(["excel", "word", "powerpoint", "outlook"], ["0.35", "0.30", "0.15", "0.20"])`; `sessions = max(1, stoch_round(requests / 5))`.
- Record (product `office_agent.<app>`): `sessions`, `messages` = requests, other core counters 0. Counters all 0.

**claude_code.**
- Draws, in order: `sessions = max(1, stoch_round(requests / 40))`; `x = lognorm("4", "0.5")`; `lines_added = stoch_round(requests * x)` (float); `lines_removed = stoch_round(lines_added × 0.3)`; `edits_accepted = stoch_round(requests × 0.25)`; `edits_rejected = stoch_round(edits_accepted × 0.1)`; `commits = stoch_round(sessions × 0.8)`; `pull_requests = stoch_round(commits × 0.3)`.
- Record (product `claude_code`): those eight core counters, with `messages` 0. Counters all 0.

**all.** A person's `all` record on a day has `counters.web_searches` = the day's sum of `web`. It is written only when that sum is above 0, and all its core counters are 0.

### 5.7 Copilot draws (stream `use|<person_id>|copilot`)

After the gate (5.5), in this order, with the medians of the table below:

1. `shown = count(shown_median, "0.6", 1)`
2. `accepted = stoch_round(shown × 0.28)`
3. `completion_lines = stoch_round(accepted × 2.5)`
4. `ask = count(ask_median, "0.7", 0)`
5. `agent = count(agent_median, "0.8", 0)`
6. `agent_lines = stoch_round(agent × 15)`
7. `agent_deleted = stoch_round(agent_lines × 0.3)`
8. `cli = bern("0.25")`; if `cli`: `cli_prompts = count(cli_median, "0.6", 1)`, `cli_in = count("4000", "0.4", 1)`, `cli_out = count("600", "0.5", 1)`
9. `app = bern("0.10")`; if `app`: `app_prompts = count(app_median, "0.6", 1)`, `app_in = count("3000", "0.4", 1)`, `app_out = count("500", "0.5", 1)`
10. `model = pick(["model-b-light", "model-a-standard", "model-b-premium"], model_weights)`
11. `custom_agents = uniform_int(0, 2)`, `mcp = uniform_int(0, 3)`, `plugins = uniform_int(0, 1)`, `skills = uniform_int(0, 2)`, `slash = uniform_int(0, 3)`

| `behaviour.copilot.<profile>` | `shown_median` | `ask_median` | `agent_median` | `cli_median` | `app_median` | `model_weights` (light, standard, premium) |
|---|---|---|---|---|---|---|
| developer | `80` | `4` | `3` | `6` | `3` | `0.3`, `0.5`, `0.2` |
| power_user | `240` | `12` | `9` | `18` | `9` | `0.1`, `0.4`, `0.5` |

**Derived values (no draws).**
- `credits = (ask + agent + cli_prompts + app_prompts) × credits_per_interaction[model]`, a `Decimal` (8.1). The prompts of an unused surface count 0.
- **Feature records.** Fields not listed are 0 among the five integer fields of 4.2, and counters not listed are 0.

| Feature | Written | Fields | Counters |
|---|---|---|---|
| `code_completion` | always | `suggestions_shown` = shown; `suggestions_accepted` = accepted; `lines_added` = completion_lines | `lines_suggested_to_add` = shown × 3 |
| `chat_panel_ask_mode` | when ask > 0 | `messages` = ask | none |
| `chat_panel_agent_mode` | when agent > 0 | `messages` = agent; `lines_added` = agent_lines; `lines_removed` = agent_deleted | `lines_suggested_to_add` = agent_lines; `lines_suggested_to_delete` = agent_deleted |
| `copilot_cli` | when `cli` | `messages` = cli_prompts; `sessions` = `(cli_prompts + 2) // 3`; `requests` = cli_prompts × 2 | none |
| `copilot_app` | when `app` | the same as `copilot_cli`, from app_prompts | none |

- **CLI tokens record.** `requests` = cli_prompts × 2, `tokens_input_total` = requests × cli_in, `tokens_output` = requests × cli_out. The app record is built the same way from app_prompts, app_in and app_out.
- **The `copilot` record's counters.** `custom_agents_used`, `mcp_servers_used`, `plugins_used`, `skills_used` and `slash_commands_used` take the values of step 11.

### 5.8 Drift

On the first day of every month after the first (6.1), stream `drift|<person_id>` gives one draw for each person employed that day, in person-id order.
- The draw is `pick` over the outcomes of `behaviour.drift.<current profile>`, in the order listed below, with "stay" last.
- "Stay" has weight 1 − Σ of the others, computed in `Decimal`. A profile with no outcomes still draws (only "stay" can come out).
- A move to `developer` is possible only for people whose function is in `developer_functions`. For others, its weight is added to "stay".
- The new profile is valid from that day.

| From | To, monthly probability, in this order |
|---|---|
| dormant | (none) |
| curious | office_worker `"0.04"`, developer `"0.01"`, dormant `"0.02"` |
| office_worker | dormant `"0.01"` |
| developer | dormant `"0.005"` |
| power_user | (none) |

### 5.9 The ledger

Every simulated use is one ledger row. There are three kinds:

| Kind | What one row holds |
|---|---|
| Usage | provider, day, actor (person id or service account id), product, model, requests, the token buckets, web searches, credits, failed requests, and a tag (`base`, `runaway` or `correction`) |
| Activity | provider, day, person, activity product, counters |
| Seat | provider, person, seat start, seat end, `pending_cancellation_date` |

**Silver** is rendered from the ledger: rows that share a record key are summed into one record. The **ground-truth totals** are computed from the ledger by their own aggregation (11.3), never by reading silver.

## 6. Period, calendar and pulls

### 6.1 Period and months

- **`start`.** Defaults to `2026-04-01`.
- **`months`.** 1-24, default 6.
- **`end`.** Let (y, m) be the month of `start` plus `months` months. `end` = `date(y, m, min(start.day, days in that month))` − 1 day. With the defaults, `end` is `2026-09-30`.
- **Months of the period.** These are the calendar months that intersect [`start`, `end`], numbered from 0. Month k's **range** is [max(its first day, `start`), min(its last day, `end`)].
- **Dates.** Every drawn or default date inside a month is taken from that month's range, and draws over "the days of the month" use the range's length.

### 6.2 Calendar

**Weekends.** The weekend factor (5.2) applies on Saturday and Sunday. Service accounts are not affected.

**Holidays.** `calendar.holidays` is a list of `{"name", "from": "MM-DD", "to": "MM-DD", "factor"}`, recurring every year. A range with `from` after `to` spans the new year. A day inside several ranges multiplies their factors. Service accounts are not affected.

The default is neutral, with no country's public holidays:

```json
[{"name": "year-end break", "from": "12-24", "to": "01-01", "factor": "0.2"},
 {"name": "summer dip", "from": "07-15", "to": "08-15", "factor": "0.7"}]
```

### 6.3 Pulls and batches

All times are UTC. `P` = `end` + `pull.lag_days` days. `C` = `P` + `events.late_correction.after_days` days, and `after_days` must be ≥ 1.

| Batch | `pulled_at` | Records |
|---|---|---|
| Anthropic main | `P` 06:00:00 | every Anthropic record of the period |
| Copilot main | `P` 06:10:00 | every Copilot record of the period except seat assignments |
| Copilot seat list of day d | d 06:10:00, for each day d of the period | the `SeatAssignment`s of day d |
| LiteLLM main | `P` 06:20:00 | every LiteLLM record of the period |
| Copilot correction | `C` 06:10:00 | 7.4 (only when the correction is applied) |

**Files.** Each batch writes one file per kind that has records, at `records/<kind>/<batch_id>.jsonl`. The staggered times keep the batch ids of different providers distinct.

**State at `P`.** The main batches show the state a full re-read at `P` would give:
- Copilot active counts are filled.
- `is_final` is computed at `P`, so the last days are not final. For Anthropic and for Copilot billing, the last month is not final either.

## 7. Events and their order of application

Each event has `enabled` and is written to the ground truth (11.3) with an id, a type, dates, actors and details. The details include `applied`, which is false when the event is enabled but has nothing to act on. A disabled event is not written.

**Order of application:**
1. Build the units and the initial population (3.3), names (9.3) and attributes (9.1).
2. Assign initial profiles (5.3). Grant seats (4.1, 4.2) and draw the Copilot seat ages (stream `seats`).
3. For each month k of the period, in order, with day `f` = the first day of its range:
   - a. drift on `f`, from month 1 on (5.8);
   - b. the reorganisation, if `k` is its month (7.2), effective on `f`;
   - c. leavers (7.1);
   - d. joiners (7.1);
   - e. if `k` is the forced month, the forced leaver and joiner (7.1 step 5).
4. Generate the base ledger day by day: for each day, people in id order, then service accounts in id order (5.5-5.7, 9.1).
5. Add the runaway rows (7.3).
6. Price every usage row with the price table and the price change (7.5, 8.2).
7. Run Copilot billing: monthly pool, discounts, overage (8.3).
8. Render the main batches and seat lists (4, 6.3) and validate them (4.5).
9. Apply the late correction (7.4): change the ledger, re-price, re-bill, render and validate the correction batch.
10. Write the ground truth and the manifest (11.3, 11.4).

### 7.1 Joiners and leavers (stream `event|joiners_leavers`)

For month k, let `N` be the people employed on `f`. Draws happen in this order.

1. **Counts.** `L = stoch_round(N × leaver_rate)` (the product in `Decimal`), then `J = stoch_round(N × joiner_rate)`.
2. **Leavers.** The candidates are the people employed on `f` who head no unit on `f` (after step 3b) and are not already leaving, in person-id order. For each of the `L` leavers:
   - if the candidate list is empty, stop;
   - `i = uniform_int(0, len − 1)` picks one candidate and removes it from the list;
   - the last day is the range's first day + `uniform_int(0, range length − 1)`.
3. **Joiners.** For each of the `J` joiners, in this order:
   - the start date is the range's first day + `uniform_int(0, range length − 1)`;
   - the team is a `pick` over the team units of the line dimension valid on the start date, in unit-id order, weighted by their headcount that day (people employed that day by the decisions taken so far);
   - `population` is `contractor` if `bern(contractor_share)`, else `employee`;
   - the profile is a `pick` over the five profiles in canonical order, weighted by the number of people holding each profile on `start` among people with the team's function. If that total is 0, the weights are the 5.3 targets.

   The joiner then gets the next person id, level `L5`, the team lead as manager, and the team's function and cost centre. Its name comes from stream `names` (9.3) and its location from stream `attrs` (9.1).
4. **Invites.** A joiner is invited `invite_lead_days` before the start date.
5. **`min_per_period` 1.** The forced month is `floor((months − 1) / 2)`. In that month, after step d, the generator forces events if needed:
   - **Forced leaver.** If no leaver has been drawn in this or any earlier month, one is forced. Candidates are taken as in step 2, on the range's first day, and the first candidate in person-id order leaves.
   - **Forced joiner.** Then, if no joiner has been drawn so far, one is forced. It goes to the first team valid on the forced day, in unit-id order. It draws `bern(contractor_share)`, then the profile `pick` (as in step 3) from `event|joiners_leavers`, then its name and location.
   - **The forced day.** It is the 15th of the forced month, clamped to the month's range. It is the leaver's last day and the joiner's start date.

   Because this happens inside the month loop, later months (drift, the reorganisation) see the forced people. A forced event is a normal event in the ground truth.

**Consequences.**
- **Anthropic.** The leaver's Anthropic seat ends after their last day.
- **Copilot.** A leaver's Copilot seat gets `pending_cancellation_date` = the first day of the month after their last day. The date is visible from the last day on (4.2), and the seat stays listed until the day before it.
- **Joiners.** A joiner whose team is granted (4.2) gets a Copilot seat from the start date.

### 7.2 Reorganisation: a unit split in two

**When and what.**
- The month is `month_index`; the default is `floor(months / 2)`. The effective day `f` is the first day of that month's range.
- The split unit is `unit`, a line unit key, set by each preset.
- `unit` must be a `department` or a `team`; any other kind is an error (exit 2).
- **Members** of a unit, in this section, are the people employed on `f` whose line unit on the day before `f` was that unit. People whose last day is the day before `f` are never members, heads or new leads.
- The split is applied only if `months` ≥ 2, `unit` is not `null`, and either:
  - the unit has at least 2 child units, at least one of which (from the half moved to `-b`) has at least 2 members; or
  - it has no child units and at least 2 members besides its head.

  Otherwise it is recorded with `applied: false`.

**The split.** On `f`, the unit ends: its `valid_to` is the day before. Two new units start:
- keys `<key>-a` and `<key>-b`, names `<name> A` and `<name> B`;
- the same kind, function and parent as the old unit;
- new unit ids, `-a` first;
- cost centres: `-a` keeps the old one, `-b` takes the next free number (3.3).

**A unit with child units.**
- The first `ceil(n / 2)` children, in unit-id order, move under `-a`; the rest move under `-b`.
- Moved children keep their unit ids and get a new unit row from `f` with the new parent and the new parent's cost centre.
- The old head heads `-a`.
- `-b` is headed by the lead of the first child moved to `-b` that has at least 2 members. That person moves to the `-b` unit with level `L3`. The next member (as defined above) of their old team by person id becomes that team's lead (`L4`).

**A unit without child units.**
- Its members (as defined above), head excluded, are split in person-id order: the first `ceil(n / 2)` go to `-a` with the old head, the rest to `-b`.
- The first member moved to `-b` becomes its head, with the old head's level.

**Rows and trace.**
- Every person whose unit, manager, level or cost centre changes gets a new person row from `f` (11.1).
- No provider sees the organisation, so the reorganisation leaves no trace in silver. Its trace is in the organisation files and the ground truth (S08; 16.3).

### 7.3 Runaway agent

**When.**
- The start day is `start`, if set. Otherwise it is the first Monday-to-Friday day on or after `start` + `floor(0.6 × days in the period)` days.
- The runaway days are `days` consecutive calendar days from the start day, cut at `end`.

**Target.**
- Candidates are the people who hold `developer` on the start day and are employed on every runaway day, in person-id order.
- The candidates are shuffled with stream `event|runaway`, and the first one is the target.
- With no candidate, the event has `applied: false`.

**What it adds.** On each runaway day, one ledger row is added for the target, tagged `runaway`:
- product `claude_code`, the event's model;
- `requests` = (`multiplier` − 1) × `behaviour.volume.developer.claude_code.req_median`;
- tokens by 5.4, with `in_per_req` = `in_median` and `out_per_req` = `out_median` of `claude_code`, computed in `Decimal` (no draws).

It adds no activity. If the target also used that model that day, the two rows render into one record.

### 7.4 Late provider correction (Copilot)

**Window.** The days from `end − window_end_offset_days − window_days + 1` to `end − window_end_offset_days`, cut to the period.

**Selection.**
- Candidates are the window's Copilot `base` ledger rows with credits > 0, sorted by (day, person id).
- Each candidate is selected if `bern(share)`, drawn from stream `event|correction`. If none is selected, the first candidate is.
- With no candidates, or with Copilot disabled, the event has `applied: false` and no correction batch is written.

**The change.** A selected row's credits become `(credits × factor).quantize(Decimal("0.1"), ROUND_HALF_UP)`, and its tag becomes `correction`. The ground truth keeps the old value. With the defaults, every corrected day was already final at `P` (4.6): the correction changes facts that were marked final, which is the case the gold layer must catch.

**The correction batch.**
- It re-renders every Copilot record of the period, except seat assignments, at `pulled_at` = `C` 06:10:00. Billing is re-run, because the pool runs out earlier.
- It writes only the records whose content differs from the latest earlier record of the same key, comparing every field except `pulled_at`, `data_refreshed_at` and `batch_id`.
- It therefore holds:
  - the corrected days;
  - the billing days whose discount moved;
  - the month records of the affected month;
  - every record whose `is_final` turned true between `P` and `C`.

### 7.5 Price change mid-month

**When.** The date is `date`, if set. Otherwise it is the 15th of month `floor((months − 1) / 2)`, clamped to that month's range.

**The change.**
- From the date (00:00 UTC), each of the five per-million prices of (`provider`, `model`) becomes `(price × factor).quantize(Decimal("0.0001"), ROUND_HALF_EVEN)`.
- Usage rows on and after the date are priced with the new version (8.2).
- The price table file is not changed; the event adds a version in memory and records both versions in the ground truth.
- If the provider is disabled or the model has no price entry, the event has `applied: false`.

## 8. Prices and contracts

### 8.1 Price table format

The bundled table is `data/prices.json`. It is editable, and `price_table` may point to another file.

```json
{
  "price_table_format": 1,
  "label": "Invented example values, except credit_price, which mirrors a constant of the Copilot connector. Not any provider's price list or any organisation's contract.",
  "currency": "USD",
  "token_prices": [
    {"provider": "anthropic", "model": "model-a-light",    "valid_from": "2000-01-01", "per_million":
      {"input_uncached": "0.70", "cache_read": "0.07", "cache_write_5m": "0.90", "cache_write_1h": "1.40", "output": "3.50"}},
    {"provider": "anthropic", "model": "model-a-standard", "valid_from": "2000-01-01", "per_million":
      {"input_uncached": "2.50", "cache_read": "0.25", "cache_write_5m": "3.10", "cache_write_1h": "5.00", "output": "12.50"}},
    {"provider": "anthropic", "model": "model-a-premium",  "valid_from": "2000-01-01", "per_million":
      {"input_uncached": "12.00", "cache_read": "1.20", "cache_write_5m": "15.00", "cache_write_1h": "24.00", "output": "60.00"}},
    {"provider": "litellm", "model": "model-a-standard",   "valid_from": "2000-01-01", "per_million":
      {"input_uncached": "2.60", "cache_read": "0.26", "cache_write_5m": "3.20", "cache_write_1h": "3.20", "output": "13.00"}},
    {"provider": "litellm", "model": "model-b-light",      "valid_from": "2000-01-01", "per_million":
      {"input_uncached": "0.22", "cache_read": "0.06", "cache_write_5m": "0.27", "cache_write_1h": "0.27", "output": "0.88"}},
    {"provider": "litellm", "model": "selfhosted-c-small", "valid_from": "2000-01-01", "per_million": null}
  ],
  "claude_enterprise": {"seat_per_month": "27.00", "usage_discount": "0"},
  "github_copilot": {
    "credit_price": "0.01",
    "plans": {"business": {"seat_per_month": "23.00", "included_credits_per_seat": "1500"}},
    "credits_per_interaction": {"model-b-light": "2.0", "model-a-standard": "8.0", "model-b-premium": "25.0"},
    "budget_usd": null
  }
}
```

**Rules.**
- Prices are decimal strings with at most 4 decimals. `per_million: null` means unpriced.
- `credit_price` must be `"0.01"`, because it is the connector's constant (analysis F1). A table that sets another value is rejected (exit 2).
- `credits_per_interaction` values have at most one decimal.
- `usage_discount` must be `"0"` in v1, because D8 says no discount.
- `providers.github_copilot.included_credits_per_seat`, when set, overrides the plan's value.

### 8.2 Pricing a usage row

**Price version.** A row is priced with the version valid on its day: the entry with the latest `valid_from` on or before that day. The price change (7.5) inserts one entry with `valid_from` = its date, whose prices are those of the version valid on that date × `factor`. A later entry in the table still wins from its own `valid_from`. On a tie of `valid_from`, the inserted entry wins.

**Amount.** In `Decimal`, computed from the row's token buckets and the version's per-million prices:

```
amount = (uncached × input_uncached
          + cache_read × cache_read
          + write_5m × cache_write_5m
          + write_1h × cache_write_1h
          + output × output) / 1,000,000
```

It is quantized once per ledger row to 8 decimals (`Decimal("0.00000001")`, `ROUND_HALF_EVEN`).

- **LiteLLM rows** have no 5m/1h split, so their `cache_write` is priced at `cache_write_5m`.
- **Record amounts and total amounts** are sums of row amounts.
- **Anthropic:** `cost` = `cost_list` = amount (the discount is 0).
- **LiteLLM:** `cost` = amount, and `cost_list` is null.

### 8.3 Copilot pool, discounts and overage

**Pool.** For month k: `seats_billed` = the distinct people holding a Copilot seat on any day of month k's range [A: no proration, analysis F1]. `pool` = `seats_billed` × `included_credits_per_seat`.

**Organization discount.**
- Walk the range's days in order, and within a day the models in sorted order.
- For each (day, model) with gross credits `g`: `discount_q = min(g, pool_left)`, then `pool_left −= discount_q`, and `net_q = g − discount_q`.
- `OrgTotal.extras`: `discount_quantity` = `discount_q`, `net_quantity` = `net_q`, `net_amount` = `net_q` × 0.01, `price_per_unit` = 0.01.
- `cost` = `net_amount`.

**Person discount (month records).**
- Each person who held a seat in the month has a full allowance of `included_credits_per_seat`, even if they joined mid-month.
- Their month's gross per model, in sorted model order, uses the allowance up: `discount_q = min(g, allowance_left)`.
- This is an invented display rule; the billing API does not define it (analysis F1).
- The sum of person discounts is not the organization's discount. That gap is the point of `cost_basis` `consumption` on person records.

**Overage and budget.**
- The **overage** of a month is the sum of the organization's net amounts.
- **Budget.** When `budget_usd` is set, the ground truth records the first day on which the month's cumulative overage exceeded it. Usage is not stopped; stopping is the deferred "budget reached" event (16.5).

### 8.4 Seat fees (ground truth only)

- **Claude Enterprise.** Per month: `seats_billed` × `seat_per_month`, where `seats_billed` = the distinct people holding a seat on any day of the month's range.
- **Copilot.** The same, with the plan's price.

No silver record carries a seat fee, because no endpoint the connectors read has one (analysis F2).

## 9. Determinism

### 9.1 Streams

**Derivation.**

```
stream(name) = random.Random(int.from_bytes(
    hashlib.sha256(("meterline-sim/1|%d|%s" % (seed, name)).encode("utf-8")).digest()[:8], "big"))
```

**Names in use.** `org`, `names`, `profiles`, `seats`, `attrs`, `active|<person_id>`, `use|<person_id>|<product>`, `use|<service_account_id>`, `drift|<person_id>`, `event|joiners_leavers`, `event|runaway`, `event|correction`.

**Adding a feature.** A new feature takes a new stream name and never reads an existing stream. This keeps the rest of the output unchanged when a feature is added (S03).

**`attrs`.**
- For each person of the initial population in creation order: `population` (`bern(contractor_share)`), then `location = pick(locations.values, locations.weights)`.
- A joiner draws `population` in 7.1. It then draws only its `location`, from `attrs`, at the moment it is created.

**Service accounts.** Stream `use|<sa_id>`, every day of the period in date order (weekends and holidays included). Per day:
- `requests = count(req_median, req_sigma, 1)`;
- `in_per_req = lognorm(in_median, "0.4")`;
- `out_per_req = lognorm(out_median, "0.5")`;
- `failed = stoch_round(requests × failure_rate)`.

Tokens follow 5.4 with `f_1h` 0, and `successful_requests` = requests − failed.

### 9.2 Rules

1. **Same bytes.** The same preset file, price table, seed and parameters give the same bytes in every output file, on any platform, with any Python 3.10+. The kit avoids platform `log`, `exp` and `cos` (5.1), and `random.Random(int).random()` is stable across Python 3 versions [I].
2. **No wall clock.** No `datetime.now`, `datetime.utcnow`, `date.today`, `time.time`, `uuid`, `os.urandom`, `secrets` or `random.SystemRandom` anywhere in the package. Every date comes from the period and the pull plan.
3. **No unordered iteration.** No loop over a `set`, nor over a `dict` whose order came from anything other than sorted or listed order. Every loop runs over a sorted list or a list in a documented order.
4. **No module-level `random`.** No module-level `random` function; only `random.Random` instances from `stream()`.
5. **Output formats.**
   - JSONL through `meterline.write_jsonl`.
   - CSV through `csv.writer` with `lineterminator="\n"`, UTF-8 without BOM, `QUOTE_MINIMAL`. Null is an empty field, and rows follow the order of 11.1.
   - JSON files through `json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)` plus a final newline. Money is written with `meterline.amount_str` and quantities, credits and the scale with `meterline.quantity_str`, both as strings. Dates are `YYYY-MM-DD` and datetimes ISO with `+00:00`.
6. **Hashes.** A hash of an object is `sha256` of its canonical form: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.

### 9.3 Names

The name lists are generated, so every builder has the same ones. They are built from a short set of syllables, chosen so that the words rarely spell English words and never the sensitive ones a scan found with a wider alphabet. Letters such as `k`, `n`, `p`, `g` and `f` are left out, because the slurs and sensitive words that a wider alphabet produced (round 1 of the review) need them.

```
S = ["ba","be","da","de","la","le","li","lo","ma","me","mi","mo","re","ri","ro",
     "sa","ta","te","to","va","ve","vo","za","zo"]
given  = [a + b     for a in S for b in S]              (576 words, "baba" ... "zozo")
family = [a + b + c for a in S for b in S for c in S]   (13,824 words, "bababa" ... "zozozo")
```

- **`data/names.json`.** It holds `{"given": given, "family": family}` in exactly this order, with a `"note"` saying the words are generated nonsense syllables. Check S12 regenerates the lists and compares them with the file.
- **Drawing.** For each person in creation order (joiners when created), stream `names` draws `given[uniform_int(0, len − 1)]` and then `family[uniform_int(0, len − 1)]`. A pair already used is drawn again; after 1,000 tries the run exits 1.
- **Forms.**
  - Display: each word with its first letter in upper case.
  - Email: `<given>.<family>@<email_domain>`.
  - Login: `<given>-<family>`.

## 10. Generation pipeline

```
defaults + preset + --params + price table + seed + months + start + scale
  └─ resolve and validate (3, 8.1); scale (3.3)
      └─ organisation: units → people → names, logins, emails, attributes → seats (3.3, 9.1, 9.3)
          └─ profiles (5.3); month by month: drift, reorganisation, leavers, joiners (5.8, 7.2, 7.1); forced events
              └─ ledger day by day: people, then service accounts (5.5-5.7, 9.1); runaway rows (7.3)
                  └─ pricing (8.2, 7.5); Copilot billing (8.3)
                      └─ render main batches + seat lists (4, 6.3) → validate (4.5)
                          └─ late correction (7.4) → render correction batch → validate
                              └─ ground truth from the ledger (11.3) → manifest (11.4) → write (11.5)
```

## 11. Outputs

The output folder is `<out>/<name>-s<seed>-m<months>/`, where `<name>` is the preset's `name` and `--out` defaults to `./sim-out`. If the folder exists and is not empty, the run exits 2 and writes nothing. Runs that differ only in `start`, `scale` or parameters need different `--out` folders.

### 11.1 Organisation files (`org/`)

| File | Columns, in order | Rows sorted by |
|---|---|---|
| `units.csv` | `unit_id`, `valid_from`, `valid_to`, `dimension`, `key`, `name`, `kind`, `parent_id`, `function`, `cost_center`, `head_person_id` | `unit_id`, `valid_from` |
| `people.csv` | `person_id`, `valid_from`, `valid_to`, `unit_id`, `manager_id`, `function`, `level`, `cost_center`, `population`, `attrs` | `person_id`, `valid_from` |
| `memberships.csv` | `person_id`, `unit_id`, `dimension`, `role`, `valid_from`, `valid_to` | `person_id`, `unit_id`, `valid_from` |
| `identities.csv` | `person_id`, `provider`, `method`, `actor_kind`, `actor_id`, `actor_login`, `actor_email`, `valid_from`, `valid_to` | `person_id`, `provider`, `valid_from` |
| `directory.csv` | `person_id`, `given_name`, `family_name`, `email` | `person_id` |

**Validity.**
- `valid_from` and `valid_to` are inclusive dates.
- The first row of a person or unit present on `start` has `valid_from` = `start`. A joiner's first row starts on their start date.
- `valid_to` is empty while the row runs past `end`. A person's last row ends on their last day.
- A new row starts whenever any column but the two dates changes.

**Columns.**
- `attrs` is a JSON object with sorted keys and no spaces: `{"location":"site-b"}`.
- `memberships.csv` holds matrix memberships only (3.4); the line unit is in `people.unit_id`. The file is written with its header even when empty.

**`identities.csv`.** One row per person and provider account.
- **Anthropic:** `method` `email`, `actor_kind` `user`, `actor_login` empty, valid over the person's employment.
- **Copilot:** `method` `manual` (a hand-kept login list), `actor_kind` `user`, `actor_email` empty, valid over the seat.
- Service accounts are not in this file, because their traffic is `unassigned` in silver; they are in the ground truth.

**`directory.csv`** keeps names and emails apart from `people.csv` (9.3).

### 11.2 Silver records

The files are `records/<kind>/<batch_id>.jsonl`, with kinds `usage`, `activity`, `seats`, `seat_assignments` and `org_totals`, written by `meterline.write_jsonl`. One file holds the records of one provider's batch (6.3). No Parquet is written.

### 11.3 Ground truth (`ground_truth.json`)

```
{
  "ground_truth_format": 1,
  "generator": {"name": "meterline-sim", "version": "1.0.0"},
  "preset": "<name>", "seed": <int>, "months": <int>, "scale": "<decimal>", "start": "YYYY-MM-DD",
  "period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "parameters": { the resolved parameter object (3.6), as resolved },
  "price_table_sha256": "<hex, as in 11.4>",
  "pulls": [{"provider", "purpose": "main" | "seat_list" | "correction", "pulled_at", "batch_id"}],
  "people": [{"person_id", "start", "last_day",
              "profiles": [{"profile", "from", "to"}],
              "seats": [{"provider", "from", "to", "pending_cancellation_date"}]}],
  "service_accounts": [{"id", "model", "priced"}],
  "events": [{"event_id", "type", "dates", "actors", "details"}],
  "totals": [{"kind", "provider", "cost_basis", "grain", "month",
              "cost", "cost_list", "tokens_input_uncached", "tokens_cache_read", "tokens_cache_write",
              "tokens_input_total", "tokens_output", "requests", "quantities"}],
  "totals_before_correction": [same shape],
  "contracts": [{"provider", "month", "seats_billed", "seat_fees", "pool", "gross_credits",
                 "discount_credits", "overage_amount", "budget_crossed_on"}]
}
```

**Ordering.**

| List | Sorted by |
|---|---|
| `pulls` | `batch_id` |
| `people` | `person_id` |
| a person's `profiles` | `from` |
| a person's `seats` | (`provider`, `from`) |
| `service_accounts` | `id` |
| `events` | (`dates[0]` or `""`, `type`, `actors[0]` or `""`) |
| `totals` | (`kind`, `provider`, `cost_basis` with null first, `grain`, `month`) |
| `contracts` | (`provider`, `month`) |

- `start` is the person's start date, or `start` for the initial population.
- `last_day` and `to` are null when they run past `end`.

**Totals.**
- `totals` are the expected sums over the latest record per key of kinds `usage` and `org_totals`, grouped by (kind, provider, `cost_basis`, `grain`, month of `period_start` as `YYYY-MM`).
- A field is the sum of its non-null values, or null when all are null. `quantities` maps each unit to its sum.
- They are computed from the ledger by applying the rendering rules to sums, never by reading silver.
- `totals_before_correction` is the same for the Copilot provider only, at the main pull.

**Contracts.** One row per provider and month. For Anthropic, `pool`, `gross_credits`, `discount_credits`, `overage_amount` and `budget_crossed_on` are null.

**Events.**
- Ids are `E001`, `E002`, ... in sorted order.
- `dates` are `YYYY-MM-DD` strings and `actors` are person ids, or provider names for a price change.
- `details`, per type, always include `applied`. When `applied` is false, every other detail field that has no value is null. When `dates` is empty, its sort key is `""`.

| Type | `dates` | `actors` | `details` |
|---|---|---|---|
| `joiner` | [start date] | [person] | `applied`, `unit_id`, `profile`, `population`, `invite_date`, `seats` (providers) |
| `leaver` | [last day] | [person] | `applied`, `unit_id`, `profile`, `seats` |
| `reorganisation` | [f] | sorted ids of people with a new row on f | `applied`, `old_unit_id`, `new_unit_ids`, `moved_units`, `new_heads` (unit id → person id) |
| `runaway_agent` | the runaway days | [person] | `applied`, `product`, `model`, `added_requests_per_day`, `added_tokens_per_day` (bucket → int), `added_cost_list_per_day` (list), `base_requests_per_day` (the target's `claude_code` requests over all models that day without the runaway, list) |
| `late_correction` | the window days | sorted ids of affected people | `applied`, `provider`, `correction_batch_id`, `rows` (list of `{day, person_id, model, credits_before, credits_after}`, sorted by day, person) |
| `price_change` | [date] | [provider] | `applied`, `provider`, `model`, `prices_before`, `prices_after` |

The ground truth holds no email and no name; those are in `directory.csv`.

### 11.4 Manifest (`manifest.json`)

```
{"generator", "version", "preset", "seed", "months", "start", "scale",
 "preset_sha256", "parameters_sha256", "price_table_sha256",
 "files": [{"path", "bytes", "sha256"}]}
```

- `preset_sha256` hashes the preset file's parsed object and `parameters_sha256` the resolved parameters, both by 9.2 rule 6. `price_table_sha256` hashes the parsed price-table file by the same rule, without the price change's inserted entry.
- `files` are sorted by path. The manifest itself is excluded.
- The manifest has no timestamp.

### 11.5 Writing

Everything is generated and validated in memory. It is then written to `<folder>.tmp` and renamed to `<folder>` at the end. A failed run leaves no output folder.

## 12. CLI

```
meterline-sim generate --preset NAME --seed N --months N [--start YYYY-MM-DD] [--scale S] [--out DIR] [--params FILE]
```

**Arguments.**
- `--preset`: `vertical`, `tiny`, or a path to a preset file.
- `--seed`: an integer from 0 to 2^63 − 1.
- `--months`: 1-24, default 6.
- `--start`: default `2026-04-01`.
- `--scale`: a decimal > 0, default 1.
- `--params`: a JSON file of parameter overrides (3.1), for example `{"events": {"runaway_agent": {"enabled": false}}}`.

**Exit codes.**
- **0:** written. The command prints the output folder and record counts per kind.
- **2:** bad arguments, an invalid preset, parameters or price table, or an output folder that is not empty. The command prints a one-line reason.
- **1:** a validation failure inside the run (4.5) or a name exhaustion (9.3). Nothing is written.

**Library entry points.**
- `cli.main(argv=None)` returns the exit code.
- `meterline_sim.verify(folder) -> list[str]` runs the invariants V1-V8 of section 15 against an output folder and returns the failures, an empty list when all hold. It reads only the folder. v1 has no CLI command for it.

## 13. Out of scope

**Simulator items:**
- "Deferred: sim replay server emitting raw API responses"
- "Deferred: sim live day-by-day mode"
- "Deferred: sim presets squads and mixed"
- "Deferred: sim remaining profiles and model-preference axis"
- "Deferred: sim remaining events"
- "Deferred: sim conflicting team claim and AWS bill gap"
- "Deferred: sim other Claude contract structures"

**The gold layer and its deferred parts:**
- "Deferred: gold engagement levels (A4)"
- "Deferred: gold FOCUS export (A9)"
- "Deferred: gold recommendations engine (C)"
- "Deferred: gold enforcement mode for breaker and alerts"
- "Deferred: SCIM 2.0 input adapter for people and units"
- "Deferred: delivery recipes and sender plugins"
- "Deferred: gold session telemetry and value metrics"
- "Deferred: pre-publication checks"

**Also not in v1, with no row:** Parquet output, a `verify` CLI command, enterprise-level Copilot, `cost_only` records, and unresolved identities.

## 14. Phases

| Phase | Builds | Done-checks |
|---|---|---|
| 1 | The package, parameters, presets, organisation, profiles, calendar, the Anthropic and LiteLLM silver, joiners and leavers, the reorganisation, the runaway agent, the price change, the ground truth, the manifest, the CLI and the determinism rules | S01-S05, S07, S08, S10, S11, S12, S13, S14, S15, S16, with Copilot disabled (`providers.github_copilot.enabled: false`) |
| 2 | Copilot seats, credits, activity, billing and pool, the late correction, and every check with Copilot enabled | S06, S09, and all phase 1 checks rerun with the defaults |

Each phase ends with every check of that phase passing. Phase 2 adds Copilot to the shared checks without weakening their phase 1 assertions.

## 15. Done-checks

**How the checks run.**
- Each check is a pytest test named `test_done_check_S<NN>_*` in `packages/meterline-sim/tests/`.
- Each runs on simulator output only, which is synthetic by construction, and is deterministic: fixed seeds, and Hypothesis with `derandomize=True`.
- Each fails before the build because `meterline_sim` does not exist (`ModuleNotFoundError`). Each also has a **negative control**: a test that applies the same assertion to a deliberately broken input and expects it to report a failure. That shows the check can fail for its own reason. The build shows each first failure once and changes no check afterwards.
- "Tiny run" means `generate(preset="tiny", seed=7, months=6)` into a temporary folder, and "vertical run" means `generate(preset="vertical", seed=7, months=6)`.

**The invariants that `verify(folder)` runs**, used by S04-S11 and S13:
- V1 the S04 checks;
- V2 S05;
- V3 S06 (when the correction is applied);
- V4 S07 without its existence clauses;
- V5 S08;
- V6 S09 without its existence clause;
- V7 S10;
- V8 S11 without its preset-specific counts.

**The checks.**

- **S01 Byte-identical rerun.**
  - Two tiny runs with seed 7 into two folders give equal sorted file lists and equal bytes for every file. The second run is in a subprocess with `PYTHONHASHSEED` set to a different value from the first.
  - Each manifest's sha256 values match the bytes on disk.
  - The same holds for one vertical pair, compared by manifest (marked `slow`; in the done gate).
  - Negative control: seeds 7 and 8 are reported as different.
- **S02 The seed matters.** Tiny runs with seeds 7 and 8 differ in `directory.csv` and in at least one `records/usage` file.
- **S03 Streams are isolated.** Records are compared by record key.
  - **Runaway on and off** (tiny, seed 7). Every file outside the Anthropic main batch is byte-identical, apart from the manifest and the ground truth. In the Anthropic main batch, every record that differs is one of:
    - the target's `claude_code` usage records of the runaway days;
    - the `OrgTotal` records of the runaway days;
    - the `SeatSnapshot` records from the first runaway day to `active_windows[1] − 1` days after the last, in their active counts only.

    The target's `claude_code` records with the event's model on the runaway days, and those days' `OrgTotal`s, do differ.
  - **Price change on and off** (tiny, seed 7). At least one record differs. Only Anthropic `usage` records of the changed model dated on or after the change date, and `org_totals` records dated on or after it, differ, and only in `cost` and `cost_list`. The manifest and the ground truth differ too.
- **S04 Every record validates (V1).** For every batch file of a tiny and a vertical run:
  - `meterline.read_jsonl` reads it with the kind's class;
  - `meterline.batch_failures(records, pulled_at date)` is `[]`;
  - for each Anthropic and LiteLLM main batch, `meterline.actor_cost_failures(usage, org_totals)` is `[]`;
  - every record's `batch_id` equals its file name's stem, and its `pulled_at` is one of the ground truth's `pulls`.
- **S05 Silver totals equal the ground truth (V2).**
  - Load every batch of each kind and take `meterline.latest_per_key`. Group usage and org totals by (kind, provider, `cost_basis`, `grain`, month) and sum them as 11.3 says.
  - The groups are the same as the ground truth's `totals`.
  - Every money field is equal after rounding both sides to the cent (`ROUND_HALF_EVEN`). Every token, request and quantity field is exactly equal.
  - Negative control: a copy of the ground truth with one cent added to one group fails.
- **S06 Late correction (V3).** In a tiny run, the correction is applied, and:
  - a. the correction batch exists, and each of its records differs from the latest earlier record of its key in at least one field other than `pulled_at`, `data_refreshed_at` and `batch_id`;
  - b. for each corrected row in the ground truth, the main batch's `copilot` day record of that person and day has `ai_credit` = the day's total with `credits_before`, and `latest_per_key` gives the total with `credits_after`;
  - c. `latest_per_key` over all batches gives `totals`, and the main batches alone give `totals_before_correction` (both as S05).
- **S07 Finality (V4).**
  - For every record with `is_final`, its value equals the 4.6 rule applied to its provider, kind, grain, `period_start` and `pulled_at`.
  - Existence clause, tiny run only: the main batches hold at least one Anthropic record and one Copilot day record that are not final, and at least one of each that is final. The Copilot part is skipped while Copilot is disabled (phase 1).
- **S08 Every event is findable (V5).** For each applied event in the ground truth:
  - **Joiner.**
    - No `grain` `day` record of the person is dated before the start date. Month records start on the 1st and are not checked.
    - On the start date, Anthropic `seats` minus the day before's equals the joiners starting that day minus the leavers whose last day was the day before. A joiner who starts on the period's first day is skipped here.
  - **Leaver.**
    - No usage or activity record of the person is dated after the last day.
    - Every Anthropic usage record of the person has `actor_deleted` true.
    - If they held a Copilot seat, their `SeatAssignment`s from the last day on carry the `pending_cancellation_date` of 7.1.
  - **Runaway agent.** On each runaway day, the sum of the target's `claude_code` `requests` over models = `base_requests_per_day` + `added_requests_per_day`, and `added_requests_per_day` = (`multiplier` − 1) × the developer `claude_code` `req_median` of the resolved parameters.
  - **Late correction.** As S06 (b).
  - **Price change.**
    - Every Anthropic usage record of the model dated on or after the date has a `cost_list` within `0.00000002` of its tokens priced at `prices_after` (8.2).
    - Every such record on the day before is within the same tolerance of its tokens priced at `prices_before`.
    - The tolerance allows for a record that merges two ledger rows, each rounded separately.
  - **Reorganisation.**
    - In `units.csv`, the old unit's last row ends on the day before `f`, and the two new units start on `f` with the old parent.
    - Every person employed on `f` whose line unit on the day before `f` was the old unit, or a unit below it, has on `f` a valid `people.csv` row whose `unit_id` is a new unit or a unit below one.
    - Headcount on `f` equals headcount on the day before, plus the joiners starting on `f`, minus the leavers whose last day was the day before.
- **S09 Copilot credits agree with billing (V6).** In a tiny run, after `latest_per_key`:
  - for each day, the sum over models of the `OrgTotal` gross `ai_credit` = the sum of that day's daily `copilot` `ai_credit`;
  - for each month and model, the sum of the `OrgTotal` gross = the sum of the month records' gross;
  - for each person and month, the month records' gross = their daily sum;
  - for each month, the sum of `OrgTotal` `discount_quantity` = `min(pool, gross)` from the ground truth's `contracts`, and the sum of net amounts = `overage_amount`;
  - every Copilot `cost_list` = gross × 0.01.

  Existence clause, tiny run only, not in V6: the tiny run has a month with overage > 0.
- **S10 Organisation totals ≥ the sum of people (V7).**
  - **Anthropic.** For each day, every field of the `OrgTotal` (`cost`, `cost_list`, each token field, `requests`) is ≥ the sum over that day's `UsageRecord`s. They are equal in v1.
  - **LiteLLM.** For each day and model, the `OrgTotal`'s fields equal the `unassigned` record's.
  - **Copilot.** For each day, the sum of the `OrgTotal` gross ≥ the sum of persons' daily `ai_credit`.
  - On every `SeatSnapshot`, `daily_active` ≤ `seats`.
  - Run on a tiny and a vertical run.
- **S11 Organisation files are consistent (V8).** For every day of the period:
  - every employed person has exactly one valid `people.csv` row;
  - its `unit_id` is a line unit valid that day;
  - its manager is employed that day, and following managers reaches the company head with no cycle;
  - every valid unit's parent is valid that day.

  Preset-specific, not in V8:
  - At the start, profile counts equal the 5.3 targets computed from the actual headcount. For 396 people these are 119 dormant, 119 curious, 119 office_worker, 31 developer and 8 power_user.
  - The tiny run has every profile at the start.
  - The vertical run has 6 divisions, 18 departments and 45 teams, 41 Copilot seat holders on `start`, and a headcount between 360 and 440 on `start`.
- **S12 Preset format.**
  - `tests/presets/matrix.json` (3.4) generates. Its `memberships.csv` has 3 rows in dimension `community`, and `units.csv` has the group with `dimension` `community`.
  - Each of the following exits 2: a preset with an unknown key; shares that do not sum to 1; a fractional JSON number; `credit_price` other than `"0.01"`; `email_domain` `"mail.com"`.
  - `--scale 2` on `vertical` gives `max(1, floor(teams × 2 + 0.5))` teams per department and the same divisions and departments.
  - `data/names.json` equals the lists regenerated by 9.3.
- **S13 Property tests.**
  - **(a)** Hypothesis, `@settings(max_examples=50, deadline=None, derandomize=True)`, `seed` from `st.integers(0, 2**31 − 1)`, the tiny preset with `months=2`: `verify(folder)` returns `[]` (V1-V8).
  - **(b)** With `max_examples=5`, the same strategy and two runs per seed: byte-identical folders.
- **S14 Speed.** A tiny run with `months=6` finishes in under 10 seconds of wall time. The test prints the time and is in the done gate, marked `timing`.
- **S15 CLI.** Run in-process through `cli.main(argv)`:
  - `generate --preset tiny --seed 1 --months 2 --out <tmp>` returns 0 and creates `<tmp>/tiny-s1-m2/` with `org/`, `records/`, `ground_truth.json` and `manifest.json`;
  - the same call again returns 2, because the folder is not empty;
  - `--preset nosuch` returns 2, and `--months 0` returns 2;
  - with `meterline_sim.render.batch_failures` patched to return `["forced"]`, the call returns 1 and leaves no output folder.
- **S16 Determinism and genericity scans.**
  - A scan of `src/meterline_sim/` finds none of the names in 9.2 rule 2, no `import faker` or `from faker`, and no module-level `random.` call other than `random.Random`.
  - `float` does not appear in `prices.py` or `billing.py`.
  - Every email in `directory.csv`, `identities.csv` and the records of a tiny and a vertical run ends in `@example.com`.
  - Every `actor_login` matches `^[a-z]+-[a-z]+$`.
  - Negative control: a fixture file containing `datetime.now` is flagged.

## 16. Open decisions for the owner

1. **Map Copilot's daily model breakdown?** The per-user daily report holds `totals_by_model_feature` and `totals_by_language_model`: chat activity counts by model, with no credits. meterline-copilot does not map them (analysis F3), so a person's Copilot model mix is monthly only, through billing.
   - Recommendation: not now. The breakdown gives counts, not cost; revisit when the gold layer needs a daily model mix.
2. **Self-hosted model on LiteLLM: null cost or `0.00`?** D3 says null cost, and the default follows it. The real connector would write `0.00` with `cost_basis` `estimate`, because LiteLLM always reports a `spend` number (analysis F4).
   - Recommendation: switch the default to `"zero"`, so the simulated silver matches what the connector writes. The gold layer then tells "free" from "unpriced" through its own model catalogue, which is the realistic case.
3. **The reorganisation is not findable in silver.** Providers do not see the organisation, so the split leaves no trace in silver; S08 checks it in the organisation files instead. This changes D12's "findable in silver" for this one event.
   - Recommendation: accept.
4. **`tiny` adapts three decisions to its size.**
   - It splits a team, because it has no department (D7 says "a department").
   - It forces at least one joiner and one leaver, because 1.5% of 20 people would often give none.
   - It lowers the included Copilot credits, so that overage occurs.

   `vertical` keeps the decisions as written.
   - Recommendation: accept.
5. **Seat fees per month without proration** (8.3, 8.4). GitHub's proration rule is unconfirmed (analysis F1), so the spec bills a seat for every month in which it was held on any day.
   - Recommendation: accept as an invented example rule.
6. **The budget is recorded, never enforced.** A user budget on GitHub stops that user (analysis F1). v1 only records the day the organization budget was crossed; "budget reached" is in the deferred events.
   - Recommendation: accept.
7. **Anthropic `cost_only` records are never produced.** The connector can emit them when cost arrives before usage. They need the timing of the deferred live mode.
   - Recommendation: leave them to that row.
8. **Activity ratios are fixed constants.** The ratios that turn requests into sessions, lines, commits and Copilot suggestions (5.6, 5.7) are fixed in v1. Profile gates, volumes, tokens, model weights, drift, service accounts and every event and provider setting are parameters (3.6).
   - Recommendation: accept. Make them parameters when a gold check needs to vary them.
