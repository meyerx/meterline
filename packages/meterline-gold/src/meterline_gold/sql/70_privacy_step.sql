-- meterline-gold sql 1.1
-- The privacy loop (spec 7.4) and the active_persons pass (7.2). Nothing here runs as setup: ``run.run_privacy``
-- executes the statements marked ``-- step: <name>`` in file order. It reads the table ``cells`` and the one-row
-- table ``privacy_params(k, block_rule)``; with a table ``person_flags(month, person_key, active)`` it also
-- decides which cells publish ``active_persons``.

-- step: privacy_init
CREATE OR REPLACE TABLE cell_members AS
SELECT DISTINCT month, cell_id, unnest(persons_list) AS person_key FROM cells;

-- step: privacy_init
CREATE OR REPLACE TABLE cell_recv AS
SELECT DISTINCT month, cell_id, unnest(receivers_list) AS person_key FROM cells;

-- step: privacy_init
CREATE OR REPLACE TABLE cell_tree AS
SELECT month, cell_id, coalesce(parent_cell_id, '') AS parent_cell_id FROM cells;

-- step: privacy_init
CREATE OR REPLACE TABLE cell_size AS
SELECT month, cell_id, CAST(count(*) AS BIGINT) AS persons
FROM cell_members
GROUP BY month, cell_id;

-- step: privacy_init
CREATE OR REPLACE TABLE cell_closure AS
WITH RECURSIVE c(month, ancestor, descendant, lvl) AS (
  SELECT month, parent_cell_id, cell_id, 0 FROM cell_tree WHERE parent_cell_id <> ''
  UNION ALL
  SELECT c.month, t.parent_cell_id, c.descendant, c.lvl + 1
  FROM c JOIN cell_tree t ON t.month = c.month AND t.cell_id = c.ancestor
  WHERE t.parent_cell_id <> '' AND c.lvl < 64
)
SELECT DISTINCT month, ancestor, descendant FROM c;

-- direct members of a cell: its persons that sit in none of its children (7.1)
-- step: privacy_init
CREATE OR REPLACE TABLE cell_direct AS
SELECT m.month, m.cell_id, m.person_key
FROM cell_members m
LEFT JOIN (SELECT DISTINCT t.month, t.parent_cell_id AS cell_id, cm.person_key
           FROM cell_tree t
           JOIN cell_members cm ON cm.month = t.month AND cm.cell_id = t.cell_id
           WHERE t.parent_cell_id <> '') ch
  ON ch.month = m.month AND ch.cell_id = m.cell_id AND ch.person_key = m.person_key
WHERE ch.person_key IS NULL;

-- step 1, primary: fewer than k persons, or fewer than k once the receivers are left out
-- step: privacy_init
CREATE OR REPLACE TABLE cell_state AS
SELECT z.month, z.cell_id,
       (z.persons < p.k OR z.persons - coalesce(r.n, 0) < p.k) AS suppressed,
       CASE WHEN z.persons < p.k OR z.persons - coalesce(r.n, 0) < p.k THEN 'k' END AS reason
FROM cell_size z
CROSS JOIN privacy_params p
LEFT JOIN (SELECT m.month, m.cell_id, count(*) AS n
           FROM cell_members m
           JOIN cell_recv rc ON rc.month = m.month AND rc.cell_id = m.cell_id AND rc.person_key = m.person_key
           GROUP BY m.month, m.cell_id) r
  ON r.month = z.month AND r.cell_id = z.cell_id;

-- ---- one iteration ------------------------------------------------------------------------------------

-- a. differencing: two unsuppressed cells of one month that differ by 1 to k - 1 persons
-- step: privacy
CREATE OR REPLACE TABLE priv_a AS
WITH live AS (
  SELECT m.month, m.cell_id, m.person_key
  FROM cell_members m
  JOIN cell_state s ON s.month = m.month AND s.cell_id = m.cell_id
  WHERE NOT s.suppressed
), inter AS (
  SELECT a.month, a.cell_id AS a_id, b.cell_id AS b_id, count(*) AS n
  FROM live a
  JOIN live b ON b.month = a.month AND b.person_key = a.person_key AND a.cell_id < b.cell_id
  GROUP BY a.month, a.cell_id, b.cell_id
), hit AS (
  SELECT i.month, i.a_id, i.b_id
  FROM inter i
  JOIN cell_size za ON za.month = i.month AND za.cell_id = i.a_id
  JOIN cell_size zb ON zb.month = i.month AND zb.cell_id = i.b_id
  CROSS JOIN privacy_params p
  WHERE za.persons + zb.persons - 2 * i.n >= 1 AND za.persons + zb.persons - 2 * i.n < p.k
)
SELECT month, a_id AS cell_id FROM hit
UNION
SELECT month, b_id FROM hit;

