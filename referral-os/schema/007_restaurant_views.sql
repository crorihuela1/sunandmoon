-- =====================================================================
-- Views — Atlanta Restaurants B2B project
-- =====================================================================
-- These are the "personalized view of menu pricing + recommendations".
-- All scoped to project slug 'atlanta-restaurants-b2b' so they never mix
-- with Sun & Moon data.
--
-- Run AFTER 006_atlanta_restaurants_seed.sql and at least one ingest.
-- =====================================================================

-- ---------------------------------------------------------------------
-- v_atlanta_restaurants — flat, analyst-friendly row per restaurant.
--   Parses the JSONB metadata the Google Places ingest writes into columns.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_atlanta_restaurants AS
SELECT
    c.id                                              AS company_id,
    c.name,
    c.city,
    c.state,
    c.postal_code,
    c.main_phone,
    c.main_email                                      AS email,
    c.website,
    (c.website IS NULL)                               AS no_website,
    c.metadata->>'price_level'                        AS price_level,        -- PRICE_LEVEL_MODERATE etc.
    CASE c.metadata->>'price_level'
        WHEN 'PRICE_LEVEL_INEXPENSIVE'    THEN 1
        WHEN 'PRICE_LEVEL_MODERATE'       THEN 2
        WHEN 'PRICE_LEVEL_EXPENSIVE'      THEN 3
        WHEN 'PRICE_LEVEL_VERY_EXPENSIVE' THEN 4
        ELSE NULL
    END                                               AS price_tier,         -- 1..4 ($..$$$$)
    NULLIF(c.metadata->>'rating','')::numeric         AS rating,
    NULLIF(c.metadata->>'rating_count','')::int       AS review_count,
    c.metadata->>'primary_type'                       AS primary_type,
    c.metadata->>'editorial_summary'                  AS editorial_summary,
    c.metadata->>'business_status'                    AS business_status,
    s.slug                                            AS segment_slug,
    s.priority_tier                                   AS segment_tier,
    cs.status,
    cs.relevance_score,
    -- menu scrape rollups (populated by scrape_restaurant_menus.py)
    (c.metadata->'menu'->>'scraped_at')               AS menu_scraped_at,
    NULLIF(c.metadata->'menu'->>'item_count','')::int AS menu_item_count,
    NULLIF(c.metadata->'menu'->>'avg_price','')::numeric  AS menu_avg_price,
    NULLIF(c.metadata->'menu'->>'min_price','')::numeric  AS menu_min_price,
    NULLIF(c.metadata->'menu'->>'max_price','')::numeric  AS menu_max_price,
    c.last_enriched_at
FROM companies c
JOIN company_segments cs ON cs.company_id = c.id
JOIN segments         s  ON s.id = cs.segment_id
JOIN projects         pr ON pr.id = s.project_id
WHERE pr.slug = 'atlanta-restaurants-b2b';

-- ---------------------------------------------------------------------
-- v_atlanta_restaurant_targets — THE recommendations view.
--   A transparent, SQL-native B2B fit score so the ranking works even
--   before the Python recommender runs. Components (0-100):
--     ability_to_pay   price tier (can they afford what you sell?)
--     scale            review_count as a traffic/size proxy
--     quality_window   sweet-spot rating 4.0-4.6 (good but room to grow)
--     digital_gap      no website = easiest value story
--   pitch_angle is a plain-English opener tailored to the dominant signal.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_atlanta_restaurant_targets AS
WITH base AS (
    SELECT *,
        COALESCE(price_tier,2)                                       AS pt,
        COALESCE(review_count,0)                                     AS rc,
        COALESCE(rating, 0)                                          AS rt
    FROM v_atlanta_restaurants
),
scored AS (
    SELECT *,
        -- ability to pay: $ =10, $$ =22, $$$ =32, $$$$ =35
        (CASE pt WHEN 1 THEN 10 WHEN 2 THEN 22 WHEN 3 THEN 32 WHEN 4 THEN 35 ELSE 18 END) AS s_pay,
        -- scale: log-ish bands on review volume, capped 30
        (LEAST(30, (LN(rc + 1) * 6))::int)                          AS s_scale,
        -- quality window: reward 4.0-4.6, penalize struggling (<3.6) and saturated (>4.8)
        (CASE
            WHEN rt BETWEEN 4.0 AND 4.6 THEN 20
            WHEN rt BETWEEN 3.6 AND 4.0 THEN 12
            WHEN rt > 4.6 THEN 8
            WHEN rt > 0   THEN 6
            ELSE 4 END)                                             AS s_quality,
        -- digital gap: no website is the strongest "you need help" tell
        (CASE WHEN no_website THEN 15 ELSE 0 END)                   AS s_gap
    FROM base
)
SELECT
    company_id, name, city, segment_slug, segment_tier, status,
    price_level, price_tier, rating, review_count, no_website, website, main_phone,
    menu_avg_price, menu_min_price, menu_max_price, menu_item_count,
    (s_pay + s_scale + s_quality + s_gap)                           AS fit_score,
    CASE
        WHEN (s_pay + s_scale + s_quality + s_gap) >= 75 THEN 'A'
        WHEN (s_pay + s_scale + s_quality + s_gap) >= 55 THEN 'B'
        ELSE 'C'
    END                                                            AS target_tier,
    CASE
        WHEN no_website
            THEN name || ' has ' || COALESCE(review_count,0) || ' Google reviews but no website — '
                 || 'leading with the "you''re invisible to anyone who searches you" gap.'
        WHEN rating BETWEEN 3.6 AND 4.0
            THEN name || ' is at ' || rating || '★ — momentum pitch: small fixes that lift rating & repeat visits.'
        WHEN price_tier >= 3
            THEN name || ' is an upscale room ($' || REPEAT('$', price_tier-1) || ') — premium ROI framing, owner has budget.'
        WHEN review_count >= 500
            THEN name || ' does real volume (' || review_count || ' reviews) — efficiency/scale pitch.'
        ELSE name || ' — standard intro; lead with a local Atlanta proof point.'
    END                                                            AS pitch_angle
FROM scored
ORDER BY fit_score DESC, review_count DESC NULLS LAST;

-- ---------------------------------------------------------------------
-- v_atlanta_menu_pricing — the menu-pricing detail for the 25 (or N)
--   restaurants where scrape_restaurant_menus.py captured real item prices.
--   Each item lives in metadata.menu.items = [{name, price, section}].
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_atlanta_menu_pricing AS
SELECT
    c.id                                              AS company_id,
    c.name,
    c.metadata->>'price_level'                        AS google_price_level,
    NULLIF(c.metadata->'menu'->>'avg_price','')::numeric AS menu_avg_price,
    NULLIF(c.metadata->'menu'->>'min_price','')::numeric AS menu_min_price,
    NULLIF(c.metadata->'menu'->>'max_price','')::numeric AS menu_max_price,
    NULLIF(c.metadata->'menu'->>'item_count','')::int    AS item_count,
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
WHERE pr.slug = 'atlanta-restaurants-b2b'
  AND c.metadata ? 'menu'
ORDER BY c.name, section, item_price DESC NULLS LAST;
