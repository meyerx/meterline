-- meterline-gold sql 1.1
-- The fixed grid (spec 7.1): attribute values, one cell tree per month, receivers, and the ``cells`` table the
-- privacy loop reads (spec 14: cells(month, cell_id, parent_cell_id, persons_list, receivers_list)).

-- Active flag of a person-month (7.2 active_persons): any active day.
CREATE OR REPLACE TABLE person_flags AS
SELECT month, person_key, active_days > 0 AS active
FROM person_month;

-- Attribute values of every person-month after collapsing (7.1); a null value is "(none)".
CREATE OR REPLACE TABLE person_attr AS
WITH raw AS (
  SELECT pm.month, pm.person_key, n.attribute,
         NULLIF(CASE n.attribute
                  WHEN 'function' THEN pm.function
                  WHEN 'level' THEN pm.level
                  WHEN 'cost_center' THEN pm.cost_center
                  WHEN 'population' THEN pm.population
                  ELSE json_extract_string(op.attrs, '$."' || n.attrs_key || '"')
                END, '') AS value
  FROM person_month pm
  CROSS JOIN policy_attr_names n
  JOIN org_people op ON op.person_key = pm.person_key AND op.valid_from <= pm.attribution_day
                    AND (op.valid_to IS NULL OR op.valid_to >= pm.attribution_day)
)
SELECT r.month, r.person_key, r.attribute, coalesce(c.mapped, r.value, '(none)') AS value
FROM raw r
LEFT JOIN policy_collapse c ON c.attribute = r.attribute AND c.value = r.value;

-- ---- kind: unit ---------------------------------------------------------------------------------------

-- The month's unit tree (7.1, v1.1): one parent per unit and month, the parent_id of the unit's line row valid on
-- the last day of the month's window on which the unit has one. A parent with no line row valid on that day
-- ends the path (NULL).
CREATE OR REPLACE TABLE unit_month_tree AS
WITH unit_rows AS (
  SELECT m.month, u.unit_id, u.parent_id,
         least(coalesce(u.valid_to, DATE '9999-12-31'), m.eval_day) AS last_valid_day
  FROM months m
  JOIN org_units u ON u.dimension = (SELECT line_dimension FROM policy_t)
                  AND u.valid_from <= m.eval_day AND coalesce(u.valid_to, DATE '9999-12-31') >= m.first_day
), latest AS (
  SELECT r.*, row_number() OVER (PARTITION BY r.month, r.unit_id ORDER BY r.last_valid_day DESC) AS rn
  FROM unit_rows r
)
SELECT l.month, l.unit_id,
       CASE WHEN l.parent_id IS NULL OR l.parent_id = '' THEN NULL
            WHEN EXISTS (SELECT 1 FROM org_units p
                         WHERE p.unit_id = l.parent_id AND p.dimension = (SELECT line_dimension FROM policy_t)
                           AND p.valid_from <= l.last_valid_day
                           AND coalesce(p.valid_to, DATE '9999-12-31') >= l.last_valid_day)
            THEN l.parent_id END AS parent_id,
       NULLIF(l.parent_id, '') AS parent_listed
FROM latest l
WHERE l.rn = 1;

-- F-37: units whose units.csv row names a parent that is no line unit valid on that day. The unit then ends its
-- path (a root); data_quality and the manifest count it.
CREATE OR REPLACE TABLE unit_parent_unknown AS
SELECT month, unit_id, parent_listed
FROM unit_month_tree
WHERE parent_listed IS NOT NULL AND parent_id IS NULL;

-- ... as a data_quality row per month, written only where it is not zero (the table is built in 50_reconciliation.sql).
INSERT INTO data_quality
SELECT month, '', '', 'units_parent_unknown', 'count', CAST(count(*) AS BIGINT), CAST(NULL AS DECIMAL(38,12)),
       CAST(NULL AS DATE)
FROM unit_parent_unknown
GROUP BY month;

