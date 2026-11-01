-- meterline-gold sql 1.1
-- Build period, months, organisation datasets and employment (spec 3.1, 5.3, 5.4, 6.1).

-- The build period: data_through, the first day of the first month, and as_of (spec 3.1).
CREATE OR REPLACE TABLE build_period AS
WITH day_records AS (
  SELECT period_start AS d FROM silver_usage WHERE grain = 'day'
  UNION ALL SELECT period_start FROM silver_activity WHERE grain = 'day'
  UNION ALL SELECT period_start FROM silver_seats WHERE grain = 'day'
  UNION ALL SELECT period_start FROM silver_assignments WHERE grain = 'day'
  UNION ALL SELECT period_start FROM silver_org_totals WHERE grain = 'day'
), pulls AS (
  SELECT pulled_date AS p FROM silver_usage
  UNION ALL SELECT pulled_date FROM silver_activity
  UNION ALL SELECT pulled_date FROM silver_seats
  UNION ALL SELECT pulled_date FROM silver_assignments
  UNION ALL SELECT pulled_date FROM silver_org_totals
)
SELECT max(d) AS data_through,
       CAST(date_trunc('month', min(d)) AS DATE) AS period_start,
       (SELECT max(p) FROM pulls) AS as_of
FROM day_records;

-- One row per calendar month of the build: window, evaluation day E, status (spec 3.1).
CREATE OR REPLACE TABLE months AS
SELECT month_label(first_day) AS month,
       first_day,
       last_day,
       CASE WHEN last_day <= data_through THEN 'complete' ELSE 'running' END AS month_status,
       least(last_day, data_through) AS eval_day,
       date_diff('day', first_day, last_day) + 1 AS days_in_month,
       date_diff('day', first_day, least(last_day, data_through)) + 1 AS days_elapsed
FROM (
  SELECT CAST(g.g AS DATE) AS first_day,
         CAST(g.g + INTERVAL 1 MONTH - INTERVAL 1 DAY AS DATE) AS last_day,
         b.data_through
  FROM build_period b, generate_series(b.period_start, b.data_through, INTERVAL 1 MONTH) AS g(g)
  WHERE b.data_through IS NOT NULL
) t;

-- The days of every month's window.
CREATE OR REPLACE TABLE window_days AS
SELECT m.month, CAST(g.d AS DATE) AS day, m.eval_day
FROM months m, generate_series(m.first_day, m.eval_day, INTERVAL 1 DAY) AS g(d);

-- A1 organisation datasets (spec 6.1).
CREATE OR REPLACE TABLE people AS
SELECT person_key, valid_from, valid_to, unit_id, manager_key, function, level, cost_center, population, attrs
FROM org_people;

CREATE OR REPLACE TABLE units AS
SELECT unit_id, valid_from, valid_to, dimension, key, name, kind, parent_id, function, cost_center, head_key
FROM org_units;

CREATE OR REPLACE TABLE identity_map AS
SELECT person_key, provider, method, actor_kind, actor_key, valid_from, valid_to
FROM org_identities;

-- one row per person: the directory is written after person_month, see 30_person_month.sql

-- Employment: per person and month the employed days of the window and the attribution day (spec 5.3, 5.4).
CREATE OR REPLACE TABLE employment AS
WITH spans AS (
  SELECT m.month, p.person_key,
         greatest(p.valid_from, m.first_day) AS s,
         least(coalesce(p.valid_to, DATE '9999-12-31'), m.eval_day) AS e
  FROM org_people p
  JOIN months m ON p.valid_from <= m.eval_day AND coalesce(p.valid_to, DATE '9999-12-31') >= m.first_day
)
SELECT month, person_key,
       sum(date_diff('day', s, e) + 1) AS employed_days,
       max(e) AS attribution_day
FROM spans
WHERE s <= e
GROUP BY month, person_key;

-- dataset: people
SELECT person_key, valid_from, valid_to, unit_id, manager_key, function, level, cost_center, population, attrs
FROM people
ORDER BY person_key, valid_from;

-- dataset: units
SELECT unit_id, valid_from, valid_to, dimension, key, name, kind, parent_id, function, cost_center, head_key
FROM units
ORDER BY unit_id, valid_from;

-- dataset: identity_map
SELECT person_key, provider, method, actor_kind, actor_key, valid_from, valid_to
FROM identity_map
ORDER BY provider, actor_kind, actor_key, valid_from;