-- step: privacy
UPDATE cell_state SET suppressed = true, reason = 'differencing'
WHERE NOT suppressed
  AND EXISTS (SELECT 1 FROM priv_a a WHERE a.month = cell_state.month AND a.cell_id = cell_state.cell_id);

-- b1. exactly one child suppressed and one unsuppressed at least: the smallest unsuppressed child goes
-- step: privacy
CREATE OR REPLACE TABLE priv_kids AS
SELECT t.month, t.parent_cell_id AS p, t.cell_id AS c, s.suppressed, z.persons,
       row_number() OVER (PARTITION BY t.month, t.parent_cell_id, s.suppressed
                          ORDER BY z.persons, t.cell_id) AS rn
FROM cell_tree t
JOIN cell_state s ON s.month = t.month AND s.cell_id = t.cell_id
JOIN cell_size z ON z.month = t.month AND z.cell_id = t.cell_id
WHERE t.parent_cell_id <> '';

-- step: privacy
CREATE OR REPLACE TABLE priv_b1 AS
SELECT u.month, u.c AS cell_id
FROM priv_kids u
JOIN (SELECT month, p,
             count(*) FILTER (WHERE suppressed) AS n_sup,
             count(*) FILTER (WHERE NOT suppressed) AS n_pub
      FROM priv_kids GROUP BY month, p) a
  ON a.month = u.month AND a.p = u.p
WHERE NOT u.suppressed AND u.rn = 1 AND a.n_sup = 1 AND a.n_pub >= 1;

-- b2. the block rule: pieces of an unsuppressed cell (7.4); the last failure case also reads the unknown block
-- (change list finding 21)
-- step: privacy
CREATE OR REPLACE TABLE priv_uncov AS
SELECT m.month, m.cell_id, m.person_key
FROM cell_members m
JOIN cell_state s ON s.month = m.month AND s.cell_id = m.cell_id AND s.suppressed
LEFT JOIN (SELECT DISTINCT cl.month, cl.ancestor AS cell_id, dm.person_key
           FROM cell_closure cl
           JOIN cell_state ds ON ds.month = cl.month AND ds.cell_id = cl.descendant AND NOT ds.suppressed
           JOIN cell_members dm ON dm.month = cl.month AND dm.cell_id = cl.descendant) cov
  ON cov.month = m.month AND cov.cell_id = m.cell_id AND cov.person_key = m.person_key
WHERE cov.person_key IS NULL;

-- step: privacy
CREATE OR REPLACE TABLE priv_pieces AS
SELECT d.month, d.cell_id AS p, 'direct' AS piece, d.person_key
FROM cell_direct d
JOIN cell_state s ON s.month = d.month AND s.cell_id = d.cell_id AND NOT s.suppressed
UNION ALL
SELECT t.month, t.parent_cell_id AS p, 'child:' || t.cell_id AS piece, u.person_key
FROM priv_uncov u
JOIN cell_tree t ON t.month = u.month AND t.cell_id = u.cell_id
JOIN cell_state sp ON sp.month = t.month AND sp.cell_id = t.parent_cell_id AND NOT sp.suppressed
WHERE t.parent_cell_id <> '';

