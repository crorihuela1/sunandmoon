-- =====================================================================
-- RPCs callable from the Cloudflare Worker via PostgREST.
-- Keeps the worker code dumb; logic in the DB where it belongs.
-- =====================================================================

-- Atomically bump the click counter on a tracking_link.
-- Called from worker.js after every redirect.
CREATE OR REPLACE FUNCTION increment_link_click(p_link_id UUID)
RETURNS void AS $$
BEGIN
  UPDATE tracking_links
     SET click_count     = click_count + 1,
         last_clicked_at = now()
   WHERE id = p_link_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Generate a unique tracking link for (company, campaign, destination).
-- short_code is 8 chars of crockford-base32 from a UUID.
CREATE OR REPLACE FUNCTION mint_tracking_link(
    p_project_id     UUID,
    p_company_id     UUID,
    p_contact_id     UUID,
    p_campaign_id    UUID,
    p_destination    TEXT,
    p_utm_source     TEXT DEFAULT NULL,
    p_utm_medium     TEXT DEFAULT 'referral',
    p_utm_campaign   TEXT DEFAULT NULL,
    p_utm_content    TEXT DEFAULT NULL
) RETURNS TABLE (id UUID, short_code TEXT) AS $$
DECLARE
    v_short TEXT;
BEGIN
    v_short := lower(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8));
    RETURN QUERY
    INSERT INTO tracking_links
      (short_code, destination_url, project_id, company_id, contact_id,
       campaign_id, utm_source, utm_medium, utm_campaign, utm_content)
    VALUES
      (v_short, p_destination, p_project_id, p_company_id, p_contact_id,
       p_campaign_id, p_utm_source, p_utm_medium, p_utm_campaign, p_utm_content)
    RETURNING tracking_links.id, tracking_links.short_code;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
