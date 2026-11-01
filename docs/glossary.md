# Glossary

Every word this project gives a meaning of its own, defined once. The READMEs and guides link here instead of
defining a word again. Field names are in `code`; the [record reference](records.md) has every field, and
[architecture.md](architecture.md) shows how the layers below fit together.

## The layers

### raw

What a provider's API returned, before meterline changes anything. A connector stores every response page as
received, compressed, under `raw/<endpoint>/<window>/<pulled_at>.json.gz` in its data directory. The folder is
the *bronze* layer.

### bronze

The first layer: the `raw/` folder of a connector's data directory, holding every response page exactly as the
provider sent it, including fields meterline does not use. It is the evidence the records were built from and
the only input of `rebuild`. Nothing in a connector deletes or rewrites a bronze file.

### silver

The second layer: the records, in the common format of the [record reference](records.md), under
`records/<kind>/<batch_id>.jsonl` (and optionally Parquet). Connectors and the simulator write silver; gold reads
it. "Silver" and "the v2 records" name the same thing.

### gold

The third layer: the datasets `meterline-gold` builds from silver, the organisation files, a price list and a
policy file. They are per person, per group and per audience, already filtered for the audience that gets each
folder, with group-size suppression applied. A gold folder is the output of one build.

## Records

### record

One JSON line in a `records/<kind>/<batch_id>.jsonl` file. A record describes an aggregate: how much one actor
used one product and model over one period (a usage record), or a count for that period (activity, seats).
It is never one API call. There are five kinds: `UsageRecord`, `ActivityRecord`, `SeatSnapshot`,
`SeatAssignment` and `OrgTotal`.

### line

Three senses. A *JSONL line* is one record in a records file. *Lines of code* (`lines_added`, `lines_removed`)
are an activity count. A *line unit* is a unit of the reporting-line dimension in the organisation files (gold's
first unit dimension, `dimensions: [line]`). The word alone, in these docs, means a JSONL line, that is one
record.

### event

meterline has no per-call usage events: a record is an aggregate over a period. The word "event" means one of
two things here: an *alert event* in gold's `alert_events` (a threshold crossed on a day: a breaker, a spike or a
scenario alert), or an *event* of the simulator (a joiner, a reorganisation, a runaway agent, a price change or a
late correction it injects). Neither is a record.

### grain

The period one record covers: `day` (`period_start` equals `period_end`), `month` (the first to the last day of
one calendar month; it may end in the future while the month runs) or `window` (any other range). A reader never
adds records of different grains.

### window

Two senses. As a *grain*, a period that is neither one day nor one calendar month. As a *fetch window*, the range
of days one request asks for (the Anthropic connector walks 31-day windows); raw files are filed under it.

### actor

Who a record is about, given by `actor_kind` and `actor_id`. The kinds are `user`, `proxy_user`, `end_user`,
`service_account`, `api_key` and `unassigned` (usage the source attributes to no one). An actor is a provider's
identity: the same person can be several actors on several providers. Compare *user* (one actor kind) and
*person* (a human in the organisation files).

### user

The actor kind `user`: a person the provider itself knows, by the provider's own id, email or login. A user is
an actor; gold maps users to persons through `identities.csv`. `proxy_user` (the person a proxy charged a call
to) and `end_user` (a person named by the calling application) are separate actor kinds, and their records can
describe the same traffic, so they are never added together.

### person

A human in the organisation files (`people.csv`), with a unit, a manager and attributes. Gold resolves each
actor to a person through `identities.csv` and stands for the person by a keyed hash, the `person_key`.
Connectors know actors and users only; persons exist from gold on.

### batch

