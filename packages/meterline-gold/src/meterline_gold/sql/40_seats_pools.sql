-- meterline-gold sql 1.1
-- seats_month, pools_month and costs_month (spec 6.5, 6.6, 6.7).

-- Providers with a seats entry in the price list.
CREATE OR REPLACE TABLE seat_providers AS
SELECT DISTINCT provider FROM price_seats;

-- (provider, provider_account_id) rows that come from assignments: one per org with SeatAssignment records.
CREATE OR REPLACE TABLE seat_orgs AS
SELECT DISTINCT a.provider, a.provider_account_id, 'assignments' AS seat_source
FROM silver_assignments a
WHERE a.provider IN (SELECT provider FROM seat_providers)
UNION ALL
-- a seat provider without any assignment: one row from identities, with its only provider_account_id (or '')
SELECT p.provider,
       CASE WHEN count(DISTINCT o.provider_account_id) = 1 THEN min(o.provider_account_id) ELSE '' END AS provider_account_id,
       'identities' AS seat_source
FROM seat_providers p
LEFT JOIN (
  SELECT provider, provider_account_id FROM silver_usage
  UNION ALL SELECT provider, provider_account_id FROM silver_activity
  UNION ALL SELECT provider, provider_account_id FROM silver_seats
  UNION ALL SELECT provider, provider_account_id FROM silver_assignments
  UNION ALL SELECT provider, provider_account_id FROM silver_org_totals
) o ON o.provider = p.provider
WHERE p.provider NOT IN (SELECT provider FROM silver_assignments)
GROUP BY p.provider;

-- From assignments: the holders of a day are the actors of the latest snapshot day on or before it.
CREATE OR REPLACE TABLE seat_snapshot_day AS
SELECT w.month, w.day, s.provider, s.provider_account_id, max(g.snap) AS snap
FROM window_days w
CROSS JOIN (SELECT DISTINCT provider, provider_account_id FROM silver_assignments) s
JOIN (SELECT DISTINCT provider, provider_account_id, period_start AS snap FROM silver_assignments) g
  ON g.provider = s.provider AND g.provider_account_id = s.provider_account_id AND g.snap <= w.day
GROUP BY w.month, w.day, s.provider, s.provider_account_id;

CREATE OR REPLACE TABLE seat_holders AS
SELECT d.month, d.day, d.provider, d.provider_account_id, coalesce(i.person_key, g.actor_key) AS holder
FROM seat_snapshot_day d
JOIN silver_assignments g
  ON g.provider = d.provider AND g.provider_account_id = d.provider_account_id AND g.period_start = d.snap
LEFT JOIN org_identities i
  ON i.provider = g.provider AND i.actor_kind = g.actor_kind AND i.actor_key = g.actor_key
 AND i.method <> 'non_person' AND i.valid_from <= d.day AND (i.valid_to IS NULL OR i.valid_to >= d.day)
WHERE d.provider IN (SELECT provider FROM seat_providers)
UNION ALL
-- From identities: persons with an identity row of the provider who are employed that day.
SELECT w.month, w.day, o.provider, o.provider_account_id, i.person_key AS holder
FROM seat_orgs o
JOIN org_identities i ON i.provider = o.provider AND i.method <> 'non_person' AND i.person_key IS NOT NULL
JOIN window_days w ON i.valid_from <= w.day AND (i.valid_to IS NULL OR i.valid_to >= w.day)
JOIN org_people p ON p.person_key = i.person_key AND p.valid_from <= w.day
                 AND (p.valid_to IS NULL OR p.valid_to >= w.day)
WHERE o.seat_source = 'identities';

-- The days on which a holder used a provider (person_day and person_day_activity, resolved or not).
CREATE OR REPLACE TABLE provider_active AS
SELECT provider, coalesce(person_key, actor_key) AS holder, day FROM person_day
UNION
SELECT provider, coalesce(person_key, actor_key) AS holder, day FROM person_day_activity;

CREATE OR REPLACE TABLE seat_counts AS
SELECT h.month, h.provider, h.provider_account_id,
       count(DISTINCT h.holder) AS seats_billed,
       count(DISTINCT CASE WHEN h.day = m.eval_day THEN h.holder END) AS seats_on_eval_day,
       count(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM provider_active a
                                        WHERE a.provider = h.provider AND a.holder = h.holder
                                          AND a.day >= m.first_day AND a.day <= m.eval_day)
                           THEN h.holder END) AS active_seats,
       count(DISTINCT CASE WHEN h.day = m.eval_day AND NOT EXISTS (
                                  SELECT 1 FROM provider_active a
                                  WHERE a.provider = h.provider AND a.holder = h.holder
                                    AND a.day > m.eval_day - CAST((SELECT idle_after_days FROM policy_t) AS INTEGER)
                                    AND a.day <= m.eval_day)
                           THEN h.holder END) AS idle_seats
