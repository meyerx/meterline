-- meterline-gold sql 1.1
-- Assertions A01-A08 (spec 9.5) and A10. Each query returns zero rows when it holds. A09 is a Python check (write.py).

-- assertion: A01
SELECT 'person_day' AS dataset FROM person_day
GROUP BY day, provider, provider_account_id, person_key, actor_key, product, model HAVING count(*) > 1
UNION ALL
SELECT 'person_day_activity' FROM person_day_activity
GROUP BY day, provider, provider_account_id, person_key, actor_key, product HAVING count(*) > 1
UNION ALL
SELECT 'person_month' FROM person_month GROUP BY month, person_key HAVING count(*) > 1
UNION ALL
SELECT 'seats_month' FROM seats_month GROUP BY month, provider, provider_account_id HAVING count(*) > 1
UNION ALL
SELECT 'pools_month' FROM pools_month GROUP BY month, provider, provider_account_id HAVING count(*) > 1
UNION ALL
SELECT 'costs_month' FROM costs_month GROUP BY month, provider, provider_account_id HAVING count(*) > 1
UNION ALL
SELECT 'reconciliation_month' FROM reconciliation_month GROUP BY month, provider, provider_account_id HAVING count(*) > 1
UNION ALL
SELECT 'data_quality' FROM data_quality GROUP BY month, provider, provider_account_id, metric HAVING count(*) > 1
UNION ALL
SELECT 'people' FROM people GROUP BY person_key, valid_from HAVING count(*) > 1
UNION ALL
SELECT 'units' FROM units GROUP BY unit_id, valid_from HAVING count(*) > 1
UNION ALL
SELECT 'identity_map' FROM identity_map GROUP BY provider, actor_kind, actor_key, valid_from HAVING count(*) > 1
-- F-37 and F-28: the other nine datasets of write.DATASET_KEYS (phase 2 and 3, and the directory)
UNION ALL
SELECT 'directory' FROM directory GROUP BY person_key HAVING count(*) > 1
UNION ALL
SELECT 'groups_month' FROM groups_month GROUP BY month, cell_id HAVING count(*) > 1
UNION ALL
SELECT 'comparison_groups' FROM comparison_groups GROUP BY month, compare_cell HAVING count(*) > 1
UNION ALL
SELECT 'comparison' FROM person_comparison GROUP BY person_key, month HAVING count(*) > 1
UNION ALL
SELECT 'scenario_persons' FROM scenario_persons GROUP BY scenario_id, month, person_key HAVING count(*) > 1
UNION ALL
SELECT 'scenario_units' FROM scenario_units GROUP BY scenario_id, month, unit_id HAVING count(*) > 1
UNION ALL
SELECT 'scenario_summary' FROM scenario_summary GROUP BY scenario_id, month HAVING count(*) > 1
UNION ALL
SELECT 'alert_events' FROM alert_events
GROUP BY day, event_type, scenario_id, subject_kind, subject_key, threshold HAVING count(*) > 1
UNION ALL
SELECT 'named_exceptions' FROM named_exceptions GROUP BY month, rule_id, person_key HAVING count(*) > 1;

