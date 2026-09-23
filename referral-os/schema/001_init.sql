-- =====================================================================
-- Referral OS — initial schema (Postgres 15+, Supabase-compatible)
-- =====================================================================
-- Design principles:
--   1. Every domain table carries project_id. Multi-tenant from day one.
--   2. Enrichment is append-only (enrichment_runs) for full audit trail.
--   3. Tracking events are immutable; never UPDATE or DELETE.
--   4. JSONB metadata columns are escape hatches for future fields
--      without migrations — but only after you've thought about whether
--      it should be a real column.
--   5. All FKs cascade so dropping a project cleans up everything.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive email/domain
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- fuzzy company-name match

-- ---------------------------------------------------------------------
-- projects: the root of multi-tenancy.
--   Each Cristian venture (Sun & Moon, future listings, side projects)
--   gets a row. Everything else is scoped under one or many projects.
-- ---------------------------------------------------------------------
CREATE TABLE projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                TEXT UNIQUE NOT NULL,
    name                TEXT NOT NULL,
    primary_domain      TEXT,                       -- e.g. sunandmoon30a.com
    tracking_base_url   TEXT,                       -- e.g. https://r.sunmoon30a.com
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- segments: partner categories ("golf cart rental", "wedding planner").
--   Scoped per-project because relevance differs across ventures.
--   priority_tier: 1=top (e.g. wedding planners), 5=long-tail.
-- ---------------------------------------------------------------------
CREATE TABLE segments (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug                          TEXT NOT NULL,
    name                          TEXT NOT NULL,
    description                   TEXT,
    priority_tier                 INT  NOT NULL DEFAULT 3 CHECK (priority_tier BETWEEN 1 AND 5),
    expected_referral_value_usd   NUMERIC(10,2),    -- est. $ per warm intro that converts
    expected_conversion_rate      NUMERIC(5,4),     -- 0.0000 - 1.0000
    target_count                  INT,              -- how many in this segment to recruit
    zoominfo_query                JSONB,            -- canonical query used to source this segment
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, slug)
);

-- ---------------------------------------------------------------------
-- companies: the businesses. NOT scoped per-project — a wedding planner
--   in Birmingham might be relevant to many properties. Project linkage
--   happens via company_segments.
-- ---------------------------------------------------------------------
CREATE TABLE companies (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- External IDs (sparse — only populated by sources that touched it)
    zoominfo_company_id      TEXT UNIQUE,
    apollo_organization_id   TEXT UNIQUE,
    google_place_id          TEXT UNIQUE,

    name                     TEXT NOT NULL,
    legal_name               TEXT,
    domain                   CITEXT,
    description              TEXT,

    -- Industry classification
    industry                 TEXT,
    sub_industry             TEXT,
    naics_codes              TEXT[],
    sic_codes                TEXT[],

    -- Size
    employee_count           INT,
    employee_count_band      TEXT,                  -- '1-10','11-50','51-200',...
    revenue_estimate_usd     NUMERIC(14,2),
    revenue_band             TEXT,
    founded_year             INT,

    -- Location (primary HQ; one row = one company)
    address_line1            TEXT,
    address_line2            TEXT,
    city                     TEXT,
    state                    TEXT,
    postal_code              TEXT,
    country                  TEXT NOT NULL DEFAULT 'US',
    latitude                 NUMERIC(9,6),
    longitude                NUMERIC(9,6),

    -- Direct contact
    main_phone               TEXT,
    main_email               CITEXT,
    website                  TEXT,

    -- Social
    linkedin_url             TEXT,
    facebook_url             TEXT,
    instagram_handle         TEXT,
    tiktok_handle            TEXT,
    youtube_url              TEXT,

    -- Enrichment provenance
    data_sources             JSONB NOT NULL DEFAULT '[]'::jsonb,  -- ['zoominfo','apollo']
    last_enriched_at         TIMESTAMPTZ,
    enrichment_score         INT CHECK (enrichment_score BETWEEN 0 AND 100),  -- data completeness 0-100

    notes                    TEXT,
    metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_companies_name_trgm     ON companies USING gin (name gin_trgm_ops);
CREATE INDEX idx_companies_domain        ON companies (domain);
CREATE INDEX idx_companies_city_state    ON companies (state, city);
CREATE INDEX idx_companies_industry      ON companies (industry);

-- ---------------------------------------------------------------------
-- company_segments: many-to-many. THIS is where project scoping happens
--   (segments are project-scoped, companies are global).
-- ---------------------------------------------------------------------
CREATE TABLE company_segments (
    company_id          UUID NOT NULL REFERENCES companies(id)  ON DELETE CASCADE,
    segment_id          UUID NOT NULL REFERENCES segments(id)   ON DELETE CASCADE,

    relevance_score     INT CHECK (relevance_score BETWEEN 0 AND 100),
    status              TEXT NOT NULL DEFAULT 'prospect'
                          CHECK (status IN ('prospect','queued','contacted','responded','engaged',
                                            'partner','dormant','disqualified')),
    partner_tier        TEXT CHECK (partner_tier IN ('A','B','C') OR partner_tier IS NULL),
    first_contacted_at  TIMESTAMPTZ,
    last_touched_at     TIMESTAMPTZ,
    notes               TEXT,

    added_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, segment_id)
);

