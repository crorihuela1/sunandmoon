-- =====================================================================
-- Categorized partner views.
--
-- v_partners_categorized: every partner, joined with segment + tier.
--   This is THE view to open in Supabase to see your full pipeline,
--   sorted from highest priority + best ratings down.
--
-- v_tier_summary: count of partners per tier × segment.
--   Lets you see at a glance which segments are filled vs. still empty.
-- =====================================================================

CREATE OR REPLACE VIEW v_partners_categorized AS
SELECT
    s.priority_tier                                AS tier,
    s.slug                                          AS segment_slug,
    s.name                                          AS segment_name,
    c.id                                            AS company_id,
    c.name                                          AS company_name,
    c.city,
    c.state,
    c.website,
    c.main_phone,
    NULLIF(c.metadata->>'rating', '')::numeric      AS google_rating,
    NULLIF(c.metadata->>'rating_count', '')::int    AS google_review_count,
    cs.status,
    cs.relevance_score,
    cs.partner_tier,
    c.data_sources,
    c.last_enriched_at,
    cs.added_at                                     AS added_to_segment_at
FROM company_segments cs
JOIN companies c ON c.id = cs.company_id
JOIN segments  s ON s.id = cs.segment_id
ORDER BY
    s.priority_tier,
    s.slug,
    NULLIF(c.metadata->>'rating_count', '')::int DESC NULLS LAST;


CREATE OR REPLACE VIEW v_tier_summary AS
SELECT
    s.priority_tier                       AS tier,
    s.slug                                AS segment_slug,
    s.name                                AS segment_name,
    s.target_count                        AS target,
    COUNT(cs.company_id)                  AS current_count,
    ROUND(100.0 * COUNT(cs.company_id) / NULLIF(s.target_count, 0), 1) AS pct_of_target,
    s.expected_referral_value_usd         AS avg_referral_value,
    s.expected_conversion_rate            AS expected_conv_rate
FROM segments s
LEFT JOIN company_segments cs ON cs.segment_id = s.id
GROUP BY s.id, s.priority_tier, s.slug, s.name, s.target_count,
         s.expected_referral_value_usd, s.expected_conversion_rate
ORDER BY s.priority_tier, s.slug;
