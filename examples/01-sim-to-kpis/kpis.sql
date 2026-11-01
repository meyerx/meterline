-- The FinOps KPIs of docs/how-to/finops-kpis.md, as DuckDB queries over the CSV files of one audience folder.
-- Each query reads views that kpis.py creates, one per dataset file, named after the dataset (costs_month, groups_month,
-- ...), with typed columns and, for a central file, a column hidden that is always false. A table token_prices holds the
-- price list given to the gold build (kpis.py --prices), with the price of one token. One block per KPI:
-- "-- name:" starts it, "-- title:" is its heading in the cookbook, "-- audience:" and "-- dataset:" say which folder and
-- file it reads, and a query ends at its ";".
-- The cookbook includes these blocks word for word; a test fails when the two differ.

-- name: spend_by_provider_month
-- title: Spend by provider and month
-- audience: exec
-- dataset: costs_month
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

-- name: spend_by_model
-- title: Spend by model
-- audience: central
-- dataset: person_day
SELECT month,
       provider,
       model,
       sum(cost_ref) AS spend_ref,
       count(*) FILTER (WHERE cost_ref_status NOT IN ('priced', 'included')) AS rows_not_priced
FROM person_day
GROUP BY month, provider, model
ORDER BY month, provider, model;

-- name: spend_by_unit
-- title: Spend by unit
-- audience: exec
-- dataset: groups_month
SELECT month,
       cell_key AS unit_id,
       persons,
       cost_ref AS spend_ref,
       hidden AS withheld
FROM groups_month
WHERE cell_kind = 'unit' AND depth = 1
ORDER BY month, cell_key;

-- name: spend_by_team
-- title: Spend by team
-- audience: central
-- dataset: person_month
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

-- name: cost_per_active_person
-- title: Cost per active person
-- audience: exec
-- dataset: groups_month
SELECT month,
       cost_ref AS spend_ref,
       active_persons,
       CASE WHEN active_persons > 0 THEN round(cost_ref / active_persons, 2) END AS cost_per_active_person
FROM groups_month
WHERE cell_kind = 'org'
ORDER BY month;

-- name: seat_utilisation
-- title: Seat utilisation and idle seats
-- audience: exec
-- dataset: seats_month
SELECT month,
       provider,
       seats_billed,
       active_seats,
       idle_seats,
       CASE WHEN active_seats IS NOT NULL THEN round(active_seats * 100.0 / seats_billed, 1) END AS utilisation_pct,
       hidden AS withheld
FROM seats_month
ORDER BY month, provider;

-- name: idle_seat_cost
-- title: Idle seat cost
-- audience: exec
-- dataset: seats_month
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

-- name: cache_share_of_input
-- title: Cache share of input tokens
-- audience: central
-- dataset: person_month
SELECT month,
       sum(tokens_cache_read) AS tokens_cache_read,
       sum(tokens_input_total) AS tokens_input_total,
       CASE WHEN sum(tokens_input_total) > 0
            THEN round(sum(tokens_cache_read) * 100.0 / sum(tokens_input_total), 1) END AS cache_share_pct
FROM person_month
GROUP BY month
ORDER BY month;

-- name: cache_savings
-- title: Cache savings (reference estimate)
-- audience: central
-- dataset: person_day
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

-- name: unreconciled_share
-- title: Unallocated or unreconciled share
-- audience: central
-- dataset: reconciliation_month
SELECT month,
       provider,
       org_cost_source,
       actor_cost_source,
       unallocated_cost_source,
       unreconciled_share
FROM reconciliation_month
ORDER BY month, provider;

-- name: one_unallocated_share
-- title: Reference unallocated share
-- audience: central
-- dataset: reconciliation_month
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

-- name: month_over_month_growth
-- title: Month-over-month growth
-- audience: exec
-- dataset: costs_month
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