-- assertion: A02
WITH s AS (
  SELECT provider, provider_account_id, month_label(period_start) AS month,
         sum(requests) AS requests,
         sum(tokens_input_uncached) AS tokens_input_uncached,
         sum(tokens_cache_read) AS tokens_cache_read,
         sum(tokens_cache_write) AS tokens_cache_write,
         sum(tokens_cache_write_5m) AS tokens_cache_write_5m,
         sum(tokens_cache_write_1h) AS tokens_cache_write_1h,
         sum(tokens_input_total) AS tokens_input_total,
         sum(tokens_output) AS tokens_output,
         sum(ai_credit) AS credits,
         sum(cost) AS cost_source
  FROM silver_usage
  WHERE grain = 'day' AND actor_kind <> 'end_user'
  GROUP BY provider, provider_account_id, month_label(period_start)
), g AS (
  SELECT provider, provider_account_id, month,
         sum(requests) AS requests,
         sum(tokens_input_uncached) AS tokens_input_uncached,
         sum(tokens_cache_read) AS tokens_cache_read,
         sum(tokens_cache_write) AS tokens_cache_write,
         sum(tokens_cache_write_5m) AS tokens_cache_write_5m,
         sum(tokens_cache_write_1h) AS tokens_cache_write_1h,
         sum(tokens_input_total) AS tokens_input_total,
         sum(tokens_output) AS tokens_output,
         sum(credits) AS credits,
         sum(cost_source) AS cost_source
  FROM person_day
  GROUP BY provider, provider_account_id, month
)
SELECT coalesce(s.provider, g.provider) AS provider, coalesce(s.provider_account_id, g.provider_account_id) AS provider_account_id,
       coalesce(s.month, g.month) AS month
FROM s FULL OUTER JOIN g ON s.provider = g.provider AND s.provider_account_id = g.provider_account_id AND s.month = g.month
WHERE s.requests IS DISTINCT FROM g.requests
   OR s.tokens_input_uncached IS DISTINCT FROM g.tokens_input_uncached
   OR s.tokens_cache_read IS DISTINCT FROM g.tokens_cache_read
   OR s.tokens_cache_write IS DISTINCT FROM g.tokens_cache_write
   OR s.tokens_cache_write_5m IS DISTINCT FROM g.tokens_cache_write_5m
   OR s.tokens_cache_write_1h IS DISTINCT FROM g.tokens_cache_write_1h
   OR s.tokens_input_total IS DISTINCT FROM g.tokens_input_total
   OR s.tokens_output IS DISTINCT FROM g.tokens_output
   OR s.credits IS DISTINCT FROM g.credits
   OR s.cost_source IS DISTINCT FROM g.cost_source;

-- assertion: A03
SELECT pm.month, pm.person_key, pm.cost_ref AS person_month_cost_ref, coalesce(d.c, 0) AS person_day_cost_ref
FROM person_month pm
LEFT JOIN (SELECT month, person_key, sum(cost_ref) AS c
           FROM person_day WHERE identity_status = 'resolved' GROUP BY month, person_key) d
  ON d.month = pm.month AND d.person_key = pm.person_key
WHERE pm.cost_ref IS DISTINCT FROM coalesce(d.c, 0);

-- assertion: A08
SELECT month, provider, provider_account_id, total_cost_ref, usage_cost_ref, seat_cost
FROM costs_month
WHERE total_cost_ref IS DISTINCT FROM usage_cost_ref + coalesce(seat_cost, 0);

-- assertion: A10
-- F-37: per month the usage cost of costs_month is the cost in person_month plus the cost that data_quality accounts
-- for (unresolved actors, non-person actors, and persons who have no person_month row).
SELECT c.month, c.cost_ref AS costs_month_usage_cost_ref, coalesce(p.cost_ref, 0) AS person_month_cost_ref,
       coalesce(q.cost_ref, 0) AS data_quality_cost_ref
FROM (SELECT month, sum(usage_cost_ref) AS cost_ref FROM costs_month GROUP BY month) c
LEFT JOIN (SELECT month, sum(cost_ref) AS cost_ref FROM person_month GROUP BY month) p ON p.month = c.month
LEFT JOIN (SELECT month, sum(v_decimal) AS cost_ref FROM data_quality
           WHERE metric IN ('cost_ref_unresolved', 'cost_ref_non_person', 'cost_ref_person_missing')
           GROUP BY month) q ON q.month = c.month
WHERE c.cost_ref IS DISTINCT FROM coalesce(p.cost_ref, 0) + coalesce(q.cost_ref, 0);

