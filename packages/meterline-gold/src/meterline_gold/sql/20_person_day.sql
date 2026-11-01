-- meterline-gold sql 1.1
-- person_day and person_day_activity (spec 5.2, 6.2, 6.3).

-- Resolution of every current day-grain usage record (spec 5.2). End users are left out.
CREATE OR REPLACE TABLE usage_resolved AS
SELECT row_number() OVER (ORDER BY u.provider, u.provider_account_id, u.scope, u.period_start, u.actor_kind, u.actor_key,
                                   u.product, u.model) AS rid,
       u.provider, u.provider_account_id, u.period_start, u.actor_kind, u.product, u.model,
       u.tokens_input_uncached, u.tokens_cache_read, u.tokens_cache_write, u.tokens_cache_write_5m,
       u.tokens_cache_write_1h, u.tokens_input_total, u.tokens_output, u.requests, u.ai_credit, u.cost,
       u.currency, u.cost_basis, u.is_final,
       CASE WHEN u.actor_kind IN ('unassigned', 'service_account', 'api_key') THEN 'non_person'
            WHEN i.method = 'non_person' THEN 'non_person'
            WHEN i.person_key IS NOT NULL THEN 'resolved'
            ELSE 'unresolved' END AS identity_status,
       CASE WHEN u.actor_kind IN ('unassigned', 'service_account', 'api_key') THEN NULL
            ELSE i.method END AS identity_method,
       CASE WHEN u.actor_kind IN ('unassigned', 'service_account', 'api_key') THEN NULL
            WHEN i.method = 'non_person' THEN NULL
            ELSE i.person_key END AS person_key,
       CASE WHEN u.actor_kind IN ('unassigned', 'service_account', 'api_key') THEN u.actor_key
            WHEN i.method = 'non_person' THEN u.actor_key
            WHEN i.person_key IS NOT NULL THEN NULL
            ELSE u.actor_key END AS actor_key
FROM silver_usage u
LEFT JOIN org_identities i
  ON i.provider = u.provider AND i.actor_kind = u.actor_kind AND i.actor_key = u.actor_key
 AND i.valid_from <= u.period_start AND (i.valid_to IS NULL OR i.valid_to >= u.period_start)
WHERE u.grain = 'day' AND u.actor_kind <> 'end_user';

-- The price in force per (provider, model, day): the record's own provider wins over '*', then the latest
-- valid_from on or before the day (spec 3.3).
CREATE OR REPLACE TABLE price_day AS
SELECT provider, model, period_start, priced, p_input, p_cache_read, p_write_5m, p_write_1h, p_output
FROM (
  SELECT c.provider, c.model, c.period_start, p.priced, p.p_input, p.p_cache_read, p.p_write_5m, p.p_write_1h,
         p.p_output,
         row_number() OVER (PARTITION BY c.provider, c.model, c.period_start
                            ORDER BY (p.provider = c.provider) DESC, p.valid_from DESC, p.priced DESC,
                                     p.p_input DESC NULLS LAST) AS rn
  FROM (SELECT DISTINCT provider, model, period_start FROM usage_resolved WHERE model <> '') c
  JOIN price_tokens p
    ON p.model = c.model AND (p.provider = c.provider OR p.provider = '*') AND p.valid_from <= c.period_start
) t
WHERE rn = 1;

CREATE OR REPLACE TABLE unit_price_day AS
SELECT provider, period_start, price
FROM (
  SELECT c.provider, c.period_start, p.price,
         row_number() OVER (PARTITION BY c.provider, c.period_start ORDER BY p.valid_from DESC, p.price DESC) AS rn
  FROM (SELECT DISTINCT provider, period_start FROM usage_resolved WHERE ai_credit IS NOT NULL) c
  JOIN price_units p ON p.provider = c.provider AND p.unit = 'ai_credit' AND p.valid_from <= c.period_start
) t
WHERE rn = 1;

