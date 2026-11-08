-- meterline-gold sql 1.1
-- The exec form of seats_month, pools_month and costs_month (spec 7.6.1), then the grid datasets of phase 2:
-- groups_month in its central, exec and manager forms, comparison_groups and the person comparison (7.2-7.6).

-- users of a costs row: distinct resolved persons with person_day rows of its own (provider, provider_account_id, month)
CREATE OR REPLACE TABLE exec_cost_rank AS
SELECT c.month, c.provider, c.provider_account_id,
       u.users,
       (u.users < (SELECT k FROM policy_t)) AS hidden_first,
       CASE WHEN u.users < (SELECT k FROM policy_t) THEN 0
            ELSE row_number() OVER (PARTITION BY c.month, (u.users < (SELECT k FROM policy_t))
                                    ORDER BY u.users, c.provider, c.provider_account_id) END AS hide_rank
FROM costs_month c
JOIN (
  SELECT c2.month, c2.provider, c2.provider_account_id, count(DISTINCT p.person_key) AS users
  FROM costs_month c2
  LEFT JOIN person_day p
    ON p.month = c2.month AND p.provider = c2.provider AND p.provider_account_id = c2.provider_account_id AND p.identity_status = 'resolved'
  GROUP BY c2.month, c2.provider, c2.provider_account_id
) u ON u.month = c.month AND u.provider = c.provider AND u.provider_account_id = c.provider_account_id;

-- After hiding the rows of rank <= j, how many distinct users sit in hidden rows, per month.
CREATE OR REPLACE TABLE exec_cost_steps AS
SELECT s.month, s.j, count(DISTINCT p.person_key) AS hidden_users
FROM (
  SELECT month, unnest(range(0, n + 1)) AS j
  FROM (SELECT month, max(hide_rank) AS n FROM exec_cost_rank GROUP BY month) mx
) s
LEFT JOIN exec_cost_rank r ON r.month = s.month AND r.hide_rank <= s.j
LEFT JOIN person_day p
  ON p.month = r.month AND p.provider = r.provider AND p.provider_account_id = r.provider_account_id AND p.identity_status = 'resolved'
GROUP BY s.month, s.j;

-- Per month, stop at the first j whose hidden users number 0 or at least k; with none, hide every row.
CREATE OR REPLACE TABLE exec_cost_stop AS
SELECT s.month,
       coalesce(min(s.j) FILTER (WHERE s.hidden_users = 0 OR s.hidden_users >= (SELECT k FROM policy_t)),
                max(s.j)) AS stop_j
FROM exec_cost_steps s
GROUP BY s.month;

CREATE OR REPLACE TABLE exec_hidden AS
SELECT r.month, r.provider, r.provider_account_id, (r.hide_rank <= st.stop_j) AS hidden
FROM exec_cost_rank r
JOIN exec_cost_stop st ON st.month = r.month;

CREATE OR REPLACE TABLE exec_costs_month AS
SELECT c.month, c.provider, c.provider_account_id, c.currency,
       CASE WHEN h.hidden THEN NULL ELSE c.usage_cost_source END AS usage_cost_source,
       CASE WHEN h.hidden THEN NULL ELSE c.cost_source_basis END AS cost_source_basis,
       CASE WHEN h.hidden THEN NULL ELSE c.usage_cost_ref END AS usage_cost_ref,
       c.seat_cost,
       CASE WHEN h.hidden THEN NULL ELSE c.total_cost_ref END AS total_cost_ref,
       CASE WHEN h.hidden THEN NULL ELSE c.cost_basis_label END AS cost_basis_label,
       CASE WHEN h.hidden THEN NULL ELSE c.invoice_usage END AS invoice_usage,
       CASE WHEN h.hidden THEN NULL ELSE c.overlap_cost_ref END AS overlap_cost_ref,
       c.month_status, c.is_final,
       coalesce(h.hidden, false) AS hidden
FROM costs_month c
LEFT JOIN exec_hidden h ON h.month = c.month AND h.provider = c.provider AND h.provider_account_id = c.provider_account_id;

CREATE OR REPLACE TABLE exec_pools_month AS
SELECT p.month, p.provider, p.provider_account_id, p.seats_billed, p.included_per_seat, p.pool_credits,
       CASE WHEN h.hidden THEN NULL ELSE p.gross_credits END AS gross_credits,
       CASE WHEN h.hidden THEN NULL ELSE p.pool_used_credits END AS pool_used_credits,
       CASE WHEN h.hidden THEN NULL ELSE p.overage_credits END AS overage_credits,
       CASE WHEN h.hidden THEN NULL ELSE p.overage_amount END AS overage_amount,
       CASE WHEN h.hidden THEN NULL ELSE p.pool_used_share END AS pool_used_share,
       p.budget_amount,
       CASE WHEN h.hidden THEN NULL ELSE p.budget_state END AS budget_state,
       CASE WHEN h.hidden THEN NULL ELSE p.budget_crossed_on END AS budget_crossed_on,
       p.currency, p.is_final, p.month_status,
       coalesce(h.hidden, false) AS hidden
