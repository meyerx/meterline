# FinOps KPIs from gold

The questions a FinOps team asks, each answered with one DuckDB query over the CSV files of one gold audience folder. The queries live once, in [`examples/01-sim-to-kpis/kpis.sql`](https://github.com/meyerx/meterline/blob/main/examples/01-sim-to-kpis/kpis.sql), and this page includes them word for word; a test fails when the two differ. `examples/01-sim-to-kpis/kpis.py` runs them all on a gold folder.

Read the folder of your reader: `exec/` for an executive view, `central/` for the central team. Never read `delivery/` or `manifest.json` (see [`AGENTS.md`](https://github.com/meyerx/meterline/blob/main/AGENTS.md)). Column meanings, units and keys are in the [dataset reference](../reference/gold-datasets.md).

## Set up

The queries read one view per dataset file, named after the dataset (`costs_month`, `groups_month`, and so on) with typed columns, so money stays exact. `kpis.py` creates them from the dataset schemas, opens only files that `meterline_gold.servable` allows for the audience, and adds a column `hidden` that is always false to a central file, so one query runs on both folders. It also loads the price list you gave to the build as a table `token_prices` (`--prices`), because gold carries no price and the cache-savings query needs one. To try it on simulated data:

```sh
meterline-sim generate --preset tiny --seed 7 --out sim-out
export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
meterline-gold build --silver sim-out/tiny-s7-m6 --org-files sim-out/tiny-s7-m6/org --policy packages/meterline-gold/src/meterline_gold/examples/policy.example.yaml --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml --out gold
python examples/01-sim-to-kpis/kpis.py gold --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml
```

## How hidden cells show

Outside `central/`, a value that a privacy rule withholds is empty, and the row says so in its last column `hidden`. An empty value is withheld, never zero. In a query it is `NULL`. A sum over rows that include a hidden row would silently leave that row out, so each query that adds rows returns `NULL` for the group and a `withheld` flag instead of a partial total. Show a withheld figure as "withheld" and nothing else.

## Billed, consumption and reference cost

Three kinds of cost figure appear in gold, and they are not the same quantity:

- **Billed** is what the provider charges (a bill line). **Consumption** is the value of what was used, which may be covered by a plan. Both are the provider's own figure, in `cost_source`, and `cost_source_basis` says which. An estimate is a third basis.
- **Reference cost**, `cost_ref`, is usage priced by gold from the price list you supplied, in one currency, the same way for every provider.

Do: compare providers with `cost_ref` (the `spend_ref` columns), and read `cost_source` only within one basis.

Don't: add a billed figure to a consumption figure, add `cost_source` to `cost_ref`, or add `seat_cost` to a total that already holds it. The first query below shows `spend_source` and `spend_ref` side by side: they differ after a price change, and both are right on their own basis.

Two traps sit in `costs_month` (central and exec), and neither shows in the row.

- **A seat line can create a seat cost where there are no seats.** `prices.example.yaml` has a seat price for `anthropic` of 27.00 a month with no `provider_account_id`. A line with no `provider_account_id` matches every organisation of that provider. When a provider sends no seat assignments, gold counts its seats from the rows of `identities.csv` for that provider, for the persons employed that day, whatever the actor kind. The Admin connector sends no seat assignments, and a Console organisation has no seats, so an Admin-fed build shows a seat count and a `seat_cost` for it, and `total_cost_ref` includes that cost. On the invented organisation of the tests, 20 identity rows give 540.00. Remove the Anthropic seat line from the price list you use, unless the organisation pays per seat. Scoping the line with `provider_account_id` is not enough when the silver holds more than one Anthropic organisation: the seat row then has an empty `provider_account_id`, and the line still matches.
- **`usage_cost_ref` is a lower bound when a model has no price.** Gold sets the reference cost of a row from the price list. A row it cannot price has none: a model missing from the list, a model with no price for that day, or a row with no model. `costs_month` adds up the rows that have one, and shows `0.00` when none has. The row carries no flag, and `total_cost_ref` has the same gap. `central/data_quality.csv` counts the rows, as the metrics `rows_unpriced` and `rows_unknown_model`; the `person_month` columns of the same names count them per person. Check them before you use `usage_cost_ref` as a total. The exec files carry no count.

## Column traps

Two columns read wrong without a rule, and neither shows it in the file. The [dataset reference](../reference/gold-datasets.md) links here from both.

- **In `data_quality`, `value` is typed by `metric`, not by the column.** One text column holds counts (`rows_total`), amounts (`cost_ref_non_person`), ratios (`unallocated_share`) and dates (`latest_day`). Read `metric` first, keep the metrics of one type, then cast: `CAST(value AS DECIMAL(38,12))` after `WHERE metric IN ('cost_ref_non_person', 'cost_ref_unresolved')`. Casting the whole column fails on a date, and a sum across metrics adds unlike things.
- **In `units`, the file has one row per unit and validity period.** `unit_id` alone is not a key; `unit_id` and `valid_from` are. A unit that was renamed or moved has several rows, so a join on `unit_id` alone repeats people and inflates every total. Take the row valid on the day: `valid_from <= day AND (valid_to IS NULL OR valid_to >= day)`, using the person's `attribution_day` for a person-month, as the team query does.

## The KPIs

### Spend by provider and month

**Question.** How much did each provider cost each month?

**Folder and dataset.** `exec/`, dataset `costs_month` (the same query runs on `central/costs_month`).

```sql
SELECT month,
       provider,
       cost_source_basis AS basis,
       CASE WHEN bool_or(hidden) THEN NULL ELSE sum(usage_cost_source) END AS spend_source,
       CASE WHEN bool_or(hidden) THEN NULL ELSE sum(usage_cost_ref) END AS spend_ref,
       CASE WHEN bool_or(hidden) THEN NULL ELSE sum(total_cost_ref) END AS spend_ref_with_seats,
       bool_or(hidden) AS withheld
FROM costs_month
GROUP BY month, provider, cost_source_basis
ORDER BY month, provider, cost_source_basis;
```

**Do not add.** Rows of different `basis` (billed, consumption, estimate): `usage_cost_source` is the provider's own figure on its own basis, so sum it only within one basis. `spend_ref` is priced from your price list and can be summed across providers. `total_cost_ref` already holds the seat fee; never add `seat_cost` to it.

**Hidden cells.** A provider-month with `hidden = true` has no figure. The query returns `NULL` and `withheld = true` for it, never a partial sum: add up the shown rows only if you also show that some are withheld.

### Spend by model

**Question.** Which models cost the most?

**Folder and dataset.** `central/`, dataset `person_day`. There is no model-level file for exec, manager or person.

```sql
SELECT month,
       provider,
       model,
       sum(cost_ref) AS spend_ref,
       count(*) FILTER (WHERE cost_ref_status NOT IN ('priced', 'included')) AS rows_not_priced
FROM person_day
GROUP BY month, provider, model
ORDER BY month, provider, model;
```

**Do not add.** Never add `cost_ref` of unpriced rows as zero: `rows_not_priced` counts the rows that have no `cost_ref` (unpriced or unknown model), so the total is a lower bound while it is not 0. Do not add `cost_source` here; it mixes bases.

**Hidden cells.** `central/` withholds nothing, so there is no `hidden` column; the query still returns `hidden = false` through the view.

### Spend by unit

**Question.** What does each top-level unit cost?

**Folder and dataset.** `exec/`, dataset `groups_month`, cells with `cell_kind = 'unit'` at `depth = 1`.

```sql
SELECT month,
       cell_key AS unit_id,
       persons,
       cost_ref AS spend_ref,
       hidden AS withheld
FROM groups_month
WHERE cell_kind = 'unit' AND depth = 1
ORDER BY month, cell_key;
```

**Do not add.** Cells overlap: a unit cell contains its sub-unit cells, so never add cells of different depths. At one depth the cells of one tree add up to the organisation cell.

**Hidden cells.** A suppressed cell has `hidden = true` and every figure empty. Show it as withheld; do not fill it in from its parent minus its siblings.

### Spend by team

**Question.** How much did each team cost, and what does a department cost when some people sit on the department itself?

**Folder and dataset.** `central/`, datasets `person_month` and `units`.

```sql
WITH RECURSIVE placed AS (
    SELECT p.month, p.person_key, p.cost_ref, p.attribution_day,
           u.unit_id, u.name AS unit_name, u.kind AS unit_kind, u.parent_id
    FROM person_month p
    JOIN units u ON u.unit_id = p.unit_id
                AND u.valid_from <= p.attribution_day
                AND (u.valid_to IS NULL OR u.valid_to >= p.attribution_day)
),
climb AS (
    SELECT month, person_key, attribution_day, unit_id AS up_unit, unit_kind AS up_kind, parent_id, 0 AS steps
    FROM placed
    UNION ALL
    SELECT c.month, c.person_key, c.attribution_day, u.unit_id, u.kind, u.parent_id, c.steps + 1
    FROM climb c
    JOIN units u ON u.unit_id = c.parent_id
                AND u.valid_from <= c.attribution_day
                AND (u.valid_to IS NULL OR u.valid_to >= c.attribution_day)
),
department AS (
    SELECT month, person_key, up_unit AS department_id,
           row_number() OVER (PARTITION BY month, person_key ORDER BY steps) AS nearest
    FROM climb
    WHERE up_kind = 'department'
)
SELECT pl.month,
       pl.unit_id,
       pl.unit_name,
       pl.unit_kind,
       pl.unit_kind = 'team' AS is_team,
       d.department_id,
       count(*) AS persons,
       sum(pl.cost_ref) AS spend_ref
FROM placed pl
LEFT JOIN department d ON d.month = pl.month AND d.person_key = pl.person_key AND d.nearest = 1
GROUP BY pl.month, pl.unit_id, pl.unit_name, pl.unit_kind, d.department_id
ORDER BY pl.month, pl.unit_id;
```

**The team rule.** A team is a unit whose `kind` is `team`. A person sits on one unit in a month: the `unit_id` of `person_month`, read on the person's `attribution_day`. Some people sit on a `department`, `division` or `company` row, a head of department for example. They are not on a team, so the query keeps them on their own row with `is_team = false` and does not move them into a team. A department costs what its teams cost plus what the people on the department row cost: add up `spend_ref` by `department_id`, the nearest unit at or above the person's own with kind `department`. It is empty for a person above the departments.

**Do not add.** Rows with `is_team = true` alone, if you want a department total: that leaves out the people who sit on the department. Do not join `units` on `unit_id` alone either (see [Column traps](#column-traps)).

**Hidden cells.** `central/` withholds nothing. For an executive or a manager, spend by unit above reads `groups_month`, which hides a unit below the size `k`.

### Cost per active person

**Question.** What does one active person cost this month?

**Folder and dataset.** `exec/`, dataset `groups_month`, the organisation cell (`cell_kind = 'org'`).

```sql
SELECT month,
       cost_ref AS spend_ref,
       active_persons,
       CASE WHEN active_persons > 0 THEN round(cost_ref / active_persons, 2) END AS cost_per_active_person
FROM groups_month
WHERE cell_kind = 'org'
ORDER BY month;
```

**Do not add.** Do not divide by `persons`: that is cost per employed person. Do not average the cost per active person of several months; divide the summed cost by the summed active persons.

**Hidden cells.** `active_persons` is withheld, not zero, when it would expose a few people. On the tiny build it is empty in the first two months, and the query then gives `NULL`; show "withheld", not 0 and not an estimate.

### Seat utilisation and idle seats

**Question.** How many paid seats are used, and how many sit idle?

**Folder and dataset.** `exec/`, dataset `seats_month`.

```sql
SELECT month,
       provider,
       seats_billed,
       active_seats,
       idle_seats,
       CASE WHEN active_seats IS NOT NULL THEN round(active_seats * 100.0 / seats_billed, 1) END AS utilisation_pct,
       hidden AS withheld
FROM seats_month
ORDER BY month, provider;
```

**Do not add.** `active_seats` and `idle_seats` count different days (the whole month against the idle window), so they do not add up to `seats_billed`. Seat cost is a fee: it is in `costs_month.total_cost_ref`, not in any usage cost.

**Hidden cells.** `active_seats` or `idle_seats` is empty when it would expose a few people, and `hidden` is then `true`. The utilisation percentage is `NULL` for such a row; the other count may still be shown.

### Idle seat cost

**Question.** What do the idle seats cost?

**Folder and dataset.** `exec/`, dataset `seats_month`, the same folder as the seat utilisation query above. The same query runs on `central/seats_month`.

```sql
SELECT month,
       provider,
       provider_account_id,
       idle_seats,
       seat_price,
       idle_seats * seat_price AS idle_seat_cost,
       currency,
       hidden AND idle_seats IS NULL AS withheld
FROM seats_month
WHERE seat_source = 'assignments'
ORDER BY month, provider, provider_account_id;
```

**What it is.** `idle_seats` times `seat_price` for one provider, provider account and month: the fee paid for seat holders with no use in the idle window. It is a fee, as in `seats_month.seat_cost`, not usage.

**Only providers with a seat product.** The query keeps the rows whose seat holders come from the provider's own seat assignments (`seat_source = 'assignments'`, GitHub Copilot in the example data). A row whose seats gold counted from `identities.csv` (`seat_source = 'identities'`) gives no row at all, never a zero and never a cost. That is the Console organisation fed by the Admin connector: it has no seats, and the provider-wide `anthropic` seat line of the example price list only creates a seat count and a `seat_cost` for it (see the first trap under [Billed, consumption and reference cost](#billed-consumption-and-reference-cost)). Reading an idle cost from that price would invent a cost.

**Do not add.** Do not add `idle_seat_cost` to `seat_cost`, to `total_cost_ref` or to any usage cost: it is part of the seat fee already in `total_cost_ref`. A row with an empty `seat_price` has no cost (`NULL`, not 0): the price list has no seat line for it. Do not add rows of different `currency`.

**Hidden cells.** In `exec/`, `idle_seats` may be withheld (`hidden = true`, the cell is empty). The cost is then `NULL` and `withheld` is `true`; show "withheld", not 0. The other seat figures of the row may still be shown. On the tiny build all idle counts of the Copilot rows are withheld for an executive; `central/` withholds nothing.

### Cache share of input tokens

**Question.** What share of input tokens was read from a cache?

**Folder and dataset.** `central/`, dataset `person_month` (`tokens_cache_read` over `tokens_input_total`).

```sql
SELECT month,
       sum(tokens_cache_read) AS tokens_cache_read,
       sum(tokens_input_total) AS tokens_input_total,
       CASE WHEN sum(tokens_input_total) > 0
            THEN round(sum(tokens_cache_read) * 100.0 / sum(tokens_input_total), 1) END AS cache_share_pct
FROM person_month
GROUP BY month
ORDER BY month;
```

**Do not add.** Do not average the per-person `cache_rate`: sum the tokens, then divide. Providers that report no cache tokens leave them empty; the share then covers the reporting providers only.

Gold carries no cache price: the price list is an input of the build and its prices are in no gold file. The money a cache saved is the next query, which reads the price list itself.

**Hidden cells.** `central/` withholds nothing.

### Cache savings (reference estimate)

**Question.** How much did prompt caching save?

**Folder and dataset.** `central/`, dataset `person_day`, with the price list you gave to the build (the `token_prices` table).

```sql
SELECT d.month,
       d.provider,
       sum(d.tokens_cache_read) AS tokens_cache_read,
       sum(d.tokens_cache_read * (p.input_uncached - p.cache_read)) AS savings_ref_estimate,
       count(*) FILTER (WHERE d.tokens_cache_read > 0 AND p.model IS NULL) AS rows_without_price
FROM person_day d
ASOF LEFT JOIN token_prices p
       ON d.provider = p.provider AND d.model = p.model AND d.day >= p.valid_from
WHERE d.provider IN (SELECT provider FROM token_prices)
GROUP BY d.month, d.provider
ORDER BY d.month, d.provider;
```

**How it is computed.** For each row with cache-read tokens: the tokens times the price of an uncached input token minus the price of a cache-read token, both from the price list entry valid on that day. It is a **reference estimate**, priced from your list: it is not what the provider charged or saved. `rows_without_price` counts rows whose model has no entry in the list. They add nothing, so the figure is then a floor.

**Copilot is not in the result.** GitHub Copilot is priced in credits and has no cache price in the list, so a provider with no entry in `token_prices` is left out of the query. That is not zero savings, it is no figure.

**Do not add.** Do not subtract it from `cost_source` or `cost_ref`: it is the difference against paying the uncached price for those tokens, not a refund. Do not add it across providers priced on different lists.

**Hidden cells.** `central/` withholds nothing.

### Unallocated or unreconciled share

**Question.** How much of the provider's total could not be traced to a person?

**Folder and dataset.** `central/`, dataset `reconciliation_month`.

```sql
SELECT month,
       provider,
       org_cost_source,
       actor_cost_source,
       unallocated_cost_source,
       unreconciled_share
FROM reconciliation_month
ORDER BY month, provider;
```

**Do not add.** `org_cost_source` and `actor_cost_source` are on the provider's basis; `cost_ref` is a different basis and is not part of the comparison. `unreconciled_share` is empty when the two sides are on different bases; do not treat it as 0.

**Not the reference.** `unreconciled_share` is the organisation remainder only: it is not the reference unallocated share. Report [the reference unallocated share](#reference-unallocated-share), which also counts the cost of API keys, service accounts and actors no one could match.

**Hidden cells.** `central/` withholds nothing.

### Reference unallocated share

**Question.** What share of a provider's cost is not tied to a person, whatever the reason? This is the reference unallocated share: the one to use and report.

**Folder and dataset.** `central/`, datasets `reconciliation_month`, `person_day` and `data_quality`.

```sql
WITH actors AS (
    SELECT month,
           provider,
           sum(cost_source) FILTER (WHERE identity_status <> 'resolved') AS cost_not_a_person
    FROM person_day
    GROUP BY month, provider
),
quality AS (
    SELECT month,
           provider,
           sum(CAST(value AS DECIMAL(38,12))) AS not_a_person_cost_ref
    FROM data_quality
    WHERE metric IN ('cost_ref_non_person', 'cost_ref_unresolved')
    GROUP BY month, provider
)
SELECT r.month,
       r.provider,
       r.org_cost_source,
       a.cost_not_a_person,
       r.unallocated_cost_source,
       q.not_a_person_cost_ref,
       r.unallocated_cost_source IS NULL AS left_out,
       CAST(round((coalesce(a.cost_not_a_person, 0) + r.unallocated_cost_source) / nullif(r.org_cost_source, 0), 4) AS DECIMAL(18,4)) AS unallocated_share
FROM reconciliation_month r
LEFT JOIN actors a ON a.month = r.month AND a.provider = r.provider
LEFT JOIN quality q ON q.month = r.month AND q.provider = r.provider
ORDER BY r.month, r.provider;
```

**How it adds up.** `unallocated_share` is (`cost_not_a_person` + `unallocated_cost_source`) divided by `org_cost_source`, all in the provider's own cost. `cost_not_a_person` is the `cost_source` of the `person_day` rows whose `identity_status` is not `resolved`: API keys, service accounts, proxy users and actors no one could match. `unallocated_cost_source` is the part of the organisation total that no actor carries. `not_a_person_cost_ref` is the same first part as `data_quality` reports it, on the reference basis, to compare with.

**Two measures, one name.** Gold has a `data_quality` metric called `unallocated_share` (the not-a-person cost over the reference cost) and a `reconciliation_month` column `unreconciled_share` (the organisation remainder only). Neither is the reference unallocated share, which adds both parts; the dataset reference says so on both. Report this share, read the other two by name, and do not compare them.

**Left out.** A row with `left_out = true` has no share. When the organisation total and the actors' cost are on different bases, `reconciliation_month` leaves `unallocated_cost_source` empty, and so does this query. In the example data that is `github_copilot`: its organisation total is billed, its actors carry credit-priced consumption. The row stays, with an empty share. Never fill in a zero.

**Do not add.** Do not add `not_a_person_cost_ref` to the source-basis columns: it is on another basis and is there for comparison only.

**One provider at a time.** There is no organisation-wide reference unallocated share. The share is defined per provider and month, each on that provider's own cost, and providers' costs can be on different bases. It is not summed or averaged across providers. The same holds for the `data_quality` metric `unallocated_share`, which is not summed or averaged across providers either and is not a headline figure. On a dashboard, show one value per provider and month, as a row or a bar per provider. Show a left-out provider with an empty share, never 0.

**Hidden cells.** `central/` withholds nothing.

### Month-over-month growth

**Question.** How fast does spend grow?

**Folder and dataset.** `exec/`, dataset `costs_month`, all providers added.

```sql
WITH monthly AS (
    SELECT month,
           bool_or(hidden) AS withheld,
           sum(total_cost_ref) AS spend_ref_with_seats
    FROM costs_month
    GROUP BY month
)
SELECT month,
       CASE WHEN NOT withheld THEN spend_ref_with_seats END AS spend_ref_with_seats,
       CASE WHEN NOT withheld AND NOT lag(withheld) OVER (ORDER BY month) AND lag(spend_ref_with_seats) OVER (ORDER BY month) > 0
            THEN round((spend_ref_with_seats - lag(spend_ref_with_seats) OVER (ORDER BY month)) * 100.0
                       / lag(spend_ref_with_seats) OVER (ORDER BY month), 1) END AS growth_pct,
       withheld
FROM monthly
ORDER BY month;
```

**Do not add.** The last month may be running (`person_month.month_status`), so compare complete months. Do not compare a month with hidden rows: the query returns `NULL` for it and for the month after it.

**Hidden cells.** If any provider-month of a month is hidden, the month's total is withheld, so the growth is too.
