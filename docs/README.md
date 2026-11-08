# meterline documentation

An index by goal. Each entry is one line. New to the project? Read the [README](../README.md) first, then pick the goal you have.

## Try it on simulated data

- [Quick start: try it without a key](../README.md#try-it-without-a-key): install, simulate an organisation, build gold, in five commands and no key.
- [Tutorial](tutorial.md): the same steps with everything each command prints, ending in the example dashboard.
- [Simulator](../packages/meterline-sim/README.md): presets, seeds and the ground truth the simulator writes.

## Run a connector on a real account

- [The daily job](../examples/03-daily-job/README.md): run every connector from Python, then gold, with cron and systemd lines.
- [Integrate on the platform you have](integrate/README.md): five scenarios from one machine to a data lake and a warehouse, with the maturity of every step (the pages: [A](integrate/a-one-machine.md), [B](integrate/b-kubernetes.md), [C](integrate/c-lake-and-warehouse.md), [D](integrate/d-gold-in-warehouse.md), [E](integrate/e-agents.md)), a [container example](../examples/04-container/README.md) and a [minimal dashboard](../examples/05-dashboard/README.md). For a data lake and a warehouse, [scenario C](integrate/c-lake-and-warehouse.md) comes with a [warehouse layout on DuckDB](../examples/06-warehouse-duckdb/README.md) and [one message per person](../examples/07-person-messages/README.md).
- [Operate the daily job](how-to/operate-daily.md): the order of a run, which command to use, locking, how long a first backfill takes and where to look on failure.
- [Run a connector](../README.md#run-a-connector): create a key (the narrowest the provider offers), put it in the environment, run `backfill`, `daily`, `rebuild`.
- [Anthropic Enterprise analytics](../packages/meterline-anthropic/README.md): the Claude Enterprise Analytics API connector.
- [Anthropic Admin usage and cost](../packages/meterline-anthropic-admin/README.md): API usage per key and model, and billed cost, for a Console organisation.
- [LiteLLM proxy](../packages/meterline-litellm/README.md): the proxy's admin API.
- [GitHub Copilot](../packages/meterline-copilot/README.md): usage reports, billing and seats.
- [Privacy for adopters](PRIVACY.md): what personal data each layer holds, how long to keep it, how to erase one person.

## Build a dashboard from gold

- [FinOps KPIs](how-to/finops-kpis.md): eight questions answered with DuckDB over the audience folders, with what not to add and how hidden cells show.
- [Examples](../examples/01-sim-to-kpis/README.md): simulated data to KPIs, and a [static exec report](../examples/02-exec-report/README.md).
- [Gold package](../packages/meterline-gold/README.md): the `meterline-gold` command, its inputs and the audience folders it writes.
- [Configure the policy](how-to/configure-policy.md): group-size suppression, unit envelopes, scenarios.
- [Gold datasets](reference/gold-datasets.md): every file a build writes, with its columns, units, key and which cost column to sum.
- [Schemas](schemas/README.md): the machine-readable contract of every silver kind, organisation file, gold dataset and the audience rules.
- [Privacy for adopters](PRIVACY.md): which folder each reader may see, and why `delivery/` and `manifest.json` are never served.

A dashboard reads **gold**, from the audience folder of its reader. Silver is for building your own pipeline and holds per-person identifiers with no suppression.

## Add a provider

- [Adding a provider](how-to/add-a-provider.md): the steps in order, from the shape files to the conformance kit.
- [Connector contract](reference/connector-contract.md): every rule a connector keeps, each with the kit check that tests it, and the wiring checklist.
- [API shapes](api-shapes/README.md): the documented provider responses each fake API must match.
- [Record reference](records.md): the five record kinds a new connector emits, and what each field means.

## Reference

- [Records](records.md): every field of the five record kinds, its type, nullability and source per connector.
- [Gold datasets](reference/gold-datasets.md): the columns, units and keys of every gold file, and the rule for which cost column to sum.
- [Schemas](schemas/README.md): JSON Schema for the silver kinds, the organisation inputs, every gold dataset and the audience rules.
- [Glossary](glossary.md): every project term, defined once, and the evidence levels.
- [Standards mapping](standards-mapping.md): each record field against OpenTelemetry GenAI and FOCUS.
- [Gold fixture](gold-fixture/README.md): a small committed input and expected output for gold.

## Explanation

- [Architecture](architecture.md): the layers from the provider APIs to the audiences, as one diagram.
- [Decisions](decisions/README.md): one record per structural choice of the format, with the alternatives weighed.
- [Privacy](PRIVACY.md): the personal-data inventory, retention, erasure, roles and notes for a data-protection assessment.
- [Threat model](THREAT-MODEL.md): assets, trust boundaries, what could go wrong and what stops it.

## Maintainers

- Maintainer material (the accepted specs and which one is in force per package, change lists, analyses, build plans, the style guide and the release steps) is in `docs/dev/` of the maintainer's working repository; a file there that a document in this repository relies on is linked where it is used. Nothing there is needed to use meterline.