FROM pools_month p
LEFT JOIN exec_hidden h ON h.month = p.month AND h.provider = p.provider AND h.provider_account_id = p.provider_account_id;

CREATE OR REPLACE TABLE exec_seats_month AS
SELECT s.month, s.provider, s.provider_account_id, s.seat_source, s.eval_day, s.seats_billed, s.seats_on_eval_day,
       CASE WHEN s.active_seats >= (SELECT k FROM policy_t)
                 AND s.seats_billed - s.active_seats >= (SELECT k FROM policy_t)
            THEN s.active_seats END AS active_seats,
       CASE WHEN s.idle_seats >= (SELECT k FROM policy_t)
                 AND s.seats_on_eval_day - s.idle_seats >= (SELECT k FROM policy_t)
            THEN s.idle_seats END AS idle_seats,
       s.seats_reported_max, s.seat_price, s.seat_cost, s.currency, s.month_status, s.is_final,
       NOT (s.active_seats >= (SELECT k FROM policy_t)
            AND s.seats_billed - s.active_seats >= (SELECT k FROM policy_t)
            AND s.idle_seats >= (SELECT k FROM policy_t)
            AND s.seats_on_eval_day - s.idle_seats >= (SELECT k FROM policy_t)) AS hidden
FROM seats_month s;

-- dataset: exec/seats_month
SELECT month, provider, provider_account_id, seat_source, eval_day, seats_billed, seats_on_eval_day, active_seats, idle_seats,
       seats_reported_max, seat_price, seat_cost, currency, month_status, is_final, hidden
FROM exec_seats_month
ORDER BY month, provider, provider_account_id;

-- dataset: exec/pools_month
SELECT month, provider, provider_account_id, seats_billed, included_per_seat, pool_credits, gross_credits, pool_used_credits,
       overage_credits, overage_amount, pool_used_share, budget_amount, budget_state, budget_crossed_on, currency,
       is_final, month_status, hidden
FROM exec_pools_month
ORDER BY month, provider, provider_account_id;

-- dataset: exec/costs_month
SELECT month, provider, provider_account_id, currency, usage_cost_source, cost_source_basis, usage_cost_ref, seat_cost,
       total_cost_ref, cost_basis_label, invoice_usage, overlap_cost_ref, month_status, is_final, hidden
FROM exec_costs_month
ORDER BY month, provider, provider_account_id;

-- ---- groups_month (7.2) -------------------------------------------------------------------------------

-- Figures of every cell before suppression (A04 reads them); the subset rule of 7.2 is applied here.
CREATE OR REPLACE TABLE cell_ranked AS
SELECT m.month, m.cell_id, m.person_key, pm.cost_ref, pm.active_days, pm.is_final,
       row_number() OVER (PARTITION BY m.month, m.cell_id ORDER BY pm.cost_ref, m.person_key) AS rnk,
       count(*) OVER (PARTITION BY m.month, m.cell_id) AS n
FROM cell_members m
JOIN person_month pm ON pm.month = m.month AND pm.person_key = m.person_key;

CREATE OR REPLACE TABLE cell_figures AS
SELECT r.month, r.cell_id,
       max(r.n) AS persons,
       sum(r.active_days) AS active_days,
       sum(r.cost_ref) AS cost_ref,
       bool_and(r.is_final) AS is_final,
       max(r.cost_ref) FILTER (WHERE r.rnk = qceil(50 * r.n, 100) AND qceil(50 * r.n, 100) >= p.k
                                      AND r.n - qceil(50 * r.n, 100) >= p.k) AS p50_cost_ref,
       max(r.cost_ref) FILTER (WHERE r.rnk = qceil(75 * r.n, 100) AND qceil(75 * r.n, 100) >= p.k
                                      AND r.n - qceil(75 * r.n, 100) >= p.k) AS p75_cost_ref,
       max(r.cost_ref) FILTER (WHERE r.rnk = qceil(90 * r.n, 100) AND qceil(90 * r.n, 100) >= p.k
                                      AND r.n - qceil(90 * r.n, 100) >= p.k) AS p90_cost_ref,
       max(r.cost_ref) FILTER (WHERE r.rnk = qceil(99 * r.n, 100) AND qceil(99 * r.n, 100) >= p.k
                                      AND r.n - qceil(99 * r.n, 100) >= p.k) AS p99_cost_ref,
       CASE WHEN r.cell_id = 'org:' AND qceil(max(r.n), 10) >= max(p.k) AND max(r.n) - qceil(max(r.n), 10) >= max(p.k)
            THEN ratio4(sum(r.cost_ref) FILTER (WHERE r.rnk > r.n - qceil(r.n, 10)), sum(r.cost_ref)) END
         AS top_decile_share