CREATE INDEX idx_company_segments_status ON company_segments (segment_id, status);

-- ---------------------------------------------------------------------
-- contacts: humans at companies. Owner / decision-maker focus.
-- ---------------------------------------------------------------------
CREATE TABLE contacts (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    zoominfo_contact_id      TEXT UNIQUE,
    apollo_person_id         TEXT UNIQUE,

    first_name               TEXT,
    last_name                TEXT,
    full_name                TEXT GENERATED ALWAYS AS
                                (NULLIF(TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')), ''))
                                STORED,
    title                    TEXT,
    seniority                TEXT,
    department               TEXT,

    email                    CITEXT,
    email_verified           BOOLEAN NOT NULL DEFAULT FALSE,
    email_verification_source TEXT,                  -- 'hunter','zerobounce','manual'
    direct_phone             TEXT,
    mobile_phone             TEXT,

    linkedin_url             TEXT,
    twitter_handle           TEXT,

    is_owner                 BOOLEAN NOT NULL DEFAULT FALSE,
    is_decision_maker        BOOLEAN NOT NULL DEFAULT FALSE,

    data_sources             JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_enriched_at         TIMESTAMPTZ,

    notes                    TEXT,
    metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contacts_company  ON contacts (company_id);
CREATE INDEX idx_contacts_email    ON contacts (email);

-- ---------------------------------------------------------------------
-- campaigns: outreach pushes (email blast, event invite, etc.)
-- ---------------------------------------------------------------------
CREATE TABLE campaigns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL,
    name            TEXT NOT NULL,
    channel         TEXT NOT NULL CHECK (channel IN ('email','instagram_dm','linkedin_dm','phone','in_person','postal','sms','other')),
    status          TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','scheduled','active','paused','completed','archived')),
    starts_at       TIMESTAMPTZ,
    ends_at         TIMESTAMPTZ,
    template_subject TEXT,
    template_body    TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, slug)
);

-- ---------------------------------------------------------------------
-- tracking_links: each partner gets unique short URLs.
--   short_code is the URL slug: https://r.sunmoon30a.com/r/{short_code}
-- ---------------------------------------------------------------------
CREATE TABLE tracking_links (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    short_code          TEXT UNIQUE NOT NULL,
    destination_url     TEXT NOT NULL,
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    company_id          UUID REFERENCES companies(id) ON DELETE SET NULL,
    contact_id          UUID REFERENCES contacts(id)  ON DELETE SET NULL,
    campaign_id         UUID REFERENCES campaigns(id) ON DELETE SET NULL,

    utm_source          TEXT,
    utm_medium          TEXT,
    utm_campaign        TEXT,
    utm_content         TEXT,
    utm_term            TEXT,

    expires_at          TIMESTAMPTZ,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    click_count         INT NOT NULL DEFAULT 0,
    last_clicked_at     TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tracking_links_company  ON tracking_links (company_id);
CREATE INDEX idx_tracking_links_campaign ON tracking_links (campaign_id);

-- ---------------------------------------------------------------------
-- tracking_events: every click, open, visit. Append-only.
--   Partition by month later if volume warrants.
-- ---------------------------------------------------------------------
CREATE TABLE tracking_events (
    id                  BIGSERIAL PRIMARY KEY,
    event_type          TEXT NOT NULL
                          CHECK (event_type IN (
                            'link_click','email_open','email_click',
                            'site_visit','form_submit','booking_inquiry',
                            'social_engagement','social_follow','social_dm',
                            'phone_call','sms_reply','unsubscribe'
                          )),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    company_id          UUID REFERENCES companies(id) ON DELETE SET NULL,
    contact_id          UUID REFERENCES contacts(id)  ON DELETE SET NULL,
    tracking_link_id    UUID REFERENCES tracking_links(id) ON DELETE SET NULL,
    campaign_id         UUID REFERENCES campaigns(id) ON DELETE SET NULL,

    source              TEXT,        -- 'website','email','instagram','facebook','tiktok','linkedin'
    url                 TEXT,
    referrer            TEXT,
    user_agent          TEXT,
    ip_hash             TEXT,        -- SHA-256(ip + daily_salt) — privacy-preserving
    country             TEXT,
    city                TEXT,

    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_company_time     ON tracking_events (company_id, occurred_at DESC);
CREATE INDEX idx_events_contact_time     ON tracking_events (contact_id, occurred_at DESC);
CREATE INDEX idx_events_type_time        ON tracking_events (event_type, occurred_at DESC);
CREATE INDEX idx_events_campaign_time    ON tracking_events (campaign_id, occurred_at DESC);

-- ---------------------------------------------------------------------
-- outreach: one row per individual touch (per-contact, per-campaign)
-- ---------------------------------------------------------------------
CREATE TABLE outreach (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    campaign_id       UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    contact_id        UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    channel           TEXT NOT NULL,
    sequence_step     INT  NOT NULL DEFAULT 1,
    subject           TEXT,
    body              TEXT,
    sent_at           TIMESTAMPTZ,
    delivered         BOOLEAN,
    opened_at         TIMESTAMPTZ,
    first_clicked_at  TIMESTAMPTZ,
    replied_at        TIMESTAMPTZ,
    reply_sentiment   TEXT CHECK (reply_sentiment IN ('positive','neutral','negative','opt_out') OR reply_sentiment IS NULL),
    status            TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','queued','sent','delivered','bounced','opened','replied','converted','failed')),
    provider_message_id TEXT,           -- Postmark/Resend message ID for webhook joins
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_outreach_contact   ON outreach (contact_id, sent_at DESC);
CREATE INDEX idx_outreach_campaign  ON outreach (campaign_id, status);

-- ---------------------------------------------------------------------
-- referrals: the money table. Bookings attributed to a partner.
-- ---------------------------------------------------------------------
CREATE TABLE referrals (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id               UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    partner_company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    partner_contact_id       UUID REFERENCES contacts(id) ON DELETE SET NULL,

    guest_name               TEXT,
    guest_email              CITEXT,
    guest_phone              TEXT,

    booking_platform         TEXT,             -- 'airbnb','vrbo','direct'
    booking_external_id      TEXT,
    booking_value_usd        NUMERIC(10,2),
    booking_nights           INT,
    booking_dates_in         DATE,
    booking_dates_out        DATE,

    commission_pct           NUMERIC(5,4),
    commission_paid_usd      NUMERIC(10,2),
    commission_paid_at       TIMESTAMPTZ,

    attributed_via           TEXT NOT NULL CHECK (attributed_via IN ('tracking_link','discount_code','manual','self_reported')),
    attribution_token        TEXT,
    notes                    TEXT,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_referrals_partner ON referrals (partner_company_id, created_at DESC);

-- ---------------------------------------------------------------------
-- enrichment_runs: append-only audit log. Every API call, every write.
--   Lets you re-run, re-cost, and debug enrichment without losing history.
-- ---------------------------------------------------------------------
CREATE TABLE enrichment_runs (
    id                  BIGSERIAL PRIMARY KEY,
    entity_type         TEXT NOT NULL CHECK (entity_type IN ('company','contact')),
    entity_id           UUID NOT NULL,
    source              TEXT NOT NULL,    -- 'zoominfo','apollo','hunter','google_places','manual'
    operation           TEXT NOT NULL,    -- 'enrich','search','verify','match'
    request_payload     JSONB,
    response_payload    JSONB,
    fields_updated      TEXT[],
    cost_credits        NUMERIC(10,4),
    cost_usd            NUMERIC(10,4),
    succeeded           BOOLEAN NOT NULL,
    error_message       TEXT,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_enrichment_runs_entity ON enrichment_runs (entity_type, entity_id, run_at DESC);

-- ---------------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_companies_updated  BEFORE UPDATE ON companies
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_contacts_updated   BEFORE UPDATE ON contacts
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- Views for the dashboard. Keep these light — heavy aggregation lives in
-- materialized views if/when query latency demands it.
-- ---------------------------------------------------------------------

-- Partner heat-map: companies ranked by recent engagement.
CREATE VIEW v_partner_heat AS
SELECT
    c.id                                                        AS company_id,
    c.name                                                      AS company_name,
    c.city,
    c.state,
    COALESCE(s.slug, '(none)')                                  AS segment_slug,
    cs.status                                                   AS partner_status,
    cs.partner_tier,
    COUNT(*) FILTER (WHERE te.occurred_at > now() - interval '7 days')  AS events_7d,
    COUNT(*) FILTER (WHERE te.occurred_at > now() - interval '30 days') AS events_30d,
    COUNT(*) FILTER (WHERE te.event_type = 'link_click'  AND te.occurred_at > now() - interval '30 days')  AS clicks_30d,
    COUNT(*) FILTER (WHERE te.event_type = 'email_open'  AND te.occurred_at > now() - interval '30 days')  AS opens_30d,
    COUNT(*) FILTER (WHERE te.event_type = 'site_visit'  AND te.occurred_at > now() - interval '30 days')  AS visits_30d,
    MAX(te.occurred_at)                                         AS last_event_at
FROM companies c
LEFT JOIN company_segments cs  ON cs.company_id = c.id
LEFT JOIN segments         s   ON s.id = cs.segment_id
LEFT JOIN tracking_events  te  ON te.company_id = c.id
GROUP BY c.id, c.name, c.city, c.state, s.slug, cs.status, cs.partner_tier;

-- Segment performance: pipeline funnel per segment.
CREATE VIEW v_segment_performance AS
SELECT
    s.id                                                          AS segment_id,
    s.slug                                                        AS segment_slug,
    s.name                                                        AS segment_name,
    s.priority_tier,
    s.target_count,
    COUNT(DISTINCT cs.company_id)                                 AS total_companies,
    COUNT(DISTINCT cs.company_id) FILTER (WHERE cs.status='contacted')  AS contacted,
    COUNT(DISTINCT cs.company_id) FILTER (WHERE cs.status='engaged')    AS engaged,
    COUNT(DISTINCT cs.company_id) FILTER (WHERE cs.status='partner')    AS partners,
    COUNT(DISTINCT r.id)                                          AS referrals_count,
    SUM(r.booking_value_usd)                                      AS referral_revenue_usd
FROM segments s
LEFT JOIN company_segments cs ON cs.segment_id = s.id
LEFT JOIN referrals r         ON r.partner_company_id = cs.company_id
GROUP BY s.id, s.slug, s.name, s.priority_tier, s.target_count;
