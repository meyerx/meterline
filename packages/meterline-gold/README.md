# meterline-gold

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Reads meterline silver (the v2 records), a set of organisation files, a reference price list and a policy file, and writes a **gold folder**. The folder holds datasets about people, groups, seats, costs and data quality, each already filtered for the audience that gets it (central, manager, person, exec). Nothing it writes enforces anything. Project terms such as *silver*, *gold* and *ground truth* are defined in the [glossary](https://github.com/meyerx/meterline/blob/main/docs/glossary.md).

It reads files and writes files. It calls no provider API, needs no credential other than the hashing key, and opens no network connection. Python 3.10 or later; runtime dependencies are `meterline`, `duckdb`, `pydantic` and `PyYAML`.

**Status:** built from the accepted spec [`docs/dev/history/meterline-gold-core-SPEC-v1.1-2026-10-14.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/history/meterline-gold-core-SPEC-v1.1-2026-10-14.md) (phases 1 and 2 were built against v1.0, whose findings v1.1 resolves) in three phases; all three are built. The `hidden` marker and gold schema version `1.1` follow the accepted spec [`docs/dev/meterline-gold-core-SPEC-v1.2-2026-10-15.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/meterline-gold-core-SPEC-v1.2-2026-10-15.md). See [Known limits](#known-limits).

| Phase | What it covers |
|---|---|
| 1 | Package, parameter files and schemas, the LiteLLM price importer, loading and hashing, the organisation datasets, `person_day`, `person_day_activity`, `person_month`, seats, pools, costs, reconciliation, data quality, the `central/` and `delivery/` folders, the manifest, assertions A01-A03, A08, A09 and the CLI. |
| 2 | The fixed grid, the privacy algorithm, comparison groups, the `manager/`, `person/` and `exec/` folders, assertions A04-A07 and the example dashboard. |
| 3 | Shadow scenarios, alert events, named exceptions and the rest of the layout; the manifest's `unknown_envelope_keys`, the month's unit tree, A05's parent-subset part, A06 step a and the escaping of cell ids. |

The findings the builds logged against the spec are in [`docs/dev/change-list-meterline-gold.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/change-list-meterline-gold.md).

## Read this before changing gold

In this order:

1. This README: what a build reads and writes, what it refuses, and the privacy rules.
2. [`docs/dev/meterline-gold-core-SPEC-v1.2-2026-10-15.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/meterline-gold-core-SPEC-v1.2-2026-10-15.md), the contract. It is long: read section 3 (inputs), section 6 (the datasets), section 7 (privacy) and section 9 (assertions) first. An accepted spec is frozen: a finding goes to the change list.
3. [`docs/dev/change-list-meterline-gold.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/change-list-meterline-gold.md): what each build and review found against the spec, and what the code does meanwhile. Read the entries for the section you change.
4. [`docs/gold-fixture/README.md`](https://github.com/meyerx/meterline/blob/main/docs/gold-fixture/README.md): the hand-built fixture whose expected outputs were computed by hand.
5. `src/meterline_gold/sql/`, in file-name order. The SQL is the engine; `run.py` loads, executes and fetches, and `load.py` and `write.py` do the stage 0 and the writing. No dataset figure is computed in Python.
6. `tests/test_done_check_G*` (the acceptance checks) and the `test_review_*` files (the checks added after the review).

Gold computes the reference cost and the credit pool; `meterline-sim` recomputes both on its own as an **independent oracle**, so a change to a price or pool rule is made on both sides separately (see [the simulator's README](https://github.com/meyerx/meterline/blob/main/packages/meterline-sim/README.md#gold-and-the-simulator-an-independent-oracle)).

```sh
pip install meterline-gold
export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
meterline-gold build --silver SILVER --org-files ORG --policy POLICY.yaml --prices PRICES.yaml --out gold
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline -e packages/meterline-sim -e "packages/meterline-gold[dev]"   # the tests run the simulator
```

## The command

```
meterline-gold build --silver DIR --org-files DIR --policy FILE --prices FILE --out DIR [--invoice FILE] [--parquet]
meterline-gold schemas --out DIR
meterline-gold import-litellm-prices --file FILE --keys FILE --as-of YYYY-MM-DD --out FILE
```

`--silver` points at the folder that holds `records/` (the folder above it), not at `records/` itself; a folder with no silver records is an input error.

`schemas` writes the policy and price schemas, the organisation, manifest and dataset schemas (one `gold-dataset-<name>.schema.json` per dataset file form, with column types, units, descriptions, keys and joins) and `gold-audiences.json`, which says which file may be served to which reader. From Python, `meterline_gold.servable(path, audience)` reads those rules. It is `False` for `delivery/` whatever the audience. It is `True` for `manifest.json` when the audience is `central` and `False` for the other three. Any other file is `True` only for the audience whose folder holds it, and a path the rules do not list is `False`. The same facts as a page are in the [dataset reference](https://github.com/meyerx/meterline/blob/main/docs/reference/gold-datasets.md).

Exit codes: **0** done; **2** bad arguments, an input that fails validation, a missing or short key, or an output folder that is not empty; **1** an assertion failed during the build (nothing is written).

## Public interface

What later releases keep working:

- the three commands above, with their options and exit codes;
- the policy and price file formats (`policy_format`, `price_list_format`, and the JSON Schemas `meterline-gold schemas` writes), the dataset schemas and `gold-audiences.json` it writes beside them;
- the organisation CSV files (JSON Schemas in [`docs/schemas/`](https://github.com/meyerx/meterline/blob/main/docs/schemas));
- the datasets and `manifest.json` a build writes, versioned by `GOLD_SCHEMA_VERSION` (every dataset row carries it in `schema_version`, the manifest in `gold_schema_version`);
- from Python, the names in `meterline_gold.__all__`: `build`, `servable`, `GOLD_SCHEMA_VERSION`, `InputError`, `BuildFailure` and `__version__`.

Everything else is internal and may change in any release: every module of the `meterline_gold` package, the SQL files and the example dashboard's code.

## What the inputs must be

A build stops with exit 2, writes nothing and names the file when:

- a silver record breaks a rule of the record format: the `meterline` validator runs on every record read and refuses, for example, a negative amount, an unknown `actor_kind` or `cost_basis`, a token sum that does not add up or a number that is not finite;
- silver costs are in more than one currency, or in a currency other than the policy's: gold never adds amounts of different currencies, so build once per currency from silver that holds one;
- a currency (policy, price list, silver) is not an ISO 4217 code (three upper-case letters from the list in `iso4217.py`).

A build does **not** stop for a person that an identity row or a usage row names and `people.csv` does not employ that month, nor for a unit whose `parent_id` is no unit valid that day. The first drops that person's cost from every `person_month`, the second makes the unit a root. Both are counted: `data_quality` gets `rows_person_missing` and `cost_ref_person_missing` (per month, provider and org) and `units_parent_unknown` (per month), each written only where it is not zero; `manifest.json` gets `input_flags`; the build command prints a warning. Assertion A10 holds that, per month, the usage cost of `costs_month` equals the cost in `person_month` plus the cost that `data_quality` accounts for.

## What a build writes

A build writes one folder per audience, a delivery folder and a manifest:

| Folder | Holds |
|---|---|
| `central/` | Pseudonymous person-level data and every grid cell, suppressed ones with their reason; also `scenario_persons`, `scenario_units`, `scenario_summary`, `alert_events` and `named_exceptions`. |
| `manager/<manager_key>/` | `groups_month`: the unit cells the manager heads, and every unit cell below them (never the org cell), with a last column `hidden` and no reason; `named_exceptions`: count-valued rules for the persons they manage. Each `named_exceptions` row carries the `person_key` of the person it names: a manager needs to know who. |
| `person/<person_key>/` | `person_day`, `person_month` and `comparison`: only that person's rows, and a banded comparison with their group. A person folder carries no other person's key: its `person_month` has no `manager_key` column (central's has one). `comparison` ends with a `hidden` column (see Privacy). |
| `exec/` | `groups_month` down to `audiences.exec.max_depth`, `seats_month`, `pools_month` and `costs_month` with the small-population rule applied, and `scenario_summary` with the stopped count shown only when both sides are at least `k`. All five end with a `hidden` column (see Privacy). |
| `delivery/` | `directory.csv`: the only file with names and emails, for a sender that is out of scope. Never serve it. |
| `manifest.json` | Inputs, outputs with hashes, the suppression counts, `input_flags` (what the inputs got wrong without stopping the build) and the unit-envelope keys that matched no unit. It lists every person and manager key through its paths, so it belongs with `central/`: never deliver it to an audience. |

Every file of the gold folder is readable by its owner only (files `0600`, folders `0700`, the output folder and any parent the build creates included), whatever the umask of the shell; `tests/test_review_f33_output_permissions.py` checks it. To hand a folder to an audience, copy it and set the permissions the copy needs.

**Reading the CSV files.** They are data for programs (the dashboard, a notebook, a warehouse load), not spreadsheets. Text that comes from the organisation files, such as a unit name, a function or a person's name in `delivery/directory.csv`, is written as it is. A value that starts with `=`, `+`, `-` or `@` is taken as a formula by a spreadsheet that opens the file directly. To look at a file in a spreadsheet, import it with every column as text instead of opening it. gold does not rewrite such values, because that would change the data every other reader gets (`tests/test_review_f33_csv_formula_cells.py` pins both).

**Compatibility.** A new dataset, a new column appended after the others and a new value in an open list are non-breaking; a column renamed, removed or retyped, a dataset removed or its grain changed needs a new major `GOLD_SCHEMA_VERSION`. A reader selects columns by name, ignores a column it does not know, fails on a missing one, and reads `schema_version` (the first column) to know which rule applies. The rule is in the [dataset reference](https://github.com/meyerx/meterline/blob/main/docs/reference/gold-datasets.md#compatibility).

## Who gets which folder

Each role reads the folder of its own audience, never a wider one. `<key>` is the `manager_key` or `person_key` of the reader. `meterline_gold.servable(path, audience)` says the same for a single file, and the table is a summary of it.

| Role | Folder it gets | Never receives |
|---|---|---|
| Central team, platform lead, FinOps | `central/` (and `manifest.json`) | `delivery/` (names and emails; only the people who run the build see it) |
| Executive | `exec/` | `central/`, `manager/`, `person/`, `delivery/` or `manifest.json`; no per-person row (the exec files hold no person key) |
| Manager | `manager/<key>/` | another manager's folder, `central/`, `exec/`, `person/`, `delivery/` or `manifest.json`; never the organisation cell |
| Individual | `person/<key>/` | another person's folder or key, `central/`, `manager/`, `exec/`, `delivery/` or `manifest.json` |

A folder is copied to its reader; nothing in gold sends it or checks who opened it. The privacy rules are in the section Privacy below.

## The example policies

Two policies ship in `src/meterline_gold/examples/`, both with invented values and both buildable on the simulator's output with `prices.example.yaml`:

| File | Shows |
|---|---|
| `policy.example.yaml` | A company with a unit tree: `tree.kind: unit`, cost bands, exec depth 2, scenarios with caps by level and unit envelopes. |
| `policy.attributes.example.yaml` | A flat organisation grouped by attributes: `tree.kind: attributes` with three dimensions (`population`, `function`, `attrs.location`), `bands.metric: tokens`, the `breaker_events_at_least` rule and a cap by population. |

To write your own, follow [`docs/how-to/configure-policy.md`](https://github.com/meyerx/meterline/blob/main/docs/how-to/configure-policy.md): it builds a worked example on the simulator's output and shows what each part produces.

The attributes a tree, a comparison group or a cap can use are `function`, `level`, `cost_center`, `population` and `attrs.<key>` for each key in `people.attrs_keep`.

## Fixed values

These are in the code and not in the policy file:

- Ratios are rounded half up to **4 decimals** (`premium_share`, `cache_rate`, `unreconciled_share`, the shares in `data_quality`).
- `projected_cost_ref` is rounded half up to **2 decimals**.
- Token prices are written per million tokens with at most **6 decimals** and read as exact per-token decimals (a shift of 6 places). Every other amount has at most 12 decimals.
- A unit tree is followed **64 levels** up from a person's unit; a hierarchy deeper than that would lose the ancestors beyond it.
- Group percentiles are nearest-rank at 50, 75, 90 and 99, the top decile is the top tenth of the organisation, and a person's comparison bands cut at the 50th, 75th and 90th percentile of their comparison cell.

## Scenarios and alerts

The policy file holds N named shadow scenarios (caps by attribute, a flat cap, unit envelopes, a default cap). For each month the build writes who or which unit would have been stopped, the amount beyond the cap, the top-ups needed and the alert crossings, plus daily-breaker and spike events and the named exceptions of the policy. Nothing is enforced and nothing notifies anyone.

## Privacy

The rules that keep a gold folder from exposing a person:

- Persons are never named in a dataset: a keyed hash (HMAC, key from the environment) stands for each of them.
- A group figure is published only for a cell of at least `k` people. The suppression loop of spec 7.4 suppresses a cell that differs from another published cell by fewer than `k` people. It also suppresses a sibling, or the cell itself, when what remains of a block is under `k`, with or without the people who know their own figures.
- **What an empty value means (schema version 1.1).** In `exec/costs_month`, `exec/pools_month`, `exec/seats_month`, `exec/scenario_summary` and `person/<key>/comparison`, the last column `hidden` is `true` exactly when a privacy rule withholds a value of that row. There, an empty covered value on a row with `hidden` = `true` is withheld; an empty value on a row with `hidden` = `false`, or in a column the marker does not cover, means none. The marker carries no reason. The exec and manager `groups_month` carry the same `hidden` column in place of `suppressed` and `suppression_reason`; only `central/groups_month` keeps the reason. `central/` withholds nothing in the other datasets. A gold folder of version 1.0 is rebuilt, never upgraded in place.
- **A withheld count can sometimes be worked out.** `exec/seats_month` withholds `active_seats` and `idle_seats` separately and shows `seats_billed` and `seats_on_eval_day`. So `seats_billed` minus a shown `idle_seats` is an estimate of a withheld `active_seats`, exact only in simple cases. The rule tests each count against `k` on its own and does not stop this.
- **What a cost figure is.** `costs_month` (central and exec) ends with `cost_basis_label`, the words for `cost_source_basis` next to the figure: `usage value, not the invoice` for `consumption`, `estimate, not the invoice` for `estimate`, `billed amount, as invoiced` for `billed`. It also ends with `invoice_usage`, the invoiced usage amount from `--invoice` for the same provider, organisation and month (the value `reconciliation_month` shows; empty when no invoice row exists). A Copilot usage value of 662.49 and an invoiced 47.49 therefore read as two different things on one row. In `exec/costs_month` both columns are withheld with the figure they describe.
- No file is sorted by a measure, and a person sees their own figures and a band against their comparison group, never another person's figure. A person folder carries no other person's key either.
- **Keys are pseudonyms, not anonymous.** `person_key` in `central/` and in a person folder, `manager_key` in `central/`, and the `person_key` and `manager_key` of a manager's `named_exceptions` all stand for a person for as long as the hashing key exists. A manager's folder lists the persons a rule names because a manager needs to know who (names are joined at delivery, from `delivery/directory.csv`). `manifest.json` lists every key through its paths and is a central file.
- **Keys rotate with the key.** A new `METERLINE_GOLD_KEY` changes every `pk_`, `ak_` and `kc_` value of a rebuilt gold folder, so old and new folders no longer join. It does not change the short **unkeyed** sha256 labels that the connectors write into their raw folder names and step names (the LiteLLM entity tag, the Copilot login tag): an identifier that can be guessed, such as an email address, can be confirmed against them. Keep raw folders as closed as silver, and delete them when the labels must go.
- The key and the policy's thresholds stay out of git: `.env` files are ignored, and a test scans every tracked file for a key-shaped value.
- `tests/` holds a property test that tries to recover suppressed cells with an exact linear attacker and must fail, and named tests that pin each boundary of the rule at `k` = 2 and `k` = 5.

### What disappears under suppression

Take `k` = 5, an organisation of three units, and a head in each unit who knows their own figure:

| Unit sizes | What the files show |
|---|---|
| 4, 6, 8 | The unit of 4 is `suppressed` with reason `k`: its row stays, its figures are empty. The unit of 6 is also `suppressed`, reason `secondary`: the org cell minus the units of 6 and 8 would give the unit of 4 away. The unit of 8 and the org cell are published. |
| 5, 6, 8 | The unit of 5 is `suppressed`, reason `k`: without its head, 4 persons remain. The unit of 6 is `suppressed`, reason `secondary`. The unit of 8 is published. |
| 6, 6, 8 | Nothing is hidden: 6 persons leave 5 once the head is taken out. |

The same unit sizes without a head who knows their figure (5, 6, 8) hide nothing. Only `central/groups_month` shows `suppressed` and the reason. The exec and manager `groups_month` show the same cells with a last column `hidden` (`true` for a suppressed cell) and no reason. A reader there learns that a cell is hidden for privacy, not that it is below `k`.

## Known limits

The limits of gold, grouped by kind. The privacy rules above hold inside these limits.

**What the privacy rules do not cover.** Suppression is exact against a reader who adds and subtracts published figures, within one build and one month. It does not cover:

- **Refreshes of one month.** A late correction to silver or `people.csv` can make a published cell differ between two builds of one month by one person's amount, and a reader who holds both learns that amount. `meterline-job` refreshes a month in progress and then freezes it, so that a closed month is delivered once; the rule is in the table below (spec 7.4, limits). A folder made by `meterline-gold build` alone has no freeze: deliver each complete month once yourself.
- **Spike windows.** The spike and breaker alerts of `central/alert_events.csv` look back over a trailing window of days that can reach into the previous month. An alert of the month in progress can therefore change when a figure of the previous month is corrected in a later build, even though that month's published rows are frozen: `alert_events` is central only and always comes from the latest build.
- **Outside knowledge.** A reader who knows something the files do not say, such as one colleague's usage, is not modelled.
- **The hashing key.** Anyone who holds `METERLINE_GOLD_KEY` can compute the key of any person or actor whose id they know (a `person_id` of `people.csv`, or a provider's actor id), and so find their rows. Keep the key as closed as `delivery/`.
- **One tree.** Additive figures follow one tree only (units, or one set of attributes). A second cross-cutting hierarchy with costs could reveal a person through its margins, so v1 offers none (spec section 15, card 2).

The delivery rule, word for word as `meterline-job`, `PRIVACY.md` and `THREAT-MODEL.md` state it:

| Delivery rule |
|---|
| A month is refreshed on every published run until it is frozen, which happens once every source has final data for it and `freeze_after_days` have passed, or at the latest `freeze_after_days + freeze_cap_extra_days` days after its end at the next run; from then on it is delivered once and does not change, except that an erased person's rows are removed, and after a change of the hashing key manager and person folders show only months frozen under the new key. Serve only the `published` folder, never `builds/` or `frozen/`, and give managers and executives only its latest state; keeping earlier states is the adopter's choice, not the tool's. A reader who keeps every refresh of an in-progress month can sometimes learn one person's usage between two refreshes, one person's month-to-date amount when a correction or an organisation change moves that person, a cell published in one refresh and hidden in the next, or a named exception that appears and then disappears. The adopter accepts this risk and has it validated by its data protection officer. |

**How a build behaves.** These follow from how the build writes its output:

- **A leftover temporary folder stops the next build.** A build writes into `<out>.tmp` and renames it at the end. A build that was killed can leave `<out>.tmp` behind; the next build to the same `--out` then stops with exit 2 ("a leftover temporary folder is in the way"). Delete the `.tmp` folder and build again.
- **No delivery.** Gold writes `delivery/directory.csv` for a sender that is out of scope: nothing sends a folder to its audience or checks who opened it.

**Not verified.** Gold has run on the hand-built fixture and on simulated data only, never on a real organisation's silver.

## The example dashboard

`src/meterline_gold/dashboard/index.html` is one static file with no external request and no authentication. Serve **one audience folder** as the web root, with the file copied into it:

```sh
cp src/meterline_gold/dashboard/index.html gold/exec/
cd gold/exec && python3 -m http.server 8000
```

With `meterline-job`, serve the `published` folder, never `builds/` or `frozen/`: copy the file into `<gold_root>/published/exec/` and run `cd <gold_root>/published/exec && python3 -m http.server 8000`. Each run assembles a new folder, so copy the file again after a run, or let your web server add it. The folder holds only the latest published state: give managers and executives that and nothing earlier.

It shows each file as a table in the file's own row order, offers no sort on a measure and computes no figure. In a file with a `hidden` column it shows an empty covered value of a `hidden` = `true` row as `hidden`, and leaves an empty value of a `hidden` = `false` row blank. Never serve the gold root or `delivery/`.

A file with a `month` column opens on its latest month; a selector next to the table shows another month or all of them (this filters the rows shown, nothing is recomputed). The chart under the table opens on a cost column (`total_cost_ref`, `usage_cost_ref` or `cost_ref`, whichever the file has), never on `schema_version`. When the table is wider than the screen, the two columns after `schema_version` stay in view as it scrolls sideways (not on a screen narrower than 700 pixels).

The page carries a content security policy (a `<meta>` tag): no request leaves its own folder, and the only script and style allowed are its own inline ones, named by their sha256 hash. An edited script or style is blocked by the browser until the hash in the tag is updated; `tests/test_review_f33_dashboard_csp.py` fails and prints the value it expects. A web server that sets its own `Content-Security-Policy` header should send the same policy.

The page aims at WCAG 2.2 AA. It has a header and a main landmark, a name on each control and scroll region, and a caption and column headers on each table. It announces the file shown after a tab change, and its borders and bars reach 3:1 contrast in both colour schemes. `tests/test_review_f43_dashboard_a11y.py` checks these in the page source. A browser run of axe-core on the fixture build found no violation on 2026-10-17; no browser check runs in CI yet.

## Tests

```sh
python -m pytest -q packages/meterline-gold
```

The done-checks of spec section 14 are the tests named `test_done_check_G<NN>_*`. They use only the hand-built fixture of `docs/gold-fixture/` and simulator output.