FROM cell_ranked r
CROSS JOIN privacy_params p
GROUP BY r.month, r.cell_id;

CREATE OR REPLACE TABLE groups_month AS
SELECT i.month, i.cell_id, i.cell_kind, i.cell_key, i.depth, i.parent_cell_id,
       s.suppressed, s.reason AS suppression_reason,
       CASE WHEN NOT s.suppressed THEN f.persons END AS persons,
       CASE WHEN ap.cell_id IS NOT NULL THEN
         (SELECT CAST(count(*) AS BIGINT) FROM cell_members m JOIN person_flags pf
            ON pf.month = m.month AND pf.person_key = m.person_key
          WHERE m.month = i.month AND m.cell_id = i.cell_id AND pf.active) END AS active_persons,
       CASE WHEN NOT s.suppressed THEN f.active_days END AS active_days,
       CASE WHEN NOT s.suppressed THEN f.cost_ref END AS cost_ref,
       CASE WHEN NOT s.suppressed THEN f.p50_cost_ref END AS p50_cost_ref,
       CASE WHEN NOT s.suppressed THEN f.p75_cost_ref END AS p75_cost_ref,
       CASE WHEN NOT s.suppressed THEN f.p90_cost_ref END AS p90_cost_ref,
       CASE WHEN NOT s.suppressed THEN f.p99_cost_ref END AS p99_cost_ref,
       CASE WHEN NOT s.suppressed THEN f.top_decile_share END AS top_decile_share,
       CASE WHEN NOT s.suppressed THEN f.is_final END AS is_final
FROM cell_info i
JOIN cell_state s ON s.month = i.month AND s.cell_id = i.cell_id
JOIN cell_figures f ON f.month = i.month AND f.cell_id = i.cell_id
LEFT JOIN cell_active_published ap ON ap.month = i.month AND ap.cell_id = i.cell_id;

-- exec: the cells down to audiences.exec.max_depth (7.6)
CREATE OR REPLACE TABLE exec_groups_month AS
SELECT * FROM groups_month WHERE depth <= (SELECT exec_max_depth FROM policy_t);

-- manager: the unit cells a person heads on a month's evaluation day, and every unit cell below them (7.6)
CREATE OR REPLACE TABLE manager_keys AS
SELECT head_key AS manager_key FROM unit_head
UNION
SELECT manager_key FROM person_month WHERE manager_key IS NOT NULL;

CREATE OR REPLACE TABLE manager_groups_month AS
WITH own AS (
  SELECT DISTINCT h.head_key AS manager_key, h.month, 'unit:' || h.unit_id AS cell_id
  FROM unit_head h
  WHERE (SELECT tree_kind FROM policy_t) = 'unit'
), below AS (
  SELECT o.manager_key, o.month, o.cell_id FROM own o
  UNION
  SELECT o.manager_key, o.month, cl.descendant
  FROM own o
  JOIN cell_closure cl ON cl.month = o.month AND cl.ancestor = o.cell_id
)
SELECT b.manager_key, g.*
FROM below b
JOIN groups_month g ON g.month = b.month AND g.cell_id = b.cell_id
WHERE g.cell_kind = 'unit';

-- ---- comparison groups (7.5) --------------------------------------------------------------------------

CREATE OR REPLACE TABLE compare_ranked AS
SELECT c.month, c.compare_cell, c.person_key, pm.cost_ref,
       row_number() OVER (PARTITION BY c.month, c.compare_cell ORDER BY pm.cost_ref, c.person_key) AS rnk,
       count(*) OVER (PARTITION BY c.month, c.compare_cell) AS n
FROM compare_cells c
JOIN person_month pm ON pm.month = c.month AND pm.person_key = c.person_key;

