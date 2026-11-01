-- meterline-gold sql 1.1
-- Exact quotients for every published figure. Every division of the engine sits in this file.
-- n and d are non-negative. Each macro is NULL when n or d is NULL or d = 0.
-- Reference implementation (spec 6.0): scale n and d by 10^12, cast to HUGEINT, compute
-- (2*N*10^s + D) // (2*D) with integer division, then scale by 10^-s.

CREATE OR REPLACE MACRO pow10h(s) AS
  CASE s
    WHEN 0 THEN CAST(1 AS HUGEINT)
    WHEN 1 THEN CAST(10 AS HUGEINT)
    WHEN 2 THEN CAST(100 AS HUGEINT)
    WHEN 3 THEN CAST(1000 AS HUGEINT)
    WHEN 4 THEN CAST(10000 AS HUGEINT)
    WHEN 5 THEN CAST(100000 AS HUGEINT)
    WHEN 6 THEN CAST(1000000 AS HUGEINT)
    WHEN 7 THEN CAST(10000000 AS HUGEINT)
    WHEN 8 THEN CAST(100000000 AS HUGEINT)
    WHEN 9 THEN CAST(1000000000 AS HUGEINT)
    WHEN 10 THEN CAST(10000000000 AS HUGEINT)
    WHEN 11 THEN CAST(100000000000 AS HUGEINT)
    WHEN 12 THEN CAST(1000000000000 AS HUGEINT)
  END;

CREATE OR REPLACE MACRO unit_scale(s) AS
  CASE s
    WHEN 0 THEN CAST(1 AS DECIMAL(38,12))
    WHEN 1 THEN CAST(0.1 AS DECIMAL(38,12))
    WHEN 2 THEN CAST(0.01 AS DECIMAL(38,12))
    WHEN 3 THEN CAST(0.001 AS DECIMAL(38,12))
    WHEN 4 THEN CAST(0.0001 AS DECIMAL(38,12))
    WHEN 5 THEN CAST(0.00001 AS DECIMAL(38,12))
    WHEN 6 THEN CAST(0.000001 AS DECIMAL(38,12))
    WHEN 7 THEN CAST(0.0000001 AS DECIMAL(38,12))
    WHEN 8 THEN CAST(0.00000001 AS DECIMAL(38,12))
    WHEN 9 THEN CAST(0.000000001 AS DECIMAL(38,12))
    WHEN 10 THEN CAST(0.0000000001 AS DECIMAL(38,12))
    WHEN 11 THEN CAST(0.00000000001 AS DECIMAL(38,12))
    WHEN 12 THEN CAST(0.000000000001 AS DECIMAL(38,12))
  END;

CREATE OR REPLACE MACRO scaled(x) AS CAST(CAST(x AS DECIMAL(38,12)) * 1000000000000 AS HUGEINT);

-- n/d rounded half up to s decimals, as DECIMAL(38,12)
CREATE OR REPLACE MACRO qdiv(n, d, s) AS
  CASE WHEN n IS NULL OR d IS NULL OR d = 0 THEN NULL
  ELSE CAST(CAST((2 * scaled(n) * pow10h(s) + scaled(d)) // (2 * scaled(d)) AS DECIMAL(38,12)) * unit_scale(s) AS DECIMAL(38,12))
  END;

-- n/d rounded half up to 4 decimals
CREATE OR REPLACE MACRO ratio4(n, d) AS qdiv(n, d, 4);

-- the smallest integer >= n/d
CREATE OR REPLACE MACRO qceil(n, d) AS
  CASE WHEN n IS NULL OR d IS NULL OR d = 0 THEN NULL
  ELSE (scaled(n) + scaled(d) - 1) // scaled(d)
  END;

-- the label YYYY-MM of a date
CREATE OR REPLACE MACRO month_label(d) AS
  CAST(year(d) AS VARCHAR) || '-' || lpad(CAST(month(d) AS VARCHAR), 2, '0');

-- a value as written into a cell id (spec 7.1, 7.5): % first, then | and =
CREATE OR REPLACE MACRO esc_cell(v) AS
  replace(replace(replace(v, '%', '%25'), '|', '%7C'), '=', '%3D');
