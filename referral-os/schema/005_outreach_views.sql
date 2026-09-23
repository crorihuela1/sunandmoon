-- =====================================================================
-- Outreach audience views.
--
-- v_outreach_ready: every contact who is OK to email today.
--   Filters: has email, status='prospect', not contacted in last 60 days.
--   Joins all the context we need for personalization.
--
-- v_outreach_pipeline: per-segment "where everyone is in the lifecycle"
--   summary. The dashboard query.
-- =====================================================================

CREATE OR REPLACE VIEW v_outreach_ready AS
SELECT
    k.id                                         AS contact_id,
    k.email                                      AS email,
    k.first_name                                 AS first_name,
    c.id                                         AS company_id,
    c.name                                       AS company_name,
    c.city                                       AS city,
    c.state                                      AS state,
    c.website                                    AS website,
    c.instagram_handle                           AS instagram,
    c.metadata->>'editorial_summary'             AS editorial_summary,
    c.metadata->>'market_role'                   AS market_role,
    c.metadata->>'market_metro'                  AS market_metro,
    NULLIF(c.metadata->>'rating','')::numeric    AS rating,
    NULLIF(c.metadata->>'rating_count','')::int  AS reviews,
    s.slug                                       AS segment_slug,
    s.name                                       AS segment_name,
    s.priority_tier                              AS tier,
    cs.relevance_score                           AS relevance,
    cs.status                                    AS pipeline_status
FROM contacts k
JOIN companies        c  ON c.id  = k.company_id
JOIN company_segments cs ON cs.company_id = c.id
JOIN segments         s  ON s.id  = cs.segment_id
WHERE k.email IS NOT NULL
  AND cs.status = 'prospect'
  AND NOT EXISTS (
      SELECT 1 FROM outreach o
      WHERE o.contact_id = k.id
        AND o.channel    = 'email'
        AND o.sent_at    > NOW() - INTERVAL '60 days'
  )
ORDER BY
    s.priority_tier,
    cs.relevance_score DESC NULLS LAST,
    NULLIF(c.metadata->>'rating_count','')::int DESC NULLS LAST;


CREATE OR REPLACE VIEW v_outreach_pipeline AS
SELECT
    s.priority_tier                                            AS tier,
    s.slug                                                     AS segment,
    COUNT(DISTINCT c.id)                                       AS companies,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='prospect')   AS prospect,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='queued')     AS queued,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='contacted')  AS contacted,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='responded')  AS responded,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='engaged')    AS engaged,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='partner')    AS partner,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='dormant')    AS dormant,
    COUNT(DISTINCT c.id) FILTER (WHERE cs.status='disqualified') AS disqualified
FROM segments s
LEFT JOIN company_segments cs ON cs.segment_id = s.id
LEFT JOIN companies        c  ON c.id  = cs.company_id
GROUP BY s.id, s.priority_tier, s.slug
ORDER BY s.priority_tier, s.slug;