CREATE OR REPLACE TABLE comparison_groups AS
SELECT r.month, r.compare_cell, max(r.n) AS persons,
       max(r.cost_ref) FILTER (WHERE r.rnk = qceil(50 * r.n, 100) AND qceil(50 * r.n, 100) >= p.k
                                      AND r.n - qceil(50 * r.n, 100) >= p.k) AS p50_cost_ref,
       max(r.cost_ref) FILTER (WHERE r.rnk = qceil(75 * r.n, 100) AND qceil(75 * r.n, 100) >= p.k
                                      AND r.n - qceil(75 * r.n, 100) >= p.k) AS p75_cost_ref,
       max(r.cost_ref) FILTER (WHERE r.rnk = qceil(90 * r.n, 100) AND qceil(90 * r.n, 100) >= p.k
                                      AND r.n - qceil(90 * r.n, 100) >= p.k) AS p90_cost_ref
FROM compare_ranked r
CROSS JOIN privacy_params p
GROUP BY r.month, r.compare_cell;

-- each person's own cost and band against the published cut points of their comparison cell
CREATE OR REPLACE TABLE person_comparison AS
SELECT pm.person_key, pm.month, c.compare_cell, pm.cost_ref AS own_cost_ref,
       CASE WHEN g.p50_cost_ref IS NULL THEN NULL
            WHEN pm.cost_ref <= g.p50_cost_ref THEN 'up_to_p50'
            WHEN g.p75_cost_ref IS NULL THEN 'above_p50'
            WHEN pm.cost_ref <= g.p75_cost_ref THEN 'p50_to_p75'
            WHEN g.p90_cost_ref IS NULL THEN 'above_p75'
            WHEN pm.cost_ref <= g.p90_cost_ref THEN 'p75_to_p90'
            ELSE 'above_p90' END AS comparison_band
FROM person_month pm
JOIN compare_cells c ON c.month = pm.month AND c.person_key = pm.person_key
JOIN comparison_groups g ON g.month = c.month AND g.compare_cell = c.compare_cell;

-- dataset: groups_month
SELECT month, cell_id, cell_kind, cell_key, depth, parent_cell_id, suppressed, suppression_reason, persons,
       active_persons, active_days, cost_ref, p50_cost_ref, p75_cost_ref, p90_cost_ref, p99_cost_ref,
       top_decile_share, is_final
FROM groups_month
ORDER BY month, cell_id;

-- Outside central a hidden cell carries the marker, not the reason (7.6.2).
-- dataset: exec/groups_month
SELECT month, cell_id, cell_kind, cell_key, depth, parent_cell_id, persons,
       active_persons, active_days, cost_ref, p50_cost_ref, p75_cost_ref, p90_cost_ref, p99_cost_ref,
       top_decile_share, is_final, suppressed AS hidden
FROM exec_groups_month
ORDER BY month, cell_id;

-- The marker, not the reason, as in exec/groups_month.
-- dataset: manager/groups_month
SELECT manager_key, month, cell_id, cell_kind, cell_key, depth, parent_cell_id,
       persons, active_persons, active_days, cost_ref, p50_cost_ref, p75_cost_ref, p90_cost_ref, p99_cost_ref,
       top_decile_share, is_final, suppressed AS hidden
FROM manager_groups_month
ORDER BY manager_key, month, cell_id;

-- dataset: manager/keys
SELECT manager_key FROM manager_keys ORDER BY manager_key;

-- dataset: comparison_groups
SELECT month, compare_cell, persons, p50_cost_ref, p75_cost_ref, p90_cost_ref
FROM comparison_groups
ORDER BY month, compare_cell;

-- dataset: comparison
SELECT person_key, month, compare_cell, own_cost_ref, comparison_band, (comparison_band IS NULL) AS hidden
FROM person_comparison
ORDER BY person_key, month;

-- ---- the phase 3 files: exec scenario summary (7.6.1) and manager named exceptions (7.6) ---------------------

CREATE OR REPLACE TABLE exec_scenario_summary AS
SELECT s.scenario_id, s.month, s.persons,
       CASE WHEN s.persons_stopped >= p.k AND s.persons - s.persons_stopped >= p.k THEN s.persons_stopped END
         AS persons_stopped,
       s.units,
       NOT (s.persons_stopped >= p.k AND s.persons - s.persons_stopped >= p.k) AS hidden
FROM scenario_summary s
CROSS JOIN policy_t p;

-- dataset: exec/scenario_summary
SELECT scenario_id, month, persons, persons_stopped, units, hidden
FROM exec_scenario_summary
ORDER BY scenario_id, month;

-- dataset: manager/named_exceptions
SELECT manager_key AS folder_key, month, rule_id, audience, person_key, manager_key, condition, threshold_text,
       value_type, v_count, v_decimal
FROM named_exceptions
WHERE audience = 'manager' AND manager_key IS NOT NULL
ORDER BY manager_key, month, rule_id, person_key;
