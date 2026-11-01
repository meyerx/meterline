# meterline

meterline helps people who run or pay for AI tools in an organisation see and compare usage and cost across providers: it reads each provider's usage API and writes the data in one common record format.

Each provider reports usage in its own shape, on its own dashboard, with its own meaning of tokens, cost and users, and keeps it for a limited time. meterline turns those reports into one JSON line per actor, product, model and period, plus optional Parquet. It keeps every response it read, so the records can be rebuilt. A second step builds per-person and per-group datasets that apply privacy rules before anyone reads them.

**Status:** works end to end on simulated data. One connector has run against a live account; the others have not. The record format, the connectors (Anthropic Enterprise Analytics, Anthropic Admin, LiteLLM, GitHub Copilot), the simulator and the *gold* datasets (the tables built from the records for each audience, see [Architecture](docs/architecture.md)) are built and tested. Evidence levels as of 2026-10-30 (the three [evidence levels](docs/glossary.md#evidence-levels) are defined in the glossary): `meterline-anthropic-admin` is verified live on a small Console organisation (one model, two days of usage; cache tokens, priority tier and several workspaces not yet seen). `meterline-litellm` is built against documented shapes and checked against LiteLLM v1.106.6's own route code. `meterline-anthropic` and `meterline-copilot` are built against documented shapes. Every connector is also tested against a fake API. Read [Known limits](#known-limits) before using it on real data.

## Who it is for

**For:** people who look after AI tools for an organisation, such as a platform or tooling team, a finance or FinOps analyst, or an engineering manager. They need usage and cost from several providers in one place, kept longer than the providers keep it, and shared with managers and staff without exposing individuals.

**Not for** these uses, where something else serves better:

- a single developer watching their own usage: the provider's own page is enough;
- real-time monitoring or enforcing limits: meterline runs as a daily batch and enforces nothing;
- invoicing or accounting: its records are usage analytics, not billing lines (see [Related standards](#related-standards));
- reading prompt or response content: no record holds any.

**Why not the provider dashboards?** Each provider's own dashboard shows one provider, in its own terms, for a limited period, and cannot be joined with your organisation's structure. What counts as a token, a user or a cost differs from one provider to the next. meterline puts every provider in one format and names the differences: for example `cost_basis` says whether a cost is billed, consumption or an estimate. It keeps the history in files you own, and its gold step hides any group figure that would expose a few people before a manager or a person sees it.

**Why not a script over the API?** A first script is quick; the rest takes the time. Paging and rate limits, late and revised data, a day the API has not finished, cache tokens counted differently per provider, totals that must match, raw responses kept so the records can be rebuilt: meterline does that once per provider, with tests against fake APIs. If you need one number from one provider once, a script is the better tool.

**Evidence of need:** untested. No one outside the project has used meterline or asked for it yet, so whether others need it is an open question. An issue that says what you used it for, or why it did not fit, is the evidence it lacks.

**Maintenance:** one maintainer, best-effort, with no fixed response time (see [`SECURITY.md`](SECURITY.md) for security reports). A change in a provider's API is fixed when a live run or a report shows it. Each package follows semantic versioning; the rules are in [`docs/records.md`](docs/records.md#compatibility).

**Assumptions.** meterline is built on these; if one does not hold for you, expect extra work:

- You have administrator or analytics access to each provider and can create a key or token for it. Use the narrowest one the provider offers. The Anthropic Admin key cannot be narrowed; its connector README says what it can do.
- You run meterline yourself, on a machine you control, once a day. It is a batch tool, not a service.
- The provider's own figures are taken as given. meterline converts and checks them but does not re-price them; gold adds a separate reference cost from a price list you supply.
- People are identified by the email, login or id each provider uses, and the connectors do not match them across providers. Gold joins them through organisation files you supply.
- One currency per gold build.
- Organisations of hundreds to a few thousand people: some connectors make one call per person per period.

## Where to start

Pick the goal you have:

- **Try it on simulated data:** [Try it without a key](#try-it-without-a-key), then the [tutorial](docs/tutorial.md).
- **Run a connector on a real account:** [Run a connector](#run-a-connector), then the [package READMEs](docs/README.md#run-a-connector-on-a-real-account).
- **Build a dashboard from gold:** [the gold package](packages/meterline-gold/README.md) and the [policy how-to](docs/how-to/configure-policy.md).
- **Add a provider:** [Adding a provider](#adding-a-provider) and the [how-to](docs/how-to/add-a-provider.md).

The full index by goal is [`docs/README.md`](docs/README.md). Which layer to read: a dashboard reads **gold**, from the audience folder of its reader; silver is for building your own pipeline and holds per-person identifiers with no suppression.

## Packages

The data moves through three layers, shown in [`docs/architecture.md`](docs/architecture.md): *bronze* (each provider's responses, stored as received), *silver* (the common records) and *gold* (datasets per person, group and audience, with group-size suppression). Every project term is defined once in the [glossary](docs/glossary.md). These packages do the work, listed in the order the data flows:

| Package | What it holds | Network |
|---|---|---|
| [`packages/meterline`](packages/meterline/README.md) | The v2 record types, their validation, the v1 converter, and the JSONL and Parquet writers. | none |
| [`packages/meterline-anthropic`](packages/meterline-anthropic/README.md) | A connector for the Anthropic Claude Enterprise Analytics API, with a `meterline-anthropic` command. | `httpx` |
| [`packages/meterline-anthropic-admin`](packages/meterline-anthropic-admin/README.md) | A connector for the Anthropic Admin Usage and Cost API of a Console organisation (API usage per key and model, and billed cost), with a `meterline-anthropic-admin` command. | `httpx` |
| [`packages/meterline-litellm`](packages/meterline-litellm/README.md) | A connector for a LiteLLM proxy's admin API, with a `meterline-litellm` command. | `httpx` |
| [`packages/meterline-copilot`](packages/meterline-copilot/README.md) | A connector for GitHub Copilot usage reports, billing and seats (organization or enterprise level), with a `meterline-copilot` command. | `httpx` |
| [`packages/meterline-sim`](packages/meterline-sim/README.md) | A generator: an invented organisation and its AI usage (Claude Enterprise, GitHub Copilot, LiteLLM) as silver records, organisation files and a ground truth, with a `meterline-sim` command. | none |
| [`packages/meterline-gold`](packages/meterline-gold/README.md) | Per-person, per-group and per-audience datasets (central, manager, person, exec) from silver, organisation files, a price list and a policy file, with group-size suppression, shadow scenarios, alert events and a static example dashboard, and a `meterline-gold` command (all 3 phases). | none |

Further providers come as sibling packages on the same record format; see [Adding a provider](#adding-a-provider).

## Quick start

Needs Python 3.10 or later. This installs every package in editable mode (the Anthropic connector with its test and Parquet extras, the simulator and gold with their test extras) and runs every test. Nothing in the tests touches the network or needs a key.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e packages/meterline -e "packages/meterline-anthropic[dev,parquet]" -e "packages/meterline-anthropic-admin[dev]" -e packages/meterline-litellm -e packages/meterline-copilot -e "packages/meterline-sim[dev]" -e "packages/meterline-gold[dev]"
.venv/bin/python -m pytest -q packages
```

The install takes a few minutes and the test run about twenty (the simulator's property tests are most of it). A healthy run prints a growing line of dots and ends with `passed` in the last line, with no `failed` or `error`. Two tests assert a wall-clock bound (`S14` in the simulator, `G21` in gold). `G21` is skipped when the environment variable `CI` is set, as it is on the CI runners, and `S14` runs there too. On a heavily loaded machine they can fail alone, so rerun them by name before suspecting anything else (unset `CI` to run `G21`).

### Try it without a key

The simulator invents an organisation and its usage; gold turns that into the per-person and per-group datasets. Run this from the repository root, after the install above.

```sh
.venv/bin/meterline-sim generate --preset tiny --seed 7 --out sim-out
export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
.venv/bin/meterline-gold build --silver sim-out/tiny-s7-m6 --org-files sim-out/tiny-s7-m6/org --policy packages/meterline-gold/src/meterline_gold/examples/policy.example.yaml --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml --out gold
```

The simulator prints the folder it wrote (`sim-out/tiny-s7-m6`) and one `kind: count` line per record kind; the records are under `sim-out/tiny-s7-m6/records/`. Gold prints `gold folder: gold`, the months it covers and one `file: N rows` line per dataset under `gold/central/`; it may also print a warning that the example policy's unit envelopes match no unit of the invented organisation, which is expected. `--silver` is the folder that holds `records/`, not `records/` itself. Both output folders are ignored by git.

The [tutorial](docs/tutorial.md) walks through the same steps with everything each command prints, and opens the result in the example dashboard. To write your own policy file, see [how-to/configure-policy.md](docs/how-to/configure-policy.md).

### Run a connector

To run the connector, create an Analytics key (scope `read:analytics`) and put it in the environment. The key is read from `ANTHROPIC_ANALYTICS_KEY` and from nowhere else.

```sh
export ANTHROPIC_ANALYTICS_KEY=...                       # never commit this
.venv/bin/meterline-anthropic backfill --since 2026-01-01   # first load, resumes after a failure
.venv/bin/meterline-anthropic daily                          # run once a day
.venv/bin/meterline-anthropic rebuild                        # re-create records from raw, no API calls
```

Output goes under `exports/meterline-anthropic/` by default (gitignored). Change it with `--data-dir`.

## The record format

Five record kinds, all dates UTC, `period_end` inclusive, amounts `Decimal` serialised as strings. Every line carries `record_version` 2; a measure a source does not report is `null`, never `0`.

| Record | One per | Holds |
|---|---|---|
| `UsageRecord` | provider, org, scope, grain, actor, period, product, model | four disjoint token buckets (uncached input, cache read, cache write, output) and their total, requests, billing `quantities`, cost, list cost, currency and `cost_basis`, `extras` |
| `ActivityRecord` | provider, org, scope, grain, actor, period, product | sessions, messages, requests, suggestions, lines, edits, commits, pull requests, and named `counters` |
| `SeatSnapshot` | provider, org, scope, day | seats, pending invites and cancellations, daily, weekly and monthly active users |
| `SeatAssignment` | provider, org, scope, day, person | one person holding a seat: plan, assigned and last-active times, granting teams |
| `OrgTotal` | provider, org, scope, grain, period, product, model | organization-wide tokens and cost, used to check that per-actor cost never exceeds the total |

`grain` is `day`, `month` or `window`; `actor_kind` says whether an actor is a user, a proxy user, an end user, a service account, an API key or unassigned. Every record also carries `pulled_at`, `data_refreshed_at` (which may be null) and `batch_id`. A re-pull of a period writes new records under a new `batch_id`; a reader takes the latest batch per record key (`meterline.latest_per_key`), and earlier batches stay readable.

Every field, with its type, nullability, meaning and Anthropic source field, is in the [record reference](docs/records.md). The accepted specs are [`docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md`](docs/dev/meterline-v2-SPEC-v1.0-2026-10-10.md) (the format and the Anthropic, LiteLLM and Copilot connectors) and [`docs/dev/meterline-anthropic-SPEC-v1.1-2026-10-10.md`](docs/dev/meterline-anthropic-SPEC-v1.1-2026-10-10.md) (the Anthropic fetcher). v1 record files are converted with `meterline.convert_v1_batch`; meterline never writes v1 again, so readers need to handle one format only. Why the format is as it is, with the alternatives weighed, is in [`docs/decisions/`](docs/decisions/README.md).

## How a run is laid out on disk

Each connector writes four things under its data directory (`--data-dir`):

```
<data-dir>/
  raw/<endpoint>/<window>/<pulled_at>.json.gz   every response page exactly as received
  runs/<batch_id>.json                          one manifest per run: windows, pages, counts, totals, new products and models, skipped windows, cost-only records
  records/<kind>/<batch_id>.jsonl               the published records, one file per batch and kind
  state.json                                    what is already loaded, plus a resume block
```

The connector has two layers so they test separately: a fetcher that only talks to the API and writes raw files, and a normaliser that only reads raw files and emits records. `rebuild` runs the normaliser alone, which is why the published records can be reproduced byte for byte.

## Adding a provider

A provider is a new sibling package under `packages/`, next to the existing connectors. It depends on `meterline`, emits the five record types of [`docs/records.md`](docs/records.md), and adds no record type. It brings its own fake API and its own shape files: one JSON file per endpoint, transcribed from the provider's documented example response, with synthetic values. A shape check compares the fake's answers with those files, so the fake cannot drift from what was transcribed; acceptance tests named `test_done_check_*` run the connector end to end against the fake. The fetcher (talks to the API, stores raw responses) stays apart from the normaliser (reads raw files only, emits records).

[`docs/how-to/add-a-provider.md`](docs/how-to/add-a-provider.md) gives the steps in order, from the [connector contract](docs/reference/connector-contract.md) and a skeleton made by `tools/new_connector.py NAME`. A connector tests itself with the conformance kit, `meterline.conformance`.

## Related standards

meterline records are per-actor analytics, not billing lines: one record says how much one actor used one product and model in one period. Two public standards sit nearby.

- **OpenTelemetry GenAI semantic conventions** (status: Development, no release yet) describe telemetry for each call to a model. They carry per-call token counts under names such as `gen_ai.usage.input_tokens`, and define no cost. They now live at <https://github.com/open-telemetry/semantic-conventions-genai>.
- **FOCUS** (FinOps Foundation; version 1.4 is the latest published release) is a format for billing data. Version 1.4 has no token columns; token direction, cache action and model properties are in the 1.5 working draft. See <https://focus.finops.org/focus-specification/>.

meterline does not export either format yet. [`docs/standards-mapping.md`](docs/standards-mapping.md) maps every record field to both (or says it has no equivalent), with the versions checked and the date, so an exporter is mechanical.

## Repo map

What each top-level file and folder holds:

```
README.md                           this file
LICENSE                             Apache License 2.0 (each package directory carries a copy)
SECURITY.md                         how to report a vulnerability, and which versions get fixes
CONTRIBUTING.md                     how to set up, change and submit
CHANGELOG.md                        what each release adds
packages/meterline/                 v2 record format, validation, v1 converter, writers
packages/meterline-anthropic/       connector, CLI, tests, synthetic fixtures
packages/meterline-anthropic-admin/   Anthropic Admin API connector, CLI, tests (fake API, synthetic rows)
packages/meterline-litellm/         LiteLLM connector, CLI, tests (fake proxy, synthetic rows)
packages/meterline-copilot/         GitHub Copilot connector, CLI, tests (fake GitHub API, synthetic rows)
packages/meterline-sim/             simulated organisation and usage generator, CLI, presets, price table, tests
packages/meterline-gold/            per-person, per-group and per-audience datasets from silver, CLI, example dashboard, tests
examples/                           three runnable examples on simulated data: FinOps KPIs, a static exec report, the daily job
docs/
  README.md                                     the index by goal: simulated data, a real account, a dashboard, a new provider
  tutorial.md                                   from the simulator to gold to the dashboard, with every command's output
  how-to/                                       configure a gold policy; add a provider
  glossary.md                                   every project term, defined once, and the evidence levels
  architecture.md                               the layers from the provider APIs to the audiences, as one diagram
  decisions/                                    one record per structural choice of the format, with its alternatives
  records.md                                    every record field, with its source per connector
  THREAT-MODEL.md                               assets, trust boundaries, STRIDE table, attack tree and assumptions
  reference/                                    generated reference: every gold dataset file, its columns, units and keys
  schemas/                                      JSON Schema of the silver kinds, the organisation files, every gold dataset and the audience rules
  PRIVACY.md                                    personal-data inventory, retention, erasure, roles and DPIA notes for adopters
  api-shapes/                                   the documented provider response shapes the fake APIs must match, and the five format-fit inputs
  gold-fixture/                                 a small committed input and output for gold
  dev/                                          maintainer material: the accepted specs (frozen) and which one is in force per package, change lists, analyses, plans, style guide, release steps; superseded specs in dev/history/
```

## Development

How the repository is tested and changed:

- **Tests:** set up and run them as in the [Quick start](#quick-start); the pytest summary line is the one place that says how many tests ran, and this README does not repeat it. The acceptance tests are the ones named `test_done_check_*`: `NN` for the v1.1 Anthropic spec, `F` and `A` for the v2 format and migration, `L` for LiteLLM, `C` for Copilot, `S` for the simulator and `G` for gold; `-k done_check` selects exactly those. Nothing in the suite touches the network, and time and sleep are injected, so the rate and back-off tests do not wait. The `parquet` extra of the Quick start install is what keeps the Parquet tests from skipping.
- **Fixtures:** synthetic only, about 50 invented users with `@example.com` addresses and about 3 months of data. They are generated by `packages/meterline-anthropic/tests/fixtures/generate.py` and committed. Never add an export from a real account, because it would put real people's emails and usage into a repository that others read.
- **Money:** `Decimal` end to end, never `float`, because sums must equal the provider's totals exactly ([decision 0001](docs/decisions/0001-decimal-money.md)). Tests scan for `float` on the cost path. The connector packages (`meterline-anthropic`, `meterline-anthropic-admin`, `meterline-litellm`, `meterline-copilot`) each scan their Python source for the name `float` and for float literals; the Anthropic and LiteLLM scans read every package's source. `meterline-sim` scans its price and billing modules for the word. `meterline-gold` scans its SQL for division, `double` and averages.
- **Spec changes:** an accepted spec is frozen. A finding goes into the change list and the next spec version, not into the accepted file.
- **Branches:** work on a branch and merge through a pull request. Stage files by name, never `git add -A`, so that no stray file, such as a key or a real export, is committed.

## Not affiliated

meterline is an independent project. It is not affiliated with, endorsed by or sponsored by Anthropic, GitHub, Microsoft or LiteLLM. Anthropic, Claude, GitHub, Copilot, Microsoft and LiteLLM are trademarks of their respective owners; the names appear here only to say which service a connector reads from.

## Known limits

These limits apply to meterline as a whole, grouped by kind. Each package README has its own Known limits section, next to the claims it qualifies.

**Mostly not verified.** As of 2026-10-30, only `meterline-anthropic-admin` has run against a live account, and that was a small Console organisation (one model, two days of usage; cache tokens, priority tier and several workspaces not yet seen). `meterline-anthropic`, `meterline-litellm` and `meterline-copilot` are at the evidence level built against documented shapes. Each connector README says what to do when a live response differs.

**Not in the source APIs.** Some facts no provider API gives, so no record holds them: for Anthropic, Claude Code used through Amazon Bedrock, seat fees and the effort level (see [the Anthropic README](packages/meterline-anthropic/README.md#known-limits)).

**Out of scope, by package.** What one package leaves out, another may do; each line says which:

- The connectors do no identity resolution, pricing from tokens or alerting: each record carries the provider's own ids, costs and counts. `meterline-gold` does the first two from files you supply (an identity map from the organisation files, and a reference cost from a price list) and computes alert events, which it never acts on.
- No package allocates seat fees to persons: `meterline-gold` reports seat cost per provider and month only.
- No package converts currencies, applies contract discounts or maps records to a consumer-specific table layout; gold refuses silver in more than one currency.

Each package README lists its own limits: [core](packages/meterline/README.md#known-limits), [Anthropic](packages/meterline-anthropic/README.md#known-limits), [Anthropic Admin](packages/meterline-anthropic-admin/README.md#known-limits), [LiteLLM](packages/meterline-litellm/README.md#known-limits), [Copilot](packages/meterline-copilot/README.md#known-limits), [simulator](packages/meterline-sim/README.md#known-limits) and [gold](packages/meterline-gold/README.md#known-limits).

## Security and data handling

Before running meterline on real data, read two documents. [`docs/PRIVACY.md`](docs/PRIVACY.md) says what personal data each layer holds, how long to keep it, how to erase one person, and the data-protection duties of whoever runs it. [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md) says what is worth protecting, what could go wrong and what stops it. In short:

- The API key is read from the environment only. It is never written to a file, a log line, a manifest, a raw page or the state file; tests check this.
- Logs carry counts and HTTP statuses, and no email address, with three known exceptions. The unknown-field report of every connector names a field the provider sent, which could look like an email, and never its value. A key under a map whose keys are data is written `*`. The Admin check on workspaces names an `api_key_id`. The gold identity-overlap error names a provider account id, which can be an email. The first two also reach `runs/<batch_id>.json`. [`docs/PRIVACY.md`](docs/PRIVACY.md#error-messages-that-name-an-identifier) has the details. Raw files and published records hold email addresses as the API returned them (records trimmed and lower-cased), so treat the data directory as sensitive.
- A withheld value can sometimes be worked out. `exec/seats_month.csv` withholds `active_seats` or `idle_seats` when a count or its complement falls under `k`, and it still shows `seats_billed` and `seats_on_eval_day`. `seats_billed` minus `idle_seats` is an estimate of `active_seats`, exact only in simple cases. Gold tests each count against `k` on its own, so treat the exec seat counts as a figure that gives a small group away only in part.
- The connectors, gold, the simulator and the two example scripts that write files (`examples/02-exec-report`, `examples/03-daily-job`) write every file readable by its owner only (files `0600`, folders `0700`), whatever your umask; tests check this. To share an output, change its permissions yourself after the run. All of them except `report.py` (which sets the mode of its one file explicitly) set the process umask to `077` for the duration of the write and put it back afterwards. A umask belongs to the whole process, so this is not thread-safe: files that another thread of the same process creates meanwhile are made private too, and two overlapping writes in one process can leave the wrong umask in place. Files are written to `<file>.tmp` and renamed into place. A symlink planted at `<file>.tmp` is followed: the write lands in the link's target and the finished file is then that link. The simulator, which builds a folder at `<folder>.tmp`, stops with an error instead. Only a process running as your account, or one that can already write in the folder, can plant such a link.
- Fixtures and examples are synthetic only, and keys stay out of git.
