-- meterline-gold sql 1.1
-- Shadow scenarios (spec 8.1): who or which unit would have been stopped, the excess beyond a cap or envelope,
-- and the top-ups needed. Nothing here enforces anything.

-- The day cost of every person with resolved rows: the position of a subject on day d is the running sum of it.
CREATE OR REPLACE TABLE person_daycost AS
SELECT month, person_key, day, coalesce(sum(cost_ref), CAST(0 AS DECIMAL(38,12))) AS cost
FROM person_day
WHERE identity_status = 'resolved' AND person_key IS NOT NULL
GROUP BY month, person_key, day;

-- Positions are evaluated on every day of the window (8.1). A position changes only on days with a cost row, so
-- the data days plus each month's first day (a person's position there is 0 or that day's cost) are enough: a
-- threshold of 0 is then reached on the first day, as the spec states.
CREATE OR REPLACE TABLE person_daygrid AS
SELECT month, person_key, day, sum(cost) AS cost
FROM (
  SELECT month, person_key, day, cost FROM person_daycost
  UNION ALL
  SELECT pm.month, pm.person_key, m.first_day, CAST(0 AS DECIMAL(38,12))
  FROM person_month pm
  JOIN months m ON m.month = pm.month
) g
GROUP BY month, person_key, day;

-- ---- persons ------------------------------------------------------------------------------------------

-- A person's cap: the value of their (collapsed) attribute in values, else the default; none when both are absent.
CREATE OR REPLACE TABLE scn_person_cap AS
SELECT s.scenario_id, pm.month, pm.person_key, coalesce(v.cap, s.default_cap) AS cap, s.top_up
FROM scn_def s
CROSS JOIN person_month pm
LEFT JOIN person_attr pa ON pa.month = pm.month AND pa.person_key = pm.person_key AND pa.attribute = s.cap_by
LEFT JOIN scn_cap_values v ON v.scenario_id = s.scenario_id AND v.value = pa.value
WHERE s.has_caps AND coalesce(v.cap, s.default_cap) IS NOT NULL;

CREATE OR REPLACE TABLE scn_person_pos AS
SELECT c.scenario_id, c.month, c.person_key, c.cap, d.day,
       sum(d.cost) OVER (PARTITION BY c.scenario_id, c.month, c.person_key ORDER BY d.day) AS pos
FROM scn_person_cap c
JOIN person_daygrid d ON d.month = c.month AND d.person_key = c.person_key;

CREATE OR REPLACE TABLE scn_person_base AS
SELECT c.scenario_id, c.month, c.person_key, c.cap, pm.cost_ref, st.stopped_on, c.top_up,
       greatest(pm.cost_ref - c.cap, CAST(0 AS DECIMAL(38,12))) AS excess
FROM scn_person_cap c
JOIN person_month pm ON pm.month = c.month AND pm.person_key = c.person_key
LEFT JOIN (SELECT scenario_id, month, person_key, min(day) AS stopped_on
           FROM scn_person_pos WHERE pos > cap
           GROUP BY scenario_id, month, person_key) st
  ON st.scenario_id = c.scenario_id AND st.month = c.month AND st.person_key = c.person_key;

CREATE OR REPLACE TABLE scenario_persons AS
SELECT scenario_id, month, person_key, cap, cost_ref, stopped_on, excess,
       CASE WHEN top_up IS NULL THEN NULL WHEN excess = 0 THEN CAST(0 AS BIGINT)
            ELSE CAST(qceil(excess, top_up) AS BIGINT) END AS top_ups,
       top_up
FROM scn_person_base;

-- ---- units --------------------------------------------------------------------------------------------

-- Subjects: units whose key has an envelope and whose cell exists that month (suppressed or not, 8.1).
CREATE OR REPLACE TABLE scn_unit_subject AS
SELECT DISTINCT e.scenario_id, ci.month, u.unit_id, e.envelope
FROM scn_envelopes e
JOIN org_units u ON u.key = e.unit_key AND u.dimension = (SELECT line_dimension FROM policy_t)
JOIN cell_info ci ON ci.cell_id = 'unit:' || u.unit_id;

CREATE OR REPLACE TABLE scn_unit_day AS
SELECT u.scenario_id, u.month, u.unit_id, u.envelope, d.day, sum(d.cost) AS day_cost
FROM scn_unit_subject u
JOIN cell_members m ON m.month = u.month AND m.cell_id = 'unit:' || u.unit_id
JOIN person_daygrid d ON d.month = m.month AND d.person_key = m.person_key
GROUP BY u.scenario_id, u.month, u.unit_id, u.envelope, d.day;

CREATE OR REPLACE TABLE scn_unit_pos AS
SELECT scenario_id, month, unit_id, envelope, day,
       sum(day_cost) OVER (PARTITION BY scenario_id, month, unit_id ORDER BY day) AS pos