-- assertion: A04
SELECT f.month, f.cost_ref AS org_cost_ref, s.cost_ref AS person_month_cost_ref
FROM cell_figures f
JOIN (SELECT month, sum(cost_ref) AS cost_ref FROM person_month GROUP BY month) s ON s.month = f.month
WHERE f.cell_id = 'org:' AND f.cost_ref IS DISTINCT FROM s.cost_ref
UNION ALL
SELECT s.month, NULL, s.cost_ref
FROM (SELECT month, sum(cost_ref) AS cost_ref FROM person_month GROUP BY month) s
WHERE NOT EXISTS (SELECT 1 FROM cell_figures f WHERE f.month = s.month AND f.cell_id = 'org:');

-- assertion: A05
WITH size AS (
  SELECT month, cell_id, count(*) AS n FROM cell_members GROUP BY month, cell_id
), inter AS (
  SELECT a.month, a.cell_id AS a_id, b.cell_id AS b_id, count(*) AS n
  FROM cell_members a
  JOIN cell_members b ON b.month = a.month AND b.person_key = a.person_key AND a.cell_id < b.cell_id
  GROUP BY a.month, a.cell_id, b.cell_id
)
SELECT i.month, i.a_id, i.b_id
FROM inter i
JOIN size za ON za.month = i.month AND za.cell_id = i.a_id
JOIN size zb ON zb.month = i.month AND zb.cell_id = i.b_id
WHERE i.n < za.n AND i.n < zb.n
UNION ALL
-- v1.1: each cell's persons are a subset of its parent cell's persons
SELECT m.month, m.cell_id, t.parent_cell_id
FROM cell_members m
JOIN cell_tree t ON t.month = m.month AND t.cell_id = m.cell_id
WHERE t.parent_cell_id <> ''
  AND NOT EXISTS (SELECT 1 FROM cell_members p
                  WHERE p.month = m.month AND p.cell_id = t.parent_cell_id AND p.person_key = m.person_key);

