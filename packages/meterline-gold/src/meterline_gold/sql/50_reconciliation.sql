-- meterline-gold sql 1.1
-- reconciliation_month and data_quality (spec 6.8, 6.9).

CREATE OR REPLACE TABLE actor_side AS
SELECT month, provider, provider_account_id,
       sum(cost_source) AS actor_cost_source,
       CASE WHEN count(DISTINCT cost_source_basis) > 1 THEN 'mixed' ELSE min(cost_source_basis) END
         AS cost_source_basis,
       sum(credits) AS actor_credits,
       sum(cost_ref) AS cost_ref
FROM person_day
GROUP BY month, provider, provider_account_id;

CREATE OR REPLACE TABLE org_side AS
SELECT month_label(period_start) AS month, provider, provider_account_id,
       sum(cost) AS org_cost_source,
       CASE WHEN count(DISTINCT CASE WHEN cost IS NOT NULL THEN cost_basis END) > 1 THEN 'mixed'
            ELSE min(CASE WHEN cost IS NOT NULL THEN cost_basis END) END AS org_cost_basis,
       sum(ai_credit) AS org_credits
FROM silver_org_totals
WHERE grain = 'day'
GROUP BY month_label(period_start), provider, provider_account_id;

CREATE OR REPLACE TABLE reconciliation_month AS
WITH keys AS (
  SELECT month, provider, provider_account_id FROM actor_side
  UNION
  SELECT month, provider, provider_account_id FROM org_side
), joined AS (
  SELECT k.month, k.provider, k.provider_account_id,
         a.actor_cost_source, a.cost_source_basis AS actor_basis, a.actor_credits, a.cost_ref,
         o.org_cost_source, o.org_cost_basis, o.org_credits,
         CASE WHEN o.org_cost_source IS NOT NULL AND a.actor_cost_source IS NOT NULL
                   AND o.org_cost_basis IS NOT DISTINCT FROM a.cost_source_basis
              THEN o.org_cost_source - a.actor_cost_source END AS unallocated_cost_source,
         iu.amount AS invoice_usage,
         isd.amount AS invoice_seats,
         s.seat_cost
  FROM keys k
  LEFT JOIN actor_side a ON a.month = k.month AND a.provider = k.provider AND a.provider_account_id = k.provider_account_id
  LEFT JOIN org_side o ON o.month = k.month AND o.provider = k.provider AND o.provider_account_id = k.provider_account_id
  LEFT JOIN invoice iu ON iu.month = k.month AND iu.provider = k.provider AND iu.provider_account_id = k.provider_account_id AND iu.kind = 'usage'
  LEFT JOIN invoice isd ON isd.month = k.month AND isd.provider = k.provider AND isd.provider_account_id = k.provider_account_id AND isd.kind = 'seats'
  LEFT JOIN seats_month s ON s.month = k.month AND s.provider = k.provider AND s.provider_account_id = k.provider_account_id
)
SELECT month, provider, provider_account_id,
       (SELECT currency FROM policy_t) AS currency,
       actor_cost_source,
       org_cost_source,
       org_cost_basis,
       unallocated_cost_source,
       CASE WHEN unallocated_cost_source < 0 THEN NULL
            ELSE ratio4(unallocated_cost_source, org_cost_source) END AS unreconciled_share,
       actor_credits,
       org_credits,
       org_credits - actor_credits AS unallocated_credits,
       cost_ref,
       cost_ref - actor_cost_source AS ref_vs_source_gap,
       ratio4(abs(cost_ref - actor_cost_source), actor_cost_source) AS ref_vs_source_share,
       invoice_usage,
       invoice_usage - org_cost_source AS invoice_usage_gap,
       invoice_seats,
       invoice_seats - seat_cost AS invoice_seats_gap
FROM joined;

-- data_quality: a long table, one row per (month, provider, provider_account_id, metric) (spec 6.9).
CREATE OR REPLACE TABLE dq_keys AS
SELECT DISTINCT month, provider, provider_account_id FROM person_day;