-- The reference cost of one silver record (spec 6.2.1): the first rule that applies.
CREATE OR REPLACE TABLE usage_priced AS
SELECT r.*,
       cat.tier AS model_tier,
       CASE
         WHEN inc.provider IS NOT NULL THEN CAST(0 AS DECIMAL(38,12))
         WHEN r.ai_credit IS NOT NULL THEN
           CASE WHEN up.price IS NOT NULL
                THEN CAST(CAST(r.ai_credit AS DECIMAL(38,12)) * up.price AS DECIMAL(38,12)) END
         WHEN r.model = '' THEN NULL
         WHEN cat.model IS NULL THEN NULL
         WHEN pd.priced IS TRUE THEN
           coalesce(r.tokens_input_uncached, 0) * pd.p_input
           + coalesce(r.tokens_cache_read, 0) * pd.p_cache_read
           + CASE WHEN r.tokens_cache_write_5m IS NOT NULL OR r.tokens_cache_write_1h IS NOT NULL
                  THEN coalesce(r.tokens_cache_write_5m, 0) * pd.p_write_5m
                       + coalesce(r.tokens_cache_write_1h, 0) * pd.p_write_1h
                  ELSE coalesce(r.tokens_cache_write, 0) * pd.p_write_5m END
           + coalesce(r.tokens_output, 0) * pd.p_output
           + CASE WHEN r.tokens_input_uncached IS NULL AND r.tokens_cache_read IS NULL
                       AND r.tokens_cache_write IS NULL AND r.tokens_input_total IS NOT NULL
                  THEN r.tokens_input_total * pd.p_input ELSE 0 END
         ELSE NULL
       END AS cost_ref,
       CASE
         WHEN inc.provider IS NOT NULL THEN 'included'
         WHEN r.ai_credit IS NOT NULL THEN CASE WHEN up.price IS NOT NULL THEN 'priced' ELSE 'unpriced' END
         WHEN r.model = '' THEN 'unpriced'
         WHEN cat.model IS NULL THEN 'unknown_model'
         WHEN pd.priced IS TRUE THEN 'priced'
         ELSE 'unpriced'
       END AS cost_ref_status
FROM usage_resolved r
LEFT JOIN price_included inc ON inc.provider = r.provider AND inc.product = r.product
LEFT JOIN price_models cat ON cat.model = r.model
LEFT JOIN price_day pd ON pd.provider = r.provider AND pd.model = r.model AND pd.period_start = r.period_start
LEFT JOIN unit_price_day up ON up.provider = r.provider AND up.period_start = r.period_start;

-- A2 person_day (spec 6.2). Resolved records of one person merge; the others stay apart by actor_key.
CREATE OR REPLACE TABLE person_day AS
SELECT period_start AS day,
       month_label(period_start) AS month,
       provider, provider_account_id, person_key, actor_key, identity_status,
       CASE WHEN count(DISTINCT identity_method) > 1 THEN 'mixed' ELSE min(identity_method) END AS identity_method,
       CASE WHEN count(DISTINCT actor_kind) > 1 THEN 'mixed' ELSE min(actor_kind) END AS actor_kind,
       product, model,
       min(model_tier) AS model_tier,
       sum(requests) AS requests,
       sum(tokens_input_uncached) AS tokens_input_uncached,
       sum(tokens_cache_read) AS tokens_cache_read,
       sum(tokens_cache_write) AS tokens_cache_write,
       sum(tokens_cache_write_5m) AS tokens_cache_write_5m,
       sum(tokens_cache_write_1h) AS tokens_cache_write_1h,
       sum(tokens_input_total) AS tokens_input_total,
       sum(tokens_output) AS tokens_output,
       sum(ai_credit) AS credits,
       sum(cost) AS cost_source,
       CASE WHEN count(DISTINCT CASE WHEN cost IS NOT NULL THEN cost_basis END) > 1 THEN 'mixed'
            ELSE min(CASE WHEN cost IS NOT NULL THEN cost_basis END) END AS cost_source_basis,
       CASE WHEN count(DISTINCT CASE WHEN cost IS NOT NULL THEN currency END) > 1 THEN 'mixed'
            ELSE min(CASE WHEN cost IS NOT NULL THEN currency END) END AS cost_source_currency,
       CASE WHEN bool_or(cost_ref IS NULL) THEN NULL ELSE sum(cost_ref) END AS cost_ref,
       CASE WHEN bool_or(cost_ref_status = 'unknown_model') THEN 'unknown_model'
            WHEN bool_or(cost_ref_status = 'unpriced') THEN 'unpriced'
            WHEN bool_or(cost_ref_status = 'priced') THEN 'priced'
            ELSE 'included' END AS cost_ref_status,
       bool_and(is_final) AS is_final,
       count(*) AS source_records