The records one run publishes, all sharing one `batch_id` (the run's `pulled_at` as `YYYYMMDDTHHMMSSZ`). A
re-pull of a period writes a new batch holding only the records that changed. A reader takes the record with the
latest `batch_id` for each record key (`meterline.latest_per_key`); earlier batches stay on disk.

### published

Three senses, kept apart. A connector *publishes* a batch when it writes the batch's records under `records/`;
a batch that fails a check publishes nothing. In gold, a cell is *published* when its figures are written; a
suppressed cell's row is still written, with its figures empty. The heading of gold spec section 7.3, "what is
published", means every row written, suppressed ones included. See [style.md](dev/style.md).

### provider account

The account at the provider that a record's data comes from, written in the record field `provider_account_id`:
the organization id of an Anthropic Analytics response, the label given with `--provider-account-id` for the Anthropic
Admin API, the label given with `--proxy-name` for a LiteLLM proxy, and `org:<slug>` or `enterprise:<slug>` for
GitHub Copilot. It is never the organisation that runs meterline: that is the *organisation* of the organisation
files and of the cell `org:` (see [cell](#cell)).

Retired names: before the first public release this field was called `org_id`, and the gold column `cell_kind` (the
kind of a cell) was called `group_id`. The option `--org-files` of `meterline-gold build` was called `--org`.

### scope

Two senses. The record field `scope` names a sub-division inside an organisation that a source reports apart;
it is `""` for every connector so far. The *scope* of a key or token is the permission it carries, such as
`read:analytics`.

### level

Several senses, always qualified in these docs. The *account level* of a Copilot connector run is
`organization` or `enterprise`. A person's `level` is an attribute in `people.csv` (for example `L4`). A
*grid level* is a depth in gold's grid of groups.

### cost_basis

What a record's cost means: `billed` (from the provider's billing system), `consumption` (a usage figure the
provider reports but does not invoice) or `estimate` (computed by a third party, such as a proxy, from a price
list). Costs of different bases are never compared or added as if they were one.

### cost_ref

Gold's *reference cost*: a cost gold computes itself from the tokens of a usage record and the price list you
supply, in the policy's currency. It makes providers comparable; it is not what anyone was invoiced. The
provider's own figure is kept beside it as the source cost.

### unreconciled_share

A column of `reconciliation_month`: the part of an organisation's total cost that the people's cost does not
account for (the total minus the sum of the actors' costs, divided by the total). It is not the `unallocated_share`
metric of `data_quality`, which is the part of the cost that belongs to no resolved person (unresolved plus
non-person actors). Neither is the figure to report: that is the
[reference unallocated share](how-to/finops-kpis.md#reference-unallocated-share) of the cookbook, which adds both parts
in the provider's own cost.

### unit

Two senses. An *organisational unit* is a row of `units.csv` (a company, a division, a team), arranged in a tree.
A *billing unit* is a key of `quantities`, such as `ai_credit`, for an amount that is not money.

### seat

A licence that lets one person use a product, such as a GitHub Copilot or a Claude Enterprise seat.
`SeatSnapshot` counts seats and active users per day; `SeatAssignment` records one person holding a seat on a day.

### pool

Credits included with seats and shared by the whole billing entity, as GitHub Copilot does. A person's use draws
on the pool; what the pool does not cover is billed as overage. A person's discount from the pool is not their
invoice.

## Gold and privacy

### audience

Who receives a gold folder: `central` (the team that runs meterline; everything, pseudonymous), `exec` (group
figures and costs, down to a set depth), each `manager` (the units they head, and named exceptions about their
people) and each `person` (their own figures and a band against their comparison group).

### cell

One group of people in one month in gold's fixed grid: the whole organisation, a unit and its subtree, or a
combination of attributes. Its `cell_id` starts with `org:`, `unit:` or `attr:`, and `cell_kind` says which of the
three it is (`org`, `unit` or `attr`). `groups_month` has one row per cell. A cell's figures are published only when the
privacy rules allow it.

### comparison group

The people a person is compared with, chosen by the policy's `privacy.compare_by` attributes (for example the
same function and level). A person sees a band against this group, never another person's figure.

### k

The minimum group size of the policy (`privacy.k`, at least 2). A figure about a group is published only when the
group holds at least `k` people, and only when no other published figure lets a reader work out a smaller group.

### suppression

Gold's rule that withholds a cell's figures in `groups_month`. In `central/` the row stays, with `suppressed` =
`true` and a `suppression_reason`: `k` (the cell is smaller than `k`), `differencing` (it differs from another
published cell by fewer than `k` people) or `secondary` (hidden so that another suppressed cell cannot be worked
out). Outside `central/` the same row has `hidden` = `true` and no reason. The gold README shows an example.

### hidden

The marker outside `central/`: in `exec/groups_month`, `manager/<key>/groups_month`, `exec/costs_month`,
`exec/pools_month`, `exec/seats_month`, `exec/scenario_summary` and a person's `comparison`, the last column
`hidden` is `true` when a privacy rule withholds a value of that row. It never says why. Cells of
`central/groups_month` are suppressed, with the reason; every other file says hidden. Both mean the value exists
but is withheld.

### scenario

A *shadow scenario* of the policy file: a rule being considered, such as a monthly cap per person. Gold computes
who would have been stopped and what top-ups it would have taken. Nothing is enforced and nobody is notified.

## Testing and evidence

### done-check

An acceptance check of a spec: a numbered item of the spec's done-check list, implemented as one or more tests
named `test_done_check_<id>_*` (for example `S03` for the simulator, `G08` for gold, `C01` for Copilot). Each was
run and shown failing before the code it checks was written. `pytest -k done_check` selects them all.

### check

Three senses, always qualified in these docs. A *done-check* is an acceptance test (above). A *validation check*
is a rule a connector applies to every record before it publishes a batch ([records.md](records.md#validation));
a failure publishes nothing. A gold *assertion* (`A01` to `A10`) is a rule gold checks during a build; a failure
stops the build and writes nothing.

### evidence levels

How far a connector has been checked against the real provider. Each connector's Status line names its level in
these words. A check beyond the level is named next to it and does not change it:

1. **built against documented shapes**: the connector's fake API answers in the shapes transcribed from the provider's documentation or public source code (`docs/api-shapes/`), and the connector passes its acceptance tests against that fake; no real response has been compared. As of 2026-10-30, `meterline-anthropic`, `meterline-litellm` and `meterline-copilot` are at this level; `meterline-litellm` was also checked against LiteLLM v1.106.6's own route code.
2. **verified by a recorded run**: a real response, recorded from a live account and reduced to synthetic values, has been replayed through the connector, and its records matched the totals of that response; the recording and its date are in the repository.
3. **verified live**: the connector has run against a live account on a named date, and its records matched the provider's own dashboard or invoice for the same period; the date and the comparison are recorded in the connector's README. As of 2026-10-30, `meterline-anthropic-admin` is at this level, on a small Console organisation (one model, two days of usage; cache tokens, the priority tier and several workspaces were not exercised).

A level is reached only with its artefact in the repository; a claim without one is removed.

### fixture

Test data committed to the repository, synthetic only. `docs/gold-fixture/` is the hand-built input of gold
whose expected outputs were computed by hand; each connector has fixtures for its fake API.

### ground truth

What the simulator knows about the organisation it invented: each person's profile, every injected event and the
expected totals, in `ground_truth.json`. Tests compare gold's output with it.

### connector

A package that reads one provider's API and writes silver records: `meterline-anthropic`, `meterline-anthropic-admin`,
`meterline-litellm`, `meterline-copilot`. Each has a *fetcher* (talks to the API, writes bronze) and a *normaliser* (reads bronze
only, writes silver).