-- assertion: A06
WITH size AS (
  SELECT month, cell_id, count(*) AS n FROM cell_members GROUP BY month, cell_id
), recv_in AS (
  SELECT m.month, m.cell_id, count(*) AS n
  FROM cell_members m
  JOIN cell_recv r ON r.month = m.month AND r.cell_id = m.cell_id AND r.person_key = m.person_key
  GROUP BY m.month, m.cell_id
), kids AS (
  SELECT t.month, t.parent_cell_id AS p, count(*) FILTER (WHERE s.suppressed) AS n_sup,
         count(*) FILTER (WHERE NOT s.suppressed) AS n_pub
  FROM cell_tree t
  JOIN cell_state s ON s.month = t.month AND s.cell_id = t.cell_id
  WHERE t.parent_cell_id <> ''
  GROUP BY t.month, t.parent_cell_id
), uncov AS (
  SELECT m.month, m.cell_id, m.person_key
  FROM cell_members m
  JOIN cell_state s ON s.month = m.month AND s.cell_id = m.cell_id AND s.suppressed
  LEFT JOIN (SELECT DISTINCT cl.month, cl.ancestor AS cell_id, dm.person_key
             FROM cell_closure cl
             JOIN cell_state ds ON ds.month = cl.month AND ds.cell_id = cl.descendant AND NOT ds.suppressed
             JOIN cell_members dm ON dm.month = cl.month AND dm.cell_id = cl.descendant) cov
    ON cov.month = m.month AND cov.cell_id = m.cell_id AND cov.person_key = m.person_key
  WHERE cov.person_key IS NULL
), pieces AS (
  SELECT d.month, d.cell_id AS p, 'direct' AS piece, d.person_key
  FROM cell_direct d JOIN cell_state s ON s.month = d.month AND s.cell_id = d.cell_id AND NOT s.suppressed
  UNION ALL
  SELECT t.month, t.parent_cell_id, 'child:' || t.cell_id, u.person_key
  FROM uncov u
  JOIN cell_tree t ON t.month = u.month AND t.cell_id = u.cell_id
  JOIN cell_state sp ON sp.month = t.month AND sp.cell_id = t.parent_cell_id AND NOT sp.suppressed
  WHERE t.parent_cell_id <> ''
), block AS (
  SELECT pc.month, pc.p AS cell_id,
         count(DISTINCT pc.person_key) AS block_n,
         count(DISTINCT pc.person_key) FILTER (WHERE r.person_key IS NULL) AS unknown_n,
         count(DISTINCT pc.piece) AS pieces,
         bool_and(pc.piece <> 'direct') AS only_children,
         count(DISTINCT pc.piece) FILTER (WHERE r.person_key IS NULL) AS unknown_pieces,
         bool_and(pc.piece <> 'direct') FILTER (WHERE r.person_key IS NULL) AS unknown_only_children
  FROM pieces pc
  LEFT JOIN cell_recv r ON r.month = pc.month AND r.cell_id = pc.p AND r.person_key = pc.person_key
  GROUP BY pc.month, pc.p
), active_covered AS (
  SELECT DISTINCT cl.month, cl.ancestor AS cell_id, m.person_key
  FROM cell_closure cl
  JOIN cell_active_published ap ON ap.month = cl.month AND ap.cell_id = cl.descendant
  JOIN cell_members m ON m.month = cl.month AND m.cell_id = cl.descendant
), active_rem AS (
  SELECT m.month, m.cell_id, f.active, (r.person_key IS NOT NULL) AS is_recv
  FROM cell_members m
  JOIN person_flags f ON f.month = m.month AND f.person_key = m.person_key
  LEFT JOIN active_covered c ON c.month = m.month AND c.cell_id = m.cell_id AND c.person_key = m.person_key
  LEFT JOIN cell_recv r ON r.month = m.month AND r.cell_id = m.cell_id AND r.person_key = m.person_key
  WHERE c.person_key IS NULL
), active_agg AS (
  SELECT month, cell_id,
         count(*) FILTER (WHERE active) AS a_with, count(*) FILTER (WHERE NOT active) AS i_with,
         count(*) FILTER (WHERE active AND NOT is_recv) AS a_without,
         count(*) FILTER (WHERE NOT active AND NOT is_recv) AS i_without
  FROM active_rem GROUP BY month, cell_id
), active_counts AS (
  SELECT m.month, m.cell_id, count(*) FILTER (WHERE f.active) AS n_active,
         count(*) FILTER (WHERE NOT f.active) AS n_inactive
  FROM cell_members m JOIN person_flags f ON f.month = m.month AND f.person_key = m.person_key
  GROUP BY m.month, m.cell_id
), live AS (
  SELECT m.month, m.cell_id, m.person_key
  FROM cell_members m
  JOIN cell_state s ON s.month = m.month AND s.cell_id = m.cell_id AND NOT s.suppressed
), diff_pairs AS (
  SELECT a.month, a.cell_id AS a_id, b.cell_id AS b_id, count(*) AS n
  FROM live a
  JOIN live b ON b.month = a.month AND b.person_key = a.person_key AND a.cell_id < b.cell_id
  GROUP BY a.month, a.cell_id, b.cell_id
)
-- step a (v1.1): two unsuppressed cells of one month that differ by 1 to k - 1 persons
SELECT d.month, d.a_id AS cell_id, 'step a' AS rule
FROM diff_pairs d
JOIN size za ON za.month = d.month AND za.cell_id = d.a_id
JOIN size zb ON zb.month = d.month AND zb.cell_id = d.b_id
CROSS JOIN policy_t p
WHERE za.n + zb.n - 2 * d.n >= 1 AND za.n + zb.n - 2 * d.n < p.k
UNION ALL
-- step 1: an unsuppressed cell under k, with or without its receivers
SELECT s.month, s.cell_id, 'step 1'
FROM cell_state s
JOIN size z ON z.month = s.month AND z.cell_id = s.cell_id
LEFT JOIN recv_in r ON r.month = s.month AND r.cell_id = s.cell_id
CROSS JOIN policy_t p
WHERE NOT s.suppressed AND (z.n < p.k OR z.n - coalesce(r.n, 0) < p.k)
UNION ALL
-- b1: a cell with exactly one suppressed child and an unsuppressed one
SELECT s.month, s.cell_id, 'b1'
FROM cell_state s
JOIN kids k ON k.month = s.month AND k.p = s.cell_id
WHERE k.n_sup = 1 AND k.n_pub >= 1
UNION ALL
-- b2: an unsuppressed cell whose block is under k, or is exactly one suppressed child's uncovered part
SELECT s.month, s.cell_id, 'b2'
FROM cell_state s
JOIN block b ON b.month = s.month AND b.cell_id = s.cell_id
CROSS JOIN policy_t p
WHERE NOT s.suppressed
  AND ((b.block_n >= 1 AND b.block_n < p.k) OR (b.unknown_n >= 1 AND b.unknown_n < p.k)
       OR (b.block_n >= 1 AND b.pieces = 1 AND b.only_children)
       OR (b.unknown_n >= 1 AND b.unknown_pieces = 1 AND b.unknown_only_children))