CREATE OR REPLACE TABLE dq_rows AS
SELECT month, provider, provider_account_id,
       count(*) AS rows_total,
       count(*) FILTER (WHERE identity_status = 'unresolved') AS rows_unresolved,
       count(DISTINCT actor_key) FILTER (WHERE identity_status = 'unresolved') AS actors_unresolved,
       coalesce(sum(cost_ref) FILTER (WHERE identity_status = 'unresolved'), CAST(0 AS DECIMAL(38,12)))
         AS cost_ref_unresolved,
       count(*) FILTER (WHERE identity_status = 'non_person') AS rows_non_person,
       coalesce(sum(cost_ref) FILTER (WHERE identity_status = 'non_person'), CAST(0 AS DECIMAL(38,12)))
         AS cost_ref_non_person,
       coalesce(sum(cost_ref), CAST(0 AS DECIMAL(38,12))) AS cost_ref_total,
       count(*) FILTER (WHERE cost_ref_status = 'unknown_model') AS rows_unknown_model,
       count(DISTINCT model) FILTER (WHERE cost_ref_status = 'unknown_model') AS models_unknown,
       count(*) FILTER (WHERE cost_ref_status = 'unpriced') AS rows_unpriced,
       count(*) FILTER (WHERE NOT is_final) AS rows_non_final,
       coalesce(sum(cost_ref) FILTER (WHERE NOT is_final), CAST(0 AS DECIMAL(38,12))) AS cost_ref_non_final
FROM person_day
GROUP BY month, provider, provider_account_id;

CREATE OR REPLACE TABLE dq_excluded AS
SELECT month, provider, provider_account_id, count(*) AS rows_end_user_excluded
FROM (
  SELECT month_label(period_start) AS month, provider, provider_account_id FROM silver_usage
  WHERE grain = 'day' AND actor_kind = 'end_user'
  UNION ALL
  SELECT month_label(period_start), provider, provider_account_id FROM silver_activity
  WHERE grain = 'day' AND actor_kind = 'end_user'
) t
GROUP BY month, provider, provider_account_id;

-- Current day-grain usage records whose key also has an earlier, final batch that differs.
CREATE OR REPLACE TABLE dq_revised AS
WITH ranked AS (
  SELECT *, max(batch_id) OVER (PARTITION BY provider, provider_account_id, scope, grain, period_start, actor_kind,
                                             actor_key, product, model) AS latest_batch
  FROM silver_usage_batches
)
SELECT month_label(c.period_start) AS month, c.provider, c.provider_account_id,
       count(DISTINCT c.actor_key || '|' || c.product || '|' || c.model || '|' || c.scope || '|'
                      || CAST(c.period_start AS VARCHAR)) AS final_rows_revised
FROM ranked c
JOIN silver_usage_batches e
  ON e.provider = c.provider AND e.provider_account_id = c.provider_account_id AND e.scope = c.scope AND e.grain = c.grain
 AND e.period_start = c.period_start AND e.actor_kind = c.actor_kind AND e.actor_key = c.actor_key
 AND e.product = c.product AND e.model = c.model
 AND e.batch_id < c.batch_id AND e.is_final AND e.content_hash <> c.content_hash
WHERE c.batch_id = c.latest_batch AND c.grain = 'day'
GROUP BY month_label(c.period_start), c.provider, c.provider_account_id;

-- Actors whose month-grain credits differ from the sum of their daily credits in the month.
CREATE OR REPLACE TABLE dq_credit_mismatch AS
SELECT month, provider, provider_account_id, count(*) AS month_credit_mismatches
FROM (
  SELECT mo.month, mo.provider, mo.provider_account_id, mo.actor_key,
         mo.credits AS month_credits, coalesce(dy.credits, CAST(0 AS DECIMAL(38,12))) AS day_credits
  FROM (SELECT month_label(period_start) AS month, provider, provider_account_id, actor_key, sum(ai_credit) AS credits
        FROM silver_usage WHERE grain = 'month' AND actor_kind <> 'end_user'
        GROUP BY month_label(period_start), provider, provider_account_id, actor_key) mo
  LEFT JOIN (SELECT month_label(period_start) AS month, provider, provider_account_id, actor_key, sum(ai_credit) AS credits
             FROM silver_usage WHERE grain = 'day' AND actor_kind <> 'end_user'
             GROUP BY month_label(period_start), provider, provider_account_id, actor_key) dy
    ON dy.month = mo.month AND dy.provider = mo.provider AND dy.provider_account_id = mo.provider_account_id AND dy.actor_key = mo.actor_key
) t
WHERE coalesce(month_credits, CAST(0 AS DECIMAL(38,12))) <> day_credits
GROUP BY month, provider, provider_account_id;

-- F-37: per (month, provider, org) the resolved rows that reach no person_month, and their reference cost.
CREATE OR REPLACE TABLE dq_orphans AS
SELECT month, provider, provider_account_id, count(*) AS rows_person_missing,
       coalesce(sum(cost_ref), CAST(0 AS DECIMAL(38,12))) AS cost_ref_person_missing
FROM person_orphans
GROUP BY month, provider, provider_account_id;

