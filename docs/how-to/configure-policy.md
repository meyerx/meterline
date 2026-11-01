# How to write a gold policy file

The policy file tells `meterline-gold build` how to group people, how small a group may be before its figures are
hidden, which bands, budgets and alerts to compute, and which shadow scenarios to try. This guide writes one
for the simulator's `tiny` organisation, builds it, and shows what each part produces. It assumes you have done
the [tutorial](../tutorial.md), so `sim-out/tiny-s7-m6` exists and `METERLINE_GOLD_KEY` is set.

The complete list of keys, types and defaults is section 4 of the
[gold spec](../dev/meterline-gold-core-SPEC-v1.2-2026-10-15.md); the two example policies in
`packages/meterline-gold/src/meterline_gold/examples/` show every key in use. This guide covers the decisions
you make, in the order the file lists them.

## Steps

1. **Name the file and its currency.** `policy_format` is `1`. `name` is lower case with hyphens. `label` says in
   words what the file is. `currency` is the reference currency of every `cost_ref` figure and must equal the
   price list's currency.
2. **Choose the minimum group size.** `privacy.k` is the smallest number of people a published group figure may
   cover; it must be at least 2. A larger `k` hides more: with `k` = 5 a team of 4 shows no figures at all, and
   percentiles need more people still (P50 needs 10).
3. **Choose how people are grouped.** `privacy.tree` is `{kind: unit}` to follow the unit tree of `units.csv`, or
   `{kind: attributes, attributes: [...]}` to group a flat organisation by person attributes (`function`,
   `level`, `cost_center`, `population`, or `attrs.<key>` for a key listed in `people.attrs_keep`).
   `privacy.compare_by` names the attributes a person is compared on (their comparison group).
   `privacy.collapse` merges attribute values before any grouping, for example three small levels into one.
4. **Set the bands.** `bands.thresholds` cuts each person's month into named bands, from `"0"` upwards, on cost
   (`metric: cost_ref`) or tokens (`metric: tokens`). A person sees their own band, never another person's figure.
5. **Set seat, pool and alert values.** `seats.idle_after_days` says when an unused seat counts as idle.
   `pools.budgets` sets a monthly overage budget for a credit pool. `alerts.daily_breaker` writes an event when a
   person's daily cost passes it; `alerts.spike` compares a day with the trailing days.
6. **Choose what the exec audience sees.** `audiences.exec.max_depth` is the deepest level of the grid exec gets
   (0 is the whole organisation only).
7. **Name the exceptions you want listed.** Each `named_exceptions` entry lists the people who meet a condition,
   for `central` or for each person's `manager`. A cost condition (`month_cost_ref_above`) is for `central` only;
   the count conditions (`breaker_events_at_least`, `spike_events_at_least`) may go to managers.
8. **Add shadow scenarios.** A scenario is a rule you are considering, such as a monthly cap per person or an
   envelope per unit. Gold computes who would have been stopped, by how much and how many top-ups it would
   have taken. Nothing is enforced and nobody is notified.
9. **Build, and read what it gives.** A policy error stops the build with exit 2 and names the key; a valid
   policy gives a gold folder whose scenario and alert files you can read.

Amounts, thresholds and fractions are quoted decimal strings (`"40.00"`); counts are integers.

## Worked example

A policy for the tiny organisation: groups of at least 5, people compared within their function, a daily breaker
at 40, a monthly overage budget of 50 for the Copilot pool, and one scenario that caps each person at 300 a month
for levels L1 to L4 and 100 for everyone else, with an envelope of 1500 for the `beta` team. Every value is
invented. Save it as `sim-out/my-policy.yaml` (the `sim-out/` folder is ignored by git):

```yaml
policy_format: 1
name: tiny-team-caps
label: "Worked example from docs/how-to/configure-policy.md: invented values for the simulator's tiny organisation."
currency: USD
privacy:
  k: 5
  tree: {kind: unit}
  compare_by: [function]
bands:
  metric: cost_ref
  thresholds:
    - {band: none, from: "0"}
    - {band: light, from: "0.01"}
    - {band: regular, from: "50"}
    - {band: heavy, from: "500"}
seats:
  idle_after_days: 21
pools:
  budgets:
    - {provider: github_copilot, provider_account_id: "org:example-tiny", monthly_amount: "50.00"}
alerts:
  daily_breaker: "40.00"
audiences:
  exec: {max_depth: 1}
named_exceptions:
  - {id: high-month, audience: central, condition: month_cost_ref_above, threshold: "500.00"}
scenarios:
  - id: caps_by_level
    label: "A monthly cap per person: 300 for L1 to L4, 100 for L5"
    person_caps:
      default: "100.00"
      by: level
      values: {L1: "300.00", L2: "300.00", L3: "300.00", L4: "300.00"}
    unit_envelopes:
      values: {beta: "1500.00"}
    alert_fractions: ["0.8", "1.0"]
    top_up_amount: "50.00"
```

Build with it:

```sh
.venv/bin/meterline-gold build --silver sim-out/tiny-s7-m6 --org-files sim-out/tiny-s7-m6/org --policy sim-out/my-policy.yaml --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml --out sim-out/gold-mine
head -3 sim-out/gold-mine/central/scenario_summary.csv
```

The build prints the folder, the months and one line per dataset, with no warning this time: `beta` is a unit
of the tiny organisation. The first rows of the scenario summary:

```text
schema_version,scenario_id,month,persons,persons_stopped,persons_excess,persons_top_up_total,units,units_stopped,units_excess,units_top_up_total
2.0,caps_by_level,2026-04,20,5,1810.28950229,1900.00,1,0,0.00,0.00
2.0,caps_by_level,2026-05,19,6,2736.40508967,2950.00,1,0,0.00,0.00
```

Read it as: in April, 5 of 20 people would have reached their cap, together 1810.29 above it, and covering that
would have taken 1900.00 of top-ups at 50 each; the `beta` envelope was never reached. `central/alert_events.csv`
holds one row per breaker event and per scenario alert (at 80% and 100% of a cap), and
`central/named_exceptions.csv` lists the months above 500.

## When the policy is wrong

The build checks the whole file before it reads any data. Change one line of the example, giving the cost
exception to managers:
<!-- broken: audience: central, condition: month_cost_ref_above -> audience: manager, condition: month_cost_ref_above -->

```yaml
  - {id: high-month, audience: manager, condition: month_cost_ref_above, threshold: "500.00"}
```

and the build stops with exit 2, writes nothing, and names the entry:

```text
meterline-gold: input error: sim-out/my-policy.yaml: named_exceptions.0: Value error, month_cost_ref_above is allowed for audience central only
```

A cost figure for one named person goes to central only, because a manager could otherwise learn a person's
cost; counts of events are allowed for managers. Other common errors: an unknown key (every key is checked, so a
typo is an error, not ignored), an amount written as a number instead of a quoted string, thresholds that do not
go up, and a currency that differs from the price list's.

## Keep it out of git

A real policy file carries your thresholds and caps, which say something about your organisation. Keep it next
to your data, not in this repository; `.env` files and the `sim-out/` and `gold/` folders are ignored by git.
