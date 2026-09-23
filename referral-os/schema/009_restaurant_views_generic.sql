-- =====================================================================
-- Generic restaurant views — span ANY restaurant project (Atlanta, 30A, …)
-- =====================================================================
-- 007 hardcoded project='atlanta-restaurants-b2b'. With a second restaurant
-- project (30a-restaurants) we want views that cover both and carry a
-- project_slug column so callers filter per project:
--     SELECT * FROM v_restaurant_targets WHERE project_slug = '30a-restaurants';
--
-- A project counts as a "restaurant project" when its metadata says
-- data_source = 'google_places'. Add future restaurant cities and they appear
-- here automatically — no view changes.
--
-- Run AFTER 007 (reuses the same metadata layout) and the 008 seed.
-- =====================================================================

CREATE OR REPLACE VIEW v_restaurants AS
SELECT
    pr.slug                                           AS project_slug,
    c.id                                              AS company_id,
    c.name, c.city, c.state, c.postal_code,
    c.main_phone,
    c.main_email                                      AS email,
    c.website,
    (c.website IS NULL)                               AS no_website,
    c.metadata->>'price_level'                        AS price_level,
    CASE c.metadata->>'price_level'
        WHEN 'PRICE_LEVEL_INEXPENSIVE'    THEN 1
        WHEN 'PRICE_LEVEL_MODERATE'       THEN 2
        WHEN 'PRICE_LEVEL_EXPENSIVE'      THEN 3
        WHEN 'PRICE_LEVEL_VERY_EXPENSIVE' THEN 4
        ELSE NULL END                                 AS price_tier,
    NULLIF(c.metadata->>'rating','')::numeric         AS rating,
    NULLIF(c.metadata->>'rating_count','')::int       AS review_count,
    c.metadata->>'primary_type'                       AS primary_type,
    c.metadata->>'editorial_summary'                  AS editorial_summary,
    s.slug                                            AS segment_slug,
    s.priority_tier                                   AS segment_tier,
    cs.status, cs.relevance_score,
    (c.metadata->'menu'->>'item_count')::int          AS menu_item_count,
    NULLIF(c.metadata->'menu'->>'avg_price','')::numeric AS menu_avg_price,
    NULLIF(c.metadata->'menu'->>'min_price','')::numeric AS menu_min_price,
    NULLIF(c.metadata->'menu'->>'max_price','')::numeric AS menu_max_price,
    c.last_enriched_at
FROM companies c
JOIN company_segments cs ON cs.company_id = c.id
JOIN segments         s  ON s.id = cs.segment_id
JOIN projects         pr ON pr.id = s.project_id
WHERE pr.metadata->>'data_source' = 'google_places';


-- Recommendations, project-generic. Same transparent fit score as 007.
CREATE OR REPLACE VIEW v_restaurant_targets AS
WITH base AS (
    SELECT *, COALESCE(price_tier,2) AS pt, COALESCE(review_count,0) AS rc,
           COALESCE(rating,0) AS rt
    FROM v_restaurants
),
scored AS (
    SELECT *,
        (CASE pt WHEN 1 THEN 10 WHEN 2 THEN 22 WHEN 3 THEN 32 WHEN 4 THEN 35 ELSE 18 END) AS s_pay,
        (LEAST(30, (LN(rc + 1) * 6))::int)                       AS s_scale,
        (CASE WHEN rt BETWEEN 4.0 AND 4.6 THEN 20
              WHEN rt BETWEEN 3.6 AND 4.0 THEN 12
              WHEN rt > 4.6 THEN 8 WHEN rt > 0 THEN 6 ELSE 4 END) AS s_quality,
        (CASE WHEN no_website THEN 15 ELSE 0 END)                AS s_gap
    FROM base
)
SELECT
    project_slug, company_id, name, city, segment_slug, segment_tier, status,
    price_level, price_tier, rating, review_count, no_website, website, main_phone, email,
    menu_avg_price, menu_min_price, menu_max_price, menu_item_count,
    (s_pay + s_scale + s_quality + s_gap)                        AS fit_score,
    CASE WHEN (s_pay+s_scale+s_quality+s_gap) >= 75 THEN 'A'
         WHEN (s_pay+s_scale+s_quality+s_gap) >= 55 THEN 'B' ELSE 'C' END AS target_tier
FROM scored
ORDER BY project_slug, fit_score DESC, review_count DESC NULLS LAST;


-- Menu pricing detail, project-generic.
CREATE OR REPLACE VIEW v_restaurant_menu_pricing AS
SELECT
    pr.slug                                           AS project_slug,
    c.id                                              AS company_id,
    c.name,
    c.metadata->>'price_level'                        AS google_price_level,
    NULLIF(c.metadata->'menu'->>'avg_price','')::numeric AS menu_avg_price,
    (c.metadata->'menu'->>'item_count')::int             AS item_count,
    c.metadata->'menu'->>'source_url'                 AS menu_source_url,
    c.metadata->'menu'->>'scraped_at'                 AS scraped_at,
    item->>'section'                                  AS section,
    item->>'name'                                     AS item_name,
    NULLIF(item->>'price','')::numeric                AS item_price
FROM companies c
JOIN company_segments cs ON cs.company_id = c.id
JOIN segments         s  ON s.id = cs.segment_id
JOIN projects         pr ON pr.id = s.project_id
LEFT JOIN LATERAL jsonb_array_elements(
        COALESCE(c.metadata->'menu'->'items','[]'::jsonb)) AS item ON TRUE
WHERE pr.metadata->>'data_source' = 'google_places'
  AND c.metadata ? 'menu'
ORDER BY pr.slug, c.name, section, item_price DESC NULLS LAST;
