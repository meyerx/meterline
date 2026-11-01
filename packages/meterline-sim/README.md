# meterline-sim

> Part of [meterline](https://github.com/meyerx/meterline/blob/main/README.md). meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Invents an organisation (units, people, managers, seats) and several months of its AI usage, then writes the records the [`meterline`](https://github.com/meyerx/meterline/blob/main/packages/meterline/README.md) connectors would have written for that usage. It also writes the organisation files and a **ground truth**: everything the generator knows, such as each person's profile over time, every injected event and the expected totals. A later layer can then be tested against data whose correct answers are known in advance. Project terms such as *silver*, *gold* and *ground truth* are defined in the [glossary](https://github.com/meyerx/meterline/blob/main/docs/glossary.md).

It reads no provider API, needs no credential and writes nothing but synthetic data. Python 3.10 or later; the only runtime dependency is `meterline`.

**Status:** built from the accepted spec [`docs/dev/meterline-sim-SPEC-v1.0-2026-10-11.md`](https://github.com/meyerx/meterline/blob/main/docs/dev/meterline-sim-SPEC-v1.0-2026-10-11.md), in two phases, both built. See [Known limits](#known-limits).

| Phase | What it covers |
|---|---|
| 1 | The package, presets, organisation, profiles, calendar, the Claude Enterprise and LiteLLM silver, joiners and leavers, the reorganisation, the runaway agent, the price change, the ground truth, the manifest, the CLI, the determinism rules. |
| 2 | GitHub Copilot seats, credits, activity, billing and pool, the late correction, and every phase 1 check rerun with the defaults (Copilot enabled). Done-checks S06 and S09. |

```sh
pip install meterline-sim
meterline-sim generate --preset tiny --seed 7 --months 6
```

**From a clone** of the repository (for the tests and development):

```sh
pip install -e packages/meterline -e "packages/meterline-sim[dev]"
```

## The command

```
meterline-sim generate --preset NAME --seed N --months N [--start YYYY-MM-DD] [--scale S] [--out DIR] [--params FILE]
```

| Argument | Meaning |
|---|---|
| `--preset` | `vertical`, `tiny`, or a path to a preset file |
| `--seed` | an integer from 0 to 2^63 - 1 |
| `--months` | 1 to 24, default 6 |
| `--start` | first day of the period, default `2026-04-01` |
| `--scale` | a decimal > 0, default 1: the same shape at another size |
| `--out` | parent of the output folder, default `./sim-out` |
| `--params` | a JSON file of parameter overrides, for example `{"events": {"runaway_agent": {"enabled": false}}}` |

Exit codes: **0** written (prints the folder and the record count per kind); **2** bad arguments, preset, parameters or price table, or an output folder that is not empty (one-line reason); **1** a validation failure inside the run or name exhaustion (nothing is written). The library entry points are `meterline_sim.generate(...)` (returns the folder) and `meterline_sim.verify(folder)` (returns the failures of the invariants V1-V8, an empty list when all hold).

## Presets

- **`vertical`**: about 400 people in four levels (company, 6 divisions, 18 departments, 45 teams), mostly business functions plus a central IT. Profile shares: dormant 30%, curious 30%, office worker 30%, developer 8%, power user 2%.
- **`tiny`**: 20 people in two teams, every profile present, built for fast tests (a six-month run takes about a second).

A preset holds the **structure** (organisation, units, profile shares or counts, developer functions) and may override any parameter under its `parameters` key. Names are invented nonsense syllables and every address is on `example.com`; nothing resembles a real organisation.

## Parameters

Every parameter and its default is in [`src/meterline_sim/data/defaults.json`](https://github.com/meyerx/meterline/blob/main/packages/meterline-sim/src/meterline_sim/data/defaults.json): profile gates and volumes, token mixes, model weights, drift, service accounts, provider settings, the holiday list, the five events, the pull lag, and a price-table path. Resolution order: defaults, then the preset's `parameters`, then `--params`. An object merges key by key; a list or scalar replaces. A key that is not in the defaults is an error (except a new service account). Probabilities, rates and prices are decimal strings; a JSON number with a fraction is an error.

The example price table is [`src/meterline_sim/data/prices.json`](https://github.com/meyerx/meterline/blob/main/packages/meterline-sim/src/meterline_sim/data/prices.json): invented values, in USD, labelled as examples. It is editable and `price_table` may point to another file. The self-hosted model has no price; `providers.litellm.unpriced_cost` decides whether its records show cost `0.00` with basis `estimate` (`"zero"`, the default) or no cost (`"null"`).

## Outputs

`<out>/<preset>-s<seed>-m<months>/`:

```
org/units.csv, people.csv, memberships.csv, identities.csv, directory.csv
records/<kind>/<batch_id>.jsonl      kinds: usage, activity, seats, seat_assignments, org_totals
ground_truth.json                    people, profiles over time, seats, events, expected totals, contracts
manifest.json                        inputs' hashes and every file's size and sha256 (no timestamp)
```

Silver is written through `meterline`'s own record classes, validators and `write_jsonl`, and every batch is checked with `meterline.batch_failures` (and, for the Claude Enterprise and LiteLLM main batches, the actor-cost check) before anything is written. The organisation files carry validity dates, so a reorganisation or a joiner is visible as new rows. The ground truth holds no names or addresses; those are only in `directory.csv`.

### The batches

The simulator writes five batches, as the connectors would on their pull days:

| Batch | `pulled_at` (UTC) | Holds |
|---|---|---|
| Claude Enterprise main | period end + `pull.lag_days`, 06:00:00 | every Claude Enterprise record of the period |
| GitHub Copilot main | the same day, 06:10:00 | every Copilot record of the period except seat assignments |
| GitHub Copilot seat list of day d | d 06:10:00, for each day of the period | the `SeatAssignment`s of that day |
| LiteLLM main | the same day, 06:20:00 | every LiteLLM record of the period |
| GitHub Copilot correction | the main pull + `late_correction.after_days`, 06:10:00 | the Copilot records the late correction revised |

`is_final` follows each provider's rule at the pull date, so the last days and the last month of the main pulls are not final.

### GitHub Copilot

Copilot is the last product of an active day and needs a seat that day, so only developers and power users with a seat draw it; everyone else with a seat keeps an idle one.

- **Seats.** A person holds a seat if their unit is in `providers.github_copilot.granted_units`, or below one, on the day the seat starts (the period start, or a joiner's start date). A unit created by the reorganisation counts as granted when the unit it split was. A leaver's seat is listed until the end of their last month and carries a `pending_cancellation_date` (the first day of the next month) from their last day on. Seat ages before the period are drawn per holder in `seat_age_days`.
- **Silver.** Per seat holder and active day: a `copilot` usage record with `ai_credit` (written even at 0 credits), `copilot_cli` and `copilot_app` token records when those surfaces were used, one activity record per feature used and one `copilot` activity record of distinct-use counts. Per seat holder, month and model: a `month` usage record with the person's own discount. Per day and model: an organisation total, `billed`, with the pool's discount. One seat snapshot per day, and one seat list per day.
- **Billing.** Per month the pool is `seats_billed` (people holding a seat on any day of the month) times `included_credits_per_seat`. The organisation's discount walks the days in order and the models of a day in sorted order, each taking what is left of the pool; the rest is metered overage at USD 0.01 a credit. A person's own discount is a separate, invented display rule, so the sum of the people's discounts is not the organisation's. Seat fees (seats billed times the plan's price, no proration) go to the ground truth only, and so do the budget (the day the month's overage first exceeded it, recorded and never enforced) and the contract figures per month.
- **Late correction.** A share of the Copilot day rows in a short window just before the pull (default: 25% of the rows of 3 days that end 7 days before the period's end) are revised by a factor (default 1.10, to one decimal, half up). They were already final at the main pull. The correction batch re-renders every Copilot record and keeps only those whose content changed: the corrected days, the billing days whose discount moved, the month records, and every record that became final in between. The ground truth keeps both the old and the new value of each corrected row, and the expected totals before and after.

## Events

The simulator injects five kinds of event, each of which can be switched off:

- joiners and leavers, about 1.5% a month each;
- one reorganisation: a unit split in two mid-period, visible only in the organisation files, as no provider sees the organisation;
- one runaway agent: a developer's Claude Code volume ×20 for 2 days;
- one late provider correction of Copilot credits;
- one price change mid-month.

Every event is written to the ground truth with its dates, actors and details, and with `applied: false` when it is enabled but has nothing to act on (for example the correction when Copilot is disabled).

## Determinism

The same preset, price table, seed and parameters give the same bytes, on any platform and with any Python 3.10+.

- one random stream per component, derived from the seed and the component's name; a new feature takes a new stream, so existing output does not move;
- the draw kit computes `ln` and `exp` in `Decimal`, so floats are the same everywhere;
- no wall clock, no unordered iteration, no module-level `random`, no Faker: names come from a bundled generated word list;
- money and quantities are `Decimal` end to end; `prices.py` and `billing.py` never use a binary float.

Three done-checks hold this. S01 reruns the build in a second process with another `PYTHONHASHSEED` and expects the same bytes. S03 switches a feature off and expects nothing else to move: with Copilot on, every Claude Enterprise and LiteLLM file is the byte-for-byte file of the build that had no Copilot. S16 scans the source.

## Gold and the simulator: an independent oracle

`meterline-gold` computes the reference cost of a usage record (pricing) and the credit pool of a seat plan. The simulator computes the same two things again, in `prices.py` and `billing.py`, and `verify` checks the generated records against them. That is on purpose: the simulator is an **independent oracle** for gold. The gold tests compare gold's output with the figures in `ground_truth.json`, which the simulator derived by its own route, so a mistake in one side shows up as a disagreement with the other.

Two rules follow. To change a price rule or a pool rule, change gold's SQL (the engine) and the simulator's arithmetic each on their own, and treat a disagreement as a finding. Never import one side's arithmetic into the other, and never share a helper: a shared helper turns the oracle into a copy.

## Known limits

The limits of the simulator, grouped by kind.

**Not in v1.** These are deferred to a later version:

- a replay server that emits raw API responses;
- a live day-by-day mode;
- the `squads` and `mixed` presets;
- the remaining profiles and the model-preference axis;
- the remaining events;
- a conflicting team claim and an AWS bill gap;
- other Claude contract structures.

**Not built, with no plan yet.** Parquet output, a `verify` command-line command, enterprise-level Copilot, `cost_only` records and unresolved identities. It writes no gold dataset; the pricing and pool arithmetic it does carry is the independent oracle described above.

## Public interface

What later releases keep working: the `meterline-sim generate` command line (arguments and exit codes above), the layout of the folder it writes (see Outputs), and from Python the names in `meterline_sim.__all__`: `generate`, `verify` and `__version__`. Everything else is internal and may change in any release: every module of the `meterline_sim` package, the preset and parameter file layouts beyond what Presets and Parameters document, and the built-in data files.

## Tests

```sh
.venv/bin/python -m pytest -q packages/meterline-sim          # several minutes: two vertical runs and a 50-example property test
.venv/bin/python -m pytest -q packages -k done_check          # the acceptance checks of the whole repo
```

The done-checks are `test_done_check_S01_*` to `S16_*`; each has a negative control that applies the same assertion to a deliberately broken input. All of them run with the defaults, Copilot enabled. `slow` marks the vertical pair of S01 and `timing` marks S14; both are part of the done gate. The tests import the package from the checkout they live in (see `tests/conftest.py`), whatever an editable install of `meterline-sim` points to.
