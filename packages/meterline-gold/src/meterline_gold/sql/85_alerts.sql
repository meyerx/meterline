-- meterline-gold sql 1.1
-- Alert events (spec 8.2) and named exceptions (spec 7.7). Alert-only: nothing here stops or notifies anyone.

-- The days each person is employed, over the build period (the days of every month's window).
CREATE OR REPLACE TABLE emp_days AS
SELECT DISTINCT p.person_key, w.day
FROM window_days w
JOIN org_people p ON p.valid_from <= w.day AND coalesce(p.valid_to, DATE '9999-12-31') >= w.day;

-- breaker: a person-day whose day cost exceeds the daily breaker, employed that day or not
CREATE OR REPLACE TABLE breaker_events AS
SELECT c.month, c.person_key, c.day, c.cost AS value, a.breaker AS threshold
FROM person_daycost c
CROSS JOIN policy_alerts a
WHERE a.breaker IS NOT NULL AND c.cost > a.breaker;

-- spike: the trailing window holds the person's employed days of the previous trailing_days days, inside the build
-- period; it fires with enough history, a day cost above the floor and above multiplier times the window mean
CREATE OR REPLACE TABLE spike_events AS
WITH cand AS (
  SELECT c.month, c.person_key, c.day, c.cost, a.spike_multiplier, a.spike_floor, a.spike_trailing,
         a.spike_min_history
  FROM person_daycost c
  CROSS JOIN policy_alerts a
  WHERE a.spike_multiplier IS NOT NULL AND c.cost > a.spike_floor
), win AS (
  SELECT c.month, c.person_key, c.day, c.cost, c.spike_multiplier, c.spike_floor, c.spike_min_history,
         count(*) AS n, sum(coalesce(d.cost, CAST(0 AS DECIMAL(38,12)))) AS trailing_sum
  FROM cand c
  JOIN emp_days e ON e.person_key = c.person_key AND e.day < c.day AND e.day >= c.day - CAST(c.spike_trailing AS INTEGER)
  LEFT JOIN person_daycost d ON d.person_key = e.person_key AND d.day = e.day
  GROUP BY c.month, c.person_key, c.day, c.cost, c.spike_multiplier, c.spike_floor, c.spike_min_history
)
SELECT month, person_key, day, cost AS value, spike_floor AS threshold, trailing_sum, CAST(n AS BIGINT) AS trailing_days
FROM win
WHERE n >= spike_min_history
  AND scaled(cost) * n * 1000000000000 > scaled(spike_multiplier) * scaled(trailing_sum);

-- scenario alerts: the first day a position reaches a fraction of its cap or envelope
CREATE OR REPLACE TABLE scenario_alerts AS
SELECT x.scenario_id, 'person' AS subject_kind, x.person_key AS subject_key, x.day,
       x.pos AS value, x.threshold
FROM (SELECT p.scenario_id, p.person_key, p.month,
             CAST(f.fraction * p.cap AS DECIMAL(38,12)) AS threshold,
             min(p.day) AS day, arg_min(p.pos, p.day) AS pos
      FROM scn_person_pos p
      JOIN scn_fractions f ON f.scenario_id = p.scenario_id
      WHERE p.pos >= CAST(f.fraction * p.cap AS DECIMAL(38,12))
      GROUP BY p.scenario_id, p.person_key, p.month, CAST(f.fraction * p.cap AS DECIMAL(38,12))) x
UNION ALL
SELECT y.scenario_id, 'unit', y.unit_id, y.day, y.pos, y.threshold
FROM (SELECT p.scenario_id, p.unit_id, p.month,
             CAST(f.fraction * p.envelope AS DECIMAL(38,12)) AS threshold,
             min(p.day) AS day, arg_min(p.pos, p.day) AS pos
      FROM scn_unit_pos p
      JOIN scn_fractions f ON f.scenario_id = p.scenario_id
      WHERE p.pos >= CAST(f.fraction * p.envelope AS DECIMAL(38,12))
      GROUP BY p.scenario_id, p.unit_id, p.month, CAST(f.fraction * p.envelope AS DECIMAL(38,12))) y;

-- proxy overlap (v0.3 hardening A-2): one event per proxy, provider it routes to and month, with the amount counted twice;
-- the day is the month's evaluation day, the subject is "<proxy provider>/<provider routed to>", the threshold is 0
CREATE OR REPLACE TABLE proxy_overlap_events AS
SELECT m.eval_day AS day, o.provider || '/' || o.model_provider AS subject_key, sum(o.overlap) AS value
FROM usage_overlap o
JOIN months m ON m.month = o.month
GROUP BY m.eval_day, o.provider, o.model_provider
HAVING sum(o.overlap) > 0;

CREATE OR REPLACE TABLE alert_events AS
SELECT day, 'breaker' AS event_type, '' AS scenario_id, 'person' AS subject_kind, person_key AS subject_key,
       value, threshold, CAST(NULL AS DECIMAL(38,12)) AS trailing_sum, CAST(NULL AS BIGINT) AS trailing_days
FROM breaker_events
UNION ALL
SELECT day, 'spike', '', 'person', person_key, value, threshold, trailing_sum, trailing_days
FROM spike_events
UNION ALL
SELECT DISTINCT day, 'scenario_alert', scenario_id, subject_kind, subject_key, value, threshold,
       CAST(NULL AS DECIMAL(38,12)), CAST(NULL AS BIGINT)
FROM scenario_alerts
UNION ALL
SELECT day, 'proxy_overlap', '', 'proxy', subject_key, value, CAST(0 AS DECIMAL(38,12)),
       CAST(NULL AS DECIMAL(38,12)), CAST(NULL AS BIGINT)
FROM proxy_overlap_events;

-- named exceptions (7.7): the persons a rule selects, per month
CREATE OR REPLACE TABLE named_exceptions AS
WITH breaker_n AS (
  SELECT month, person_key, CAST(count(*) AS BIGINT) AS n FROM breaker_events GROUP BY month, person_key
), spike_n AS (
  SELECT month, person_key, CAST(count(*) AS BIGINT) AS n FROM spike_events GROUP BY month, person_key
)
SELECT pm.month, r.rule_id, r.audience, pm.person_key, pm.manager_key, r.condition, r.threshold_text,
       CASE WHEN r.condition = 'month_cost_ref_above' THEN 'money' ELSE 'count' END AS value_type,
       CASE r.condition WHEN 'breaker_events_at_least' THEN coalesce(b.n, 0) WHEN 'spike_events_at_least' THEN coalesce(s.n, 0) END AS v_count,
       CASE r.condition WHEN 'month_cost_ref_above' THEN pm.cost_ref END AS v_decimal
FROM policy_exceptions r
CROSS JOIN person_month pm
LEFT JOIN breaker_n b ON b.month = pm.month AND b.person_key = pm.person_key
LEFT JOIN spike_n s ON s.month = pm.month AND s.person_key = pm.person_key
WHERE (r.condition = 'month_cost_ref_above' AND pm.cost_ref > r.threshold_money)
   OR (r.condition = 'breaker_events_at_least' AND coalesce(b.n, 0) >= r.threshold_count)
   OR (r.condition = 'spike_events_at_least' AND coalesce(s.n, 0) >= r.threshold_count);

-- dataset: alert_events
SELECT day, event_type, scenario_id, subject_kind, subject_key, value, threshold, trailing_sum, trailing_days
FROM alert_events
ORDER BY day, event_type, scenario_id, subject_kind, subject_key, threshold;

-- dataset: named_exceptions
SELECT month, rule_id, audience, person_key, manager_key, condition, threshold_text, value_type, v_count, v_decimal
FROM named_exceptions
ORDER BY month, rule_id, person_key;