FROM scn_unit_day;

CREATE OR REPLACE TABLE scn_unit_base AS
SELECT u.scenario_id, u.month, u.unit_id, u.envelope,
       coalesce(t.position, CAST(0 AS DECIMAL(38,12))) AS position,
       st.stopped_on, d.top_up,
       greatest(coalesce(t.position, CAST(0 AS DECIMAL(38,12))) - u.envelope, CAST(0 AS DECIMAL(38,12))) AS excess
FROM scn_unit_subject u
JOIN scn_def d ON d.scenario_id = u.scenario_id
LEFT JOIN (SELECT scenario_id, month, unit_id, sum(day_cost) AS position
           FROM scn_unit_day GROUP BY scenario_id, month, unit_id) t
  ON t.scenario_id = u.scenario_id AND t.month = u.month AND t.unit_id = u.unit_id
LEFT JOIN (SELECT scenario_id, month, unit_id, min(day) AS stopped_on
           FROM scn_unit_pos WHERE pos > envelope
           GROUP BY scenario_id, month, unit_id) st
  ON st.scenario_id = u.scenario_id AND st.month = u.month AND st.unit_id = u.unit_id;

CREATE OR REPLACE TABLE scenario_units AS
SELECT scenario_id, month, unit_id, envelope, position, stopped_on, excess,
       CASE WHEN top_up IS NULL THEN NULL WHEN excess = 0 THEN CAST(0 AS BIGINT)
            ELSE CAST(qceil(excess, top_up) AS BIGINT) END AS top_ups,
       top_up
FROM scn_unit_base;

-- ---- summary ------------------------------------------------------------------------------------------

CREATE OR REPLACE TABLE scenario_summary AS
SELECT d.scenario_id, mo.month,
       coalesce(p.persons, 0) AS persons,
       coalesce(p.persons_stopped, 0) AS persons_stopped,
       coalesce(p.excess, CAST(0 AS DECIMAL(38,12))) AS persons_excess,
       CASE WHEN d.top_up IS NULL THEN NULL
            ELSE coalesce(p.top_up_total, CAST(0 AS DECIMAL(38,12))) END AS persons_top_up_total,
       coalesce(u.units, 0) AS units,
       coalesce(u.units_stopped, 0) AS units_stopped,
       coalesce(u.excess, CAST(0 AS DECIMAL(38,12))) AS units_excess,
       CASE WHEN d.top_up IS NULL THEN NULL
            ELSE coalesce(u.top_up_total, CAST(0 AS DECIMAL(38,12))) END AS units_top_up_total
FROM scn_def d
CROSS JOIN (SELECT DISTINCT month FROM person_month) mo
LEFT JOIN (SELECT scenario_id, month, CAST(count(*) AS BIGINT) AS persons,
                  CAST(count(*) FILTER (WHERE stopped_on IS NOT NULL) AS BIGINT) AS persons_stopped,
                  sum(excess) AS excess,
                  sum(top_ups * top_up) AS top_up_total
           FROM (SELECT b.*, CAST(CASE WHEN b.top_up IS NULL THEN NULL WHEN b.excess = 0 THEN 0
                                       ELSE qceil(b.excess, b.top_up) END AS BIGINT) AS top_ups
                 FROM scn_person_base b) x
           GROUP BY scenario_id, month) p
  ON p.scenario_id = d.scenario_id AND p.month = mo.month
LEFT JOIN (SELECT scenario_id, month, CAST(count(*) AS BIGINT) AS units,
                  CAST(count(*) FILTER (WHERE stopped_on IS NOT NULL) AS BIGINT) AS units_stopped,
                  sum(excess) AS excess,
                  sum(top_ups * top_up) AS top_up_total
           FROM (SELECT b.*, CAST(CASE WHEN b.top_up IS NULL THEN NULL WHEN b.excess = 0 THEN 0
                                       ELSE qceil(b.excess, b.top_up) END AS BIGINT) AS top_ups
                 FROM scn_unit_base b) x
           GROUP BY scenario_id, month) u
  ON u.scenario_id = d.scenario_id AND u.month = mo.month;

-- dataset: scenario_persons
SELECT scenario_id, month, person_key, cap, cost_ref, stopped_on, excess, top_ups, top_ups * top_up AS top_up_total
FROM scenario_persons
ORDER BY scenario_id, month, person_key;

-- dataset: scenario_units
SELECT scenario_id, month, unit_id, envelope, position, stopped_on, excess, top_ups, top_ups * top_up AS top_up_total
FROM scenario_units
ORDER BY scenario_id, month, unit_id;

-- dataset: scenario_summary
SELECT scenario_id, month, persons, persons_stopped, persons_excess, persons_top_up_total, units, units_stopped,
       units_excess, units_top_up_total
FROM scenario_summary
ORDER BY scenario_id, month;