FROM usage_priced
GROUP BY period_start, month_label(period_start), provider, provider_account_id, person_key, actor_key, identity_status,
         product, model;

-- Resolution of every current day-grain activity record (spec 6.3).
CREATE OR REPLACE TABLE activity_resolved AS
SELECT a.provider, a.provider_account_id, a.period_start, a.product,
       a.sessions, a.messages, a.requests, a.suggestions_shown, a.suggestions_accepted, a.lines_added,
       a.lines_removed, a.edits_accepted, a.edits_rejected, a.commits, a.pull_requests,
       CASE WHEN a.actor_kind IN ('unassigned', 'service_account', 'api_key') THEN 'non_person'
            WHEN i.method = 'non_person' THEN 'non_person'
            WHEN i.person_key IS NOT NULL THEN 'resolved'
            ELSE 'unresolved' END AS identity_status,
       CASE WHEN a.actor_kind IN ('unassigned', 'service_account', 'api_key') THEN NULL
            WHEN i.method = 'non_person' THEN NULL
            ELSE i.person_key END AS person_key,
       CASE WHEN a.actor_kind IN ('unassigned', 'service_account', 'api_key') THEN a.actor_key
            WHEN i.method = 'non_person' THEN a.actor_key
            WHEN i.person_key IS NOT NULL THEN NULL
            ELSE a.actor_key END AS actor_key
FROM silver_activity a
LEFT JOIN org_identities i
  ON i.provider = a.provider AND i.actor_kind = a.actor_kind AND i.actor_key = a.actor_key
 AND i.valid_from <= a.period_start AND (i.valid_to IS NULL OR i.valid_to >= a.period_start)
WHERE a.grain = 'day' AND a.actor_kind <> 'end_user';

CREATE OR REPLACE TABLE person_day_activity AS
SELECT period_start AS day,
       month_label(period_start) AS month,
       provider, provider_account_id, person_key, actor_key, identity_status, product,
       sum(sessions) AS sessions,
       sum(messages) AS messages,
       sum(requests) AS requests,
       sum(suggestions_shown) AS suggestions_shown,
       sum(suggestions_accepted) AS suggestions_accepted,
       sum(lines_added) AS lines_added,
       sum(lines_removed) AS lines_removed,
       sum(edits_accepted) AS edits_accepted,
       sum(edits_rejected) AS edits_rejected,
       sum(commits) AS commits,
       sum(pull_requests) AS pull_requests,
       count(*) AS source_records
FROM activity_resolved
GROUP BY period_start, month_label(period_start), provider, provider_account_id, person_key, actor_key, identity_status, product;

-- dataset: person_day
SELECT day, month, provider, provider_account_id, person_key, actor_key, identity_status, identity_method, actor_kind,
       product, model, model_tier, requests, tokens_input_uncached, tokens_cache_read, tokens_cache_write,
       tokens_cache_write_5m, tokens_cache_write_1h, tokens_input_total, tokens_output, credits, cost_source,
       cost_source_basis, cost_source_currency, cost_ref, cost_ref_status, is_final, source_records
FROM person_day
ORDER BY day, provider, provider_account_id, person_key NULLS FIRST, actor_key NULLS FIRST, product, model;

-- dataset: person_day_activity
SELECT day, month, provider, provider_account_id, person_key, actor_key, identity_status, product, sessions, messages,
       requests, suggestions_shown, suggestions_accepted, lines_added, lines_removed, edits_accepted,
       edits_rejected, commits, pull_requests, source_records
FROM person_day_activity
ORDER BY day, provider, provider_account_id, person_key NULLS FIRST, actor_key NULLS FIRST, product;