FROM seat_holders h
JOIN months m ON m.month = h.month
GROUP BY h.month, h.provider, h.provider_account_id;

-- The seats entry in force on the window's first day: an entry of the row's own org wins over a null one.
CREATE OR REPLACE TABLE seat_price_entry AS
SELECT month, provider, provider_account_id, per_month, included_credits
FROM (
  SELECT m.month, o.provider, o.provider_account_id, s.per_month, s.included_credits,
         row_number() OVER (PARTITION BY m.month, o.provider, o.provider_account_id
                            ORDER BY CASE WHEN o.provider_account_id <> '' AND s.provider_account_id = o.provider_account_id THEN 1 ELSE 0 END DESC,
                                     s.valid_from DESC, s.per_month DESC,
                                     s.included_credits DESC NULLS LAST, s.provider_account_id NULLS LAST) AS rn
  FROM months m
  CROSS JOIN seat_orgs o
  JOIN price_seats s
    ON s.provider = o.provider AND (s.provider_account_id = o.provider_account_id OR s.provider_account_id IS NULL OR o.provider_account_id = '')
   AND s.valid_from <= m.first_day
) t
WHERE rn = 1;

CREATE OR REPLACE TABLE seats_month AS
SELECT m.month, o.provider, o.provider_account_id, o.seat_source, m.eval_day,
       coalesce(c.seats_billed, 0) AS seats_billed,
       coalesce(c.seats_on_eval_day, 0) AS seats_on_eval_day,
       coalesce(c.active_seats, 0) AS active_seats,
       coalesce(c.idle_seats, 0) AS idle_seats,
       (SELECT max(s.seats) FROM silver_seats s
        WHERE s.provider = o.provider AND s.provider_account_id = o.provider_account_id
          AND s.period_start >= m.first_day AND s.period_start <= m.eval_day) AS seats_reported_max,
       e.per_month AS seat_price,
       coalesce(c.seats_billed, 0) * e.per_month AS seat_cost,
       (SELECT currency FROM policy_t) AS currency,
       e.included_credits AS included_credits
FROM months m
CROSS JOIN seat_orgs o
LEFT JOIN seat_counts c ON c.month = m.month AND c.provider = o.provider AND c.provider_account_id = o.provider_account_id
LEFT JOIN seat_price_entry e ON e.month = m.month AND e.provider = o.provider AND e.provider_account_id = o.provider_account_id;

-- Pooled credits: the billing records are the current day-grain, billed OrgTotals in the window.
CREATE OR REPLACE TABLE pool_billing AS
SELECT s.month, s.provider, s.provider_account_id, t.period_start, t.cost, t.ai_credit, t.is_final,
       t.extra_discount_quantity, t.extra_net_quantity
FROM seats_month s
JOIN months m ON m.month = s.month
JOIN silver_org_totals t
  ON t.provider = s.provider AND t.provider_account_id = s.provider_account_id AND t.grain = 'day' AND t.cost_basis = 'billed'
 AND t.period_start >= m.first_day AND t.period_start <= m.eval_day
WHERE s.included_credits IS NOT NULL;

CREATE OR REPLACE TABLE pool_daily AS
SELECT month, provider, provider_account_id, period_start AS day,
       sum(sum(cost)) OVER (PARTITION BY month, provider, provider_account_id ORDER BY period_start) AS cumulative_cost
FROM pool_billing
GROUP BY month, provider, provider_account_id, period_start;

CREATE OR REPLACE TABLE pool_agg AS
SELECT month, provider, provider_account_id,
       count(*) AS n,
       sum(ai_credit) AS gross_credits,
       count(extra_discount_quantity) AS n_discount,
       sum(extra_discount_quantity) AS sum_discount,
       count(extra_net_quantity) AS n_net,
       sum(extra_net_quantity) AS sum_net,
       sum(cost) AS overage_amount,
       bool_and(is_final) AS is_final
FROM pool_billing
GROUP BY month, provider, provider_account_id;