-- step: privacy
CREATE OR REPLACE TABLE priv_b2 AS
WITH block AS (
  SELECT pc.month, pc.p AS cell_id,
         count(DISTINCT pc.person_key) AS block_n,
         count(DISTINCT pc.person_key) FILTER (WHERE r.person_key IS NULL) AS unknown_n,
         count(DISTINCT pc.piece) AS pieces,
         bool_and(pc.piece <> 'direct') AS only_children,
         count(DISTINCT pc.piece) FILTER (WHERE r.person_key IS NULL) AS unknown_pieces,
         bool_and(pc.piece <> 'direct') FILTER (WHERE r.person_key IS NULL) AS unknown_only_children
  FROM priv_pieces pc
  LEFT JOIN cell_recv r ON r.month = pc.month AND r.cell_id = pc.p AND r.person_key = pc.person_key
  GROUP BY pc.month, pc.p
), fail AS (
  SELECT b.month, b.cell_id
  FROM block b
  CROSS JOIN privacy_params p
  WHERE p.block_rule
    AND ((b.block_n >= 1 AND b.block_n < p.k)
      OR (b.unknown_n >= 1 AND b.unknown_n < p.k)
      OR (b.block_n >= 1 AND b.pieces = 1 AND b.only_children)
      OR (b.unknown_n >= 1 AND b.unknown_pieces = 1 AND b.unknown_only_children))
)
SELECT f.month, coalesce(k.c, f.cell_id) AS cell_id
FROM fail f
LEFT JOIN priv_kids k ON k.month = f.month AND k.p = f.cell_id AND NOT k.suppressed AND k.rn = 1;

-- step: privacy
UPDATE cell_state SET suppressed = true, reason = 'secondary'
WHERE NOT suppressed
  AND (EXISTS (SELECT 1 FROM priv_b1 h WHERE h.month = cell_state.month AND h.cell_id = cell_state.cell_id)
    OR EXISTS (SELECT 1 FROM priv_b2 h WHERE h.month = cell_state.month AND h.cell_id = cell_state.cell_id));

-- ---- active_persons (7.2): decided after 7.4, a pass until nothing changes ------------------------------

-- step: active_init
CREATE OR REPLACE TABLE active_cand AS
SELECT s.month, s.cell_id
FROM cell_state s
JOIN (SELECT m.month, m.cell_id,
             count(*) FILTER (WHERE f.active) AS n_active,
             count(*) FILTER (WHERE NOT f.active) AS n_inactive
      FROM cell_members m
      JOIN person_flags f ON f.month = m.month AND f.person_key = m.person_key
      GROUP BY m.month, m.cell_id) c
  ON c.month = s.month AND c.cell_id = s.cell_id
CROSS JOIN privacy_params p
WHERE NOT s.suppressed AND c.n_active >= p.k AND c.n_inactive >= p.k;

-- step: active_init
CREATE OR REPLACE TABLE cell_active_published AS
SELECT month, cell_id FROM active_cand;

-- step: active
CREATE OR REPLACE TABLE active_next AS
WITH covered AS (
  SELECT DISTINCT cl.month, cl.ancestor AS cell_id, m.person_key
  FROM cell_closure cl
  JOIN cell_active_published ap ON ap.month = cl.month AND ap.cell_id = cl.descendant
  JOIN cell_members m ON m.month = cl.month AND m.cell_id = cl.descendant
), rem AS (
  SELECT m.month, m.cell_id, f.active, (r.person_key IS NOT NULL) AS is_recv
  FROM cell_members m
  JOIN person_flags f ON f.month = m.month AND f.person_key = m.person_key
  LEFT JOIN covered c ON c.month = m.month AND c.cell_id = m.cell_id AND c.person_key = m.person_key
  LEFT JOIN cell_recv r ON r.month = m.month AND r.cell_id = m.cell_id AND r.person_key = m.person_key
  WHERE c.person_key IS NULL
), agg AS (
  SELECT month, cell_id,
         count(*) FILTER (WHERE active) AS a_with,
         count(*) FILTER (WHERE NOT active) AS i_with,
         count(*) FILTER (WHERE active AND NOT is_recv) AS a_without,
         count(*) FILTER (WHERE NOT active AND NOT is_recv) AS i_without
  FROM rem GROUP BY month, cell_id
)
SELECT c.month, c.cell_id
FROM active_cand c
CROSS JOIN privacy_params p
LEFT JOIN agg a ON a.month = c.month AND a.cell_id = c.cell_id
WHERE a.cell_id IS NULL
   OR (a.a_with >= p.k AND a.i_with >= p.k AND a.a_without >= p.k AND a.i_without >= p.k);
