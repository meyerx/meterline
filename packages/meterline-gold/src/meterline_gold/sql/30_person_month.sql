-- meterline-gold sql 1.1
-- person_month (spec 6.4). Only resolved rows count toward a person.

-- The days and tools a person was active on, from both person-level datasets.
CREATE OR REPLACE TABLE person_active AS
SELECT month, person_key, day,
       CASE WHEN product = '' THEN provider ELSE provider || '/' || product END AS tool
FROM person_day WHERE identity_status = 'resolved'
UNION ALL
SELECT month, person_key, day,
       CASE WHEN product = '' THEN provider ELSE provider || '/' || product END AS tool
FROM person_day_activity WHERE identity_status = 'resolved';

CREATE OR REPLACE TABLE pm_active AS
SELECT month, person_key, count(DISTINCT day) AS active_days
FROM person_active
GROUP BY month, person_key;

CREATE OR REPLACE TABLE pm_tools AS
SELECT month, person_key,
       map(list(tool ORDER BY tool), list(days ORDER BY tool)) AS active_days_by_tool
FROM (SELECT month, person_key, tool, count(DISTINCT day) AS days FROM person_active GROUP BY month, person_key, tool) t
GROUP BY month, person_key;

CREATE OR REPLACE TABLE pm_usage AS
SELECT month, person_key,
       sum(requests) AS requests,
       sum(tokens_input_total) AS tokens_input_total,
       sum(tokens_output) AS tokens_output,
       sum(tokens_cache_read) AS tokens_cache_read,
       sum(credits) AS credits,
       coalesce(sum(cost_ref), CAST(0 AS DECIMAL(38,12))) AS cost_ref,
       count(*) FILTER (WHERE cost_ref_status = 'unpriced') AS rows_unpriced,
       count(*) FILTER (WHERE cost_ref_status = 'unknown_model') AS rows_unknown_model,
       coalesce(sum(cost_ref) FILTER (WHERE model_tier IN (SELECT tier FROM policy_premium_tiers)),
                CAST(0 AS DECIMAL(38,12))) AS premium_cost_ref,
       sum(tokens_cache_read) FILTER (WHERE tokens_cache_read IS NOT NULL AND tokens_input_total IS NOT NULL)
         AS cache_read_both,
       sum(tokens_input_total) FILTER (WHERE tokens_cache_read IS NOT NULL AND tokens_input_total IS NOT NULL)
         AS input_total_both,
       bool_and(is_final) AS rows_final
FROM person_day
WHERE identity_status = 'resolved'
GROUP BY month, person_key;

CREATE OR REPLACE TABLE pm_providers AS
SELECT month, person_key,
       map(list(provider ORDER BY provider), list(c ORDER BY provider)) AS cost_ref_by_provider
FROM (
  SELECT month, person_key, provider, sum(cost_ref) AS c
  FROM person_day WHERE identity_status = 'resolved'
  GROUP BY month, person_key, provider
  HAVING sum(cost_ref) IS NOT NULL
) t
GROUP BY month, person_key;

CREATE OR REPLACE TABLE pm_base AS
SELECT e.month, e.person_key, m.month_status, e.attribution_day,
       p.unit_id, p.manager_key, p.function, p.level, p.cost_center, p.population,
       e.employed_days,
       coalesce(a.active_days, 0) AS active_days,
       coalesce(t.active_days_by_tool, CAST(MAP {} AS MAP(VARCHAR, BIGINT))) AS active_days_by_tool,
       u.requests, u.tokens_input_total, u.tokens_output, u.tokens_cache_read, u.credits,
       coalesce(u.cost_ref, CAST(0 AS DECIMAL(38,12))) AS cost_ref,
       coalesce(v.cost_ref_by_provider, CAST(MAP {} AS MAP(VARCHAR, DECIMAL(38,12)))) AS cost_ref_by_provider,
       coalesce(u.rows_unpriced, 0) AS rows_unpriced,
       coalesce(u.rows_unknown_model, 0) AS rows_unknown_model,
       CASE WHEN coalesce(u.cost_ref, 0) = 0 THEN NULL
            ELSE ratio4(u.premium_cost_ref, u.cost_ref) END AS premium_share,
       ratio4(u.cache_read_both, u.input_total_both) AS cache_rate,
       CASE WHEN m.month_status = 'complete' AND coalesce(u.rows_final, TRUE) THEN TRUE ELSE FALSE END AS is_final,
       CASE WHEN m.month_status = 'running'
            THEN qdiv(coalesce(u.cost_ref, CAST(0 AS DECIMAL(38,12))) * m.days_in_month, m.days_elapsed, 2) END
         AS projected_cost_ref
FROM employment e
JOIN months m ON m.month = e.month
JOIN org_people p ON p.person_key = e.person_key AND p.valid_from <= e.attribution_day
                 AND (p.valid_to IS NULL OR p.valid_to >= e.attribution_day)
LEFT JOIN pm_active a ON a.month = e.month AND a.person_key = e.person_key
LEFT JOIN pm_tools t ON t.month = e.month AND t.person_key = e.person_key
LEFT JOIN pm_usage u ON u.month = e.month AND u.person_key = e.person_key
LEFT JOIN pm_providers v ON v.month = e.month AND v.person_key = e.person_key;

-- The intensity band: the last threshold whose lower bound is at or below the value (spec 4.1 bands).
CREATE OR REPLACE TABLE person_month AS
SELECT pm.*,
       (SELECT b.band
        FROM policy_bands b
        WHERE b.from_value <= CASE WHEN (SELECT bands_metric FROM policy_t) = 'tokens'
                                   THEN CAST(coalesce(pm.tokens_input_total, 0) + coalesce(pm.tokens_output, 0)
                                             AS DECIMAL(38,12))
                                   ELSE pm.cost_ref END
        ORDER BY b.ord DESC
        LIMIT 1) AS intensity_band_value
FROM pm_base pm;

-- F-37: resolved person_day rows of a person who has no person_month row for the month (the person is absent from
-- people.csv, or was not employed that month). Their cost is in no person_month; data_quality counts it.
CREATE OR REPLACE TABLE person_orphans AS
SELECT d.month, d.provider, d.provider_account_id, d.person_key, d.cost_ref
FROM person_day d
WHERE d.identity_status = 'resolved' AND d.person_key IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM person_month pm WHERE pm.month = d.month AND pm.person_key = d.person_key);

-- The directory (spec 6.1): one row per person with a person_month row.
CREATE OR REPLACE TABLE directory AS
SELECT d.person_key, d.given_name, d.family_name, d.email
FROM org_directory d
WHERE d.person_key IN (SELECT person_key FROM person_month);

-- dataset: person_month
SELECT month, person_key, month_status, attribution_day, unit_id, manager_key, function, level, cost_center,
       population, employed_days, active_days, active_days_by_tool, requests, tokens_input_total, tokens_output,
       tokens_cache_read, credits, cost_ref, cost_ref_by_provider, rows_unpriced, rows_unknown_model,
       premium_share, cache_rate, intensity_band_value AS intensity_band, is_final, projected_cost_ref
FROM person_month
ORDER BY month, person_key;

-- dataset: directory
SELECT person_key, given_name, family_name, email
FROM directory
ORDER BY person_key;