CREATE OR REPLACE TABLE data_quality AS
WITH base AS (
  SELECT k.month, k.provider, k.provider_account_id, r.*,
         coalesce(x.rows_end_user_excluded, 0) AS rows_end_user_excluded,
         coalesce(v.final_rows_revised, 0) AS final_rows_revised,
         coalesce(c.month_credit_mismatches, 0) AS month_credit_mismatches
  FROM dq_keys k
  JOIN dq_rows r ON r.month = k.month AND r.provider = k.provider AND r.provider_account_id = k.provider_account_id
  LEFT JOIN dq_excluded x ON x.month = k.month AND x.provider = k.provider AND x.provider_account_id = k.provider_account_id
  LEFT JOIN dq_revised v ON v.month = k.month AND v.provider = k.provider AND v.provider_account_id = k.provider_account_id
  LEFT JOIN dq_credit_mismatch c ON c.month = k.month AND c.provider = k.provider AND c.provider_account_id = k.provider_account_id
), fresh AS (
  SELECT provider, provider_account_id, max(d) AS latest_day
  FROM (
    SELECT provider, provider_account_id, period_start AS d FROM silver_usage WHERE grain = 'day'
    UNION ALL SELECT provider, provider_account_id, period_start FROM silver_activity WHERE grain = 'day'
    UNION ALL SELECT provider, provider_account_id, period_start FROM silver_seats WHERE grain = 'day'
    UNION ALL SELECT provider, provider_account_id, period_start FROM silver_assignments WHERE grain = 'day'
    UNION ALL SELECT provider, provider_account_id, period_start FROM silver_org_totals WHERE grain = 'day'
  ) t
  GROUP BY provider, provider_account_id
)
SELECT month, provider, provider_account_id, 'rows_total' AS metric, 'count' AS value_type, CAST(rows_total AS BIGINT) AS v_count,
       CAST(NULL AS DECIMAL(38,12)) AS v_decimal, CAST(NULL AS DATE) AS v_date FROM base
UNION ALL SELECT month, provider, provider_account_id, 'rows_unresolved', 'count', CAST(rows_unresolved AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'actors_unresolved', 'count', CAST(actors_unresolved AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'cost_ref_unresolved', 'money', NULL, cost_ref_unresolved, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'rows_non_person', 'count', CAST(rows_non_person AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'cost_ref_non_person', 'money', NULL, cost_ref_non_person, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'unallocated_share', 'ratio', NULL,
       coalesce(ratio4(cost_ref_unresolved + cost_ref_non_person, cost_ref_total), CAST(0 AS DECIMAL(38,12))), NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'rows_unknown_model', 'count', CAST(rows_unknown_model AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'models_unknown', 'count', CAST(models_unknown AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'rows_unpriced', 'count', CAST(rows_unpriced AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'rows_non_final', 'count', CAST(rows_non_final AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'non_final_share', 'ratio', NULL,
       coalesce(ratio4(cost_ref_non_final, cost_ref_total), CAST(0 AS DECIMAL(38,12))), NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'rows_end_user_excluded', 'count', CAST(rows_end_user_excluded AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'final_rows_revised', 'count', CAST(final_rows_revised AS BIGINT), NULL, NULL FROM base
UNION ALL SELECT month, provider, provider_account_id, 'month_credit_mismatches', 'count', CAST(month_credit_mismatches AS BIGINT), NULL, NULL FROM base
-- the next two metrics are written only where they are not zero, so a clean build has no row for them
UNION ALL SELECT month, provider, provider_account_id, 'rows_person_missing', 'count', CAST(rows_person_missing AS BIGINT), NULL, NULL
       FROM dq_orphans
UNION ALL SELECT month, provider, provider_account_id, 'cost_ref_person_missing', 'money', NULL, cost_ref_person_missing, NULL
       FROM dq_orphans
UNION ALL SELECT CAST(NULL AS VARCHAR), f.provider, f.provider_account_id, 'latest_day', 'date', NULL, NULL, f.latest_day FROM fresh f
UNION ALL SELECT CAST(NULL AS VARCHAR), f.provider, f.provider_account_id, 'lag_days', 'count',
       CAST(date_diff('day', f.latest_day, (SELECT as_of FROM build_period)) AS BIGINT), NULL, NULL FROM fresh f;

-- dataset: reconciliation_month
SELECT month, provider, provider_account_id, currency, actor_cost_source, org_cost_source, org_cost_basis,
       unallocated_cost_source, unreconciled_share, actor_credits, org_credits, unallocated_credits, cost_ref,
       ref_vs_source_gap, ref_vs_source_share, invoice_usage, invoice_usage_gap, invoice_seats, invoice_seats_gap
FROM reconciliation_month
ORDER BY month, provider, provider_account_id;

-- dataset: data_quality
SELECT month, provider, provider_account_id, metric, value_type, v_count, v_decimal, v_date
FROM data_quality
ORDER BY month NULLS FIRST, provider, provider_account_id, metric;