UNION ALL
-- 7.2: a published active_persons whose cell or active remainder fails
SELECT ap.month, ap.cell_id, 'active_persons'
FROM cell_active_published ap
CROSS JOIN policy_t p
JOIN active_counts c ON c.month = ap.month AND c.cell_id = ap.cell_id
LEFT JOIN active_agg a ON a.month = ap.month AND a.cell_id = ap.cell_id
WHERE c.n_active < p.k OR c.n_inactive < p.k
   OR (a.cell_id IS NOT NULL AND (a.a_with < p.k OR a.i_with < p.k OR a.a_without < p.k OR a.i_without < p.k));

-- assertion: A07
SELECT 'exec' AS audience, e.month, e.cell_id
FROM exec_groups_month e
LEFT JOIN groups_month g ON g.month = e.month AND g.cell_id = e.cell_id
WHERE g.cell_id IS NULL
   OR ROW(e.cell_kind, e.cell_key, e.depth, e.parent_cell_id, e.suppressed, e.suppression_reason, e.persons,
          e.active_persons, e.active_days, e.cost_ref, e.p50_cost_ref, e.p75_cost_ref, e.p90_cost_ref,
          e.p99_cost_ref, e.top_decile_share, e.is_final)
      IS DISTINCT FROM
      ROW(g.cell_kind, g.cell_key, g.depth, g.parent_cell_id, g.suppressed, g.suppression_reason, g.persons,
          g.active_persons, g.active_days, g.cost_ref, g.p50_cost_ref, g.p75_cost_ref, g.p90_cost_ref,
          g.p99_cost_ref, g.top_decile_share, g.is_final)
UNION ALL
SELECT 'manager:' || e.manager_key, e.month, e.cell_id
FROM manager_groups_month e
LEFT JOIN groups_month g ON g.month = e.month AND g.cell_id = e.cell_id
WHERE g.cell_id IS NULL
   OR ROW(e.cell_kind, e.cell_key, e.depth, e.parent_cell_id, e.suppressed, e.suppression_reason, e.persons,
          e.active_persons, e.active_days, e.cost_ref, e.p50_cost_ref, e.p75_cost_ref, e.p90_cost_ref,
          e.p99_cost_ref, e.top_decile_share, e.is_final)
      IS DISTINCT FROM
      ROW(g.cell_kind, g.cell_key, g.depth, g.parent_cell_id, g.suppressed, g.suppression_reason, g.persons,
          g.active_persons, g.active_days, g.cost_ref, g.p50_cost_ref, g.p75_cost_ref, g.p90_cost_ref,
          g.p99_cost_ref, g.top_decile_share, g.is_final);