CREATE OR REPLACE TABLE pools_month AS
SELECT s.month, s.provider, s.provider_account_id,
       s.seats_billed,
       s.included_credits AS included_per_seat,
       s.seats_billed * s.included_credits AS pool_credits,
       a.gross_credits,
       CASE WHEN a.n > 0 AND a.n_discount = a.n THEN a.sum_discount
            WHEN a.gross_credits IS NULL THEN NULL
            ELSE least(a.gross_credits, s.seats_billed * s.included_credits) END AS pool_used_credits,
       CASE WHEN a.n > 0 AND a.n_net = a.n THEN a.sum_net
            WHEN a.gross_credits IS NULL THEN NULL
            ELSE a.gross_credits - least(a.gross_credits, s.seats_billed * s.included_credits) END AS overage_credits,
       a.overage_amount,
       CAST(NULL AS DECIMAL(38,12)) AS pool_used_share,
       b.monthly_amount AS budget_amount,
       CAST(NULL AS VARCHAR) AS budget_state,
       (SELECT min(d.day) FROM pool_daily d
        WHERE d.month = s.month AND d.provider = s.provider AND d.provider_account_id = s.provider_account_id
          AND b.monthly_amount IS NOT NULL AND d.cumulative_cost > b.monthly_amount) AS budget_crossed_on,
       s.currency,
       coalesce(a.is_final, FALSE) AS is_final
FROM seats_month s
LEFT JOIN pool_agg a ON a.month = s.month AND a.provider = s.provider AND a.provider_account_id = s.provider_account_id
LEFT JOIN policy_budgets b ON b.provider = s.provider AND b.provider_account_id = s.provider_account_id
WHERE s.included_credits IS NOT NULL;

UPDATE pools_month
SET pool_used_share = ratio4(pool_used_credits, pool_credits),
    budget_state = CASE WHEN budget_amount IS NULL THEN 'none'
                        WHEN budget_crossed_on IS NOT NULL THEN 'crossed'
                        ELSE 'within' END;

-- costs_month (spec 6.7): every (provider, org) with person_day rows or a seats_month row.
CREATE OR REPLACE TABLE costs_month AS
WITH usage AS (
  SELECT month, provider, provider_account_id,
         sum(cost_source) AS usage_cost_source,
         CASE WHEN count(DISTINCT cost_source_basis) > 1 THEN 'mixed' ELSE min(cost_source_basis) END
           AS cost_source_basis,
         coalesce(sum(cost_ref), CAST(0 AS DECIMAL(38,12))) AS usage_cost_ref
  FROM person_day
  GROUP BY month, provider, provider_account_id
), keys AS (
  SELECT month, provider, provider_account_id FROM usage
  UNION
  SELECT month, provider, provider_account_id FROM seats_month
)
SELECT k.month, k.provider, k.provider_account_id,
       (SELECT currency FROM policy_t) AS currency,
       u.usage_cost_source,
       u.cost_source_basis,
       coalesce(u.usage_cost_ref, CAST(0 AS DECIMAL(38,12))) AS usage_cost_ref,
       s.seat_cost,
       coalesce(u.usage_cost_ref, CAST(0 AS DECIMAL(38,12))) + coalesce(s.seat_cost, CAST(0 AS DECIMAL(38,12)))
         AS total_cost_ref,
       CASE u.cost_source_basis
         WHEN 'billed' THEN 'billed amount, as invoiced'
         WHEN 'consumption' THEN 'usage value, not the invoice'
         WHEN 'estimate' THEN 'estimate, not the invoice'
         WHEN 'mixed' THEN 'mix of billed, usage-value and estimated amounts' END AS cost_basis_label,
       iu.amount AS invoice_usage
FROM keys k
LEFT JOIN usage u ON u.month = k.month AND u.provider = k.provider AND u.provider_account_id = k.provider_account_id
LEFT JOIN seats_month s ON s.month = k.month AND s.provider = k.provider AND s.provider_account_id = k.provider_account_id
LEFT JOIN invoice iu ON iu.month = k.month AND iu.provider = k.provider AND iu.provider_account_id = k.provider_account_id AND iu.kind = 'usage';

-- dataset: seats_month
SELECT month, provider, provider_account_id, seat_source, eval_day, seats_billed, seats_on_eval_day, active_seats, idle_seats,
       seats_reported_max, seat_price, seat_cost, currency
FROM seats_month
ORDER BY month, provider, provider_account_id;

-- dataset: pools_month
SELECT month, provider, provider_account_id, seats_billed, included_per_seat, pool_credits, gross_credits, pool_used_credits,
       overage_credits, overage_amount, pool_used_share, budget_amount, budget_state, budget_crossed_on, currency,
       is_final
FROM pools_month
ORDER BY month, provider, provider_account_id;

-- dataset: costs_month
SELECT month, provider, provider_account_id, currency, usage_cost_source, cost_source_basis, usage_cost_ref, seat_cost,
       total_cost_ref, cost_basis_label, invoice_usage
FROM costs_month
ORDER BY month, provider, provider_account_id;