-- The unit path of every person-month: the person's own unit (attribution day, 5.4) and its ancestors in the
-- month's unit tree.
CREATE OR REPLACE TABLE unit_paths AS
WITH RECURSIVE walk(month, person_key, unit_id, parent_id, lvl) AS (
  SELECT pm.month, pm.person_key, pm.unit_id, t.parent_id, 0
  FROM person_month pm
  JOIN unit_month_tree t ON t.month = pm.month AND t.unit_id = pm.unit_id
  WHERE (SELECT tree_kind FROM policy_t) = 'unit'
  UNION ALL
  SELECT w.month, w.person_key, t.unit_id, t.parent_id, w.lvl + 1
  FROM walk w
  JOIN unit_month_tree t ON t.month = w.month AND t.unit_id = w.parent_id
  WHERE w.lvl < 64
)
SELECT DISTINCT month, person_key, unit_id, parent_id FROM walk;

-- Units on someone's path whose parent chain comes back to the unit itself (7.1: exit 2, units.csv).
CREATE OR REPLACE TABLE unit_cycles AS
WITH RECURSIVE r(month, start_id, cur, lvl) AS (
  SELECT t.month, t.unit_id, t.parent_id, 1
  FROM unit_month_tree t
  WHERE t.parent_id IS NOT NULL AND EXISTS (SELECT 1 FROM unit_paths up WHERE up.month = t.month AND up.unit_id = t.unit_id)
  UNION ALL
  SELECT r.month, r.start_id, t.parent_id, r.lvl + 1
  FROM r
  JOIN unit_month_tree t ON t.month = r.month AND t.unit_id = r.cur
  WHERE t.parent_id IS NOT NULL AND r.lvl < 64 AND r.cur <> r.start_id
)
SELECT DISTINCT month, start_id AS unit_id FROM r WHERE cur = start_id;

-- The parent unit of every unit cell of a month.
CREATE OR REPLACE TABLE unit_cell_parent AS
SELECT month, unit_id, min(parent_id) AS parent_id
FROM unit_paths
GROUP BY month, unit_id;

CREATE OR REPLACE TABLE unit_cell_depth AS
WITH RECURSIVE d(month, unit_id, depth) AS (
  SELECT u.month, u.unit_id, 1
  FROM unit_cell_parent u
  WHERE u.parent_id IS NULL OR u.parent_id = ''
     OR NOT EXISTS (SELECT 1 FROM unit_cell_parent x WHERE x.month = u.month AND x.unit_id = u.parent_id)
  UNION ALL
  SELECT u.month, u.unit_id, d.depth + 1
  FROM unit_cell_parent u
  JOIN d ON d.month = u.month AND d.unit_id = u.parent_id
  WHERE d.depth < 64
)
SELECT month, unit_id, min(depth) AS depth FROM d GROUP BY month, unit_id;

-- The heads of the units on each month's evaluation day E (7.1 receivers).
CREATE OR REPLACE TABLE unit_head AS
SELECT DISTINCT m.month, u.unit_id, u.head_key
FROM months m
JOIN org_units u ON u.dimension = (SELECT line_dimension FROM policy_t)
                AND u.valid_from <= m.eval_day AND (u.valid_to IS NULL OR u.valid_to >= m.eval_day)
WHERE u.head_key IS NOT NULL;

-- ---- kind: attributes ---------------------------------------------------------------------------------

CREATE OR REPLACE TABLE attr_levels AS
SELECT pa.month, pa.person_key, lv.j AS level,
       string_agg(pa.attribute || '=' || esc_cell(pa.value), '|' ORDER BY t.ord) AS cell_key
FROM person_attr pa
JOIN policy_tree t ON t.attribute = pa.attribute
JOIN (SELECT DISTINCT ord + 1 AS j FROM policy_tree) lv ON t.ord < lv.j
WHERE (SELECT tree_kind FROM policy_t) = 'attributes'
GROUP BY pa.month, pa.person_key, lv.j;

-- ---- the cells ----------------------------------------------------------------------------------------

CREATE OR REPLACE TABLE cell_info AS
SELECT m.month, 'org:' AS cell_id, 'org' AS cell_kind, '' AS cell_key, 0 AS depth, '' AS parent_cell_id
FROM months m
WHERE EXISTS (SELECT 1 FROM person_month p WHERE p.month = m.month)
UNION ALL
SELECT p.month, 'unit:' || p.unit_id, 'unit', p.unit_id, d.depth,
       CASE WHEN p.parent_id IS NULL OR p.parent_id = ''
                 OR NOT EXISTS (SELECT 1 FROM unit_cell_parent x WHERE x.month = p.month AND x.unit_id = p.parent_id)
            THEN 'org:' ELSE 'unit:' || p.parent_id END
FROM unit_cell_parent p
JOIN unit_cell_depth d ON d.month = p.month AND d.unit_id = p.unit_id
UNION ALL
SELECT c.month, 'attr:' || c.cell_key, 'attr', c.cell_key, c.level,
       CASE WHEN c.level = 1 THEN 'org:' ELSE 'attr:' || p.cell_key END
FROM (SELECT DISTINCT month, person_key, level, cell_key FROM attr_levels) c
LEFT JOIN attr_levels p ON p.month = c.month AND p.person_key = c.person_key AND p.level = c.level - 1
GROUP BY c.month, c.cell_key, c.level, p.cell_key;

CREATE OR REPLACE TABLE cell_member_rows AS
SELECT month, 'org:' AS cell_id, person_key FROM person_month
UNION ALL
SELECT month, 'unit:' || unit_id, person_key FROM unit_paths
UNION ALL
SELECT month, 'attr:' || cell_key, person_key FROM attr_levels;

-- Receivers of a unit cell: the heads of its unit and of every ancestor unit (7.1).
CREATE OR REPLACE TABLE cell_receiver_rows AS
WITH RECURSIVE anc(month, cell_unit, unit_id, lvl) AS (
  SELECT month, unit_id, unit_id, 0 FROM unit_cell_parent
  UNION ALL
  SELECT a.month, a.cell_unit, p.unit_id, a.lvl + 1
  FROM anc a
  JOIN unit_cell_parent q ON q.month = a.month AND q.unit_id = a.unit_id
  JOIN unit_cell_parent p ON p.month = a.month AND p.unit_id = q.parent_id
  WHERE a.lvl < 64
)
SELECT DISTINCT a.month, 'unit:' || a.cell_unit AS cell_id, h.head_key AS person_key
FROM anc a
JOIN unit_head h ON h.month = a.month AND h.unit_id = a.unit_id;

CREATE OR REPLACE TABLE cells AS
SELECT i.month, i.cell_id, i.parent_cell_id,
       m.persons_list,
       coalesce(r.receivers_list, CAST([] AS VARCHAR[])) AS receivers_list
FROM cell_info i
JOIN (SELECT month, cell_id, list(person_key ORDER BY person_key) AS persons_list
      FROM cell_member_rows GROUP BY month, cell_id) m
  ON m.month = i.month AND m.cell_id = i.cell_id
LEFT JOIN (SELECT month, cell_id, list(person_key ORDER BY person_key) AS receivers_list
           FROM cell_receiver_rows GROUP BY month, cell_id) r
  ON r.month = i.month AND r.cell_id = i.cell_id;

-- ---- comparison groups (7.5) --------------------------------------------------------------------------

CREATE OR REPLACE TABLE compare_cells AS
SELECT pa.month, pa.person_key,
       string_agg(pa.attribute || '=' || esc_cell(pa.value), '|' ORDER BY t.ord) AS compare_cell
FROM person_attr pa
JOIN policy_compare_by t ON t.attribute = pa.attribute
GROUP BY pa.month, pa.person_key;
