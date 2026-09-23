-- =====================================================================
-- 010: Guest inquiries — website contact form + inbound email
--
-- One row per inquiry, whatever the channel. The tracking worker inserts
-- website_form rows (POST /contact); the inquiry-agent inserts email rows
-- as it scans the experience@ inbox, and stamps draft/notification state
-- on both. tracking_events gets a companion row per inquiry
-- ('form_submit' for website, 'booking_inquiry' for email) so inquiries
-- show up in the same funnel as opens/clicks.
-- =====================================================================

CREATE TABLE IF NOT EXISTS inquiries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    channel             TEXT NOT NULL CHECK (channel IN ('website_form', 'email')),
    status              TEXT NOT NULL DEFAULT 'new'
                          CHECK (status IN ('new','draft_prepared','replied','closed','not_inquiry','spam')),

    -- who asked
    sender_name         TEXT,
    sender_email        TEXT,
    sender_phone        TEXT,

    -- what they asked
    subject             TEXT,
    message             TEXT,
    property_interest   TEXT,          -- 'golden-sun' | 'blue-moon' | 'both' | NULL
    check_in            DATE,
    check_out           DATE,
    party_size          INT,

    -- email linkage (channel = 'email')
    gmail_message_id    TEXT UNIQUE,   -- RFC822 Message-ID header
    gmail_thread_subject TEXT,

    -- agent output
    classification      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- intent, confidence, extracted fields
    draft_prepared_at   TIMESTAMPTZ,
    notified_at         TIMESTAMPTZ,   -- when the owner-notification email was sent

    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inquiries_project_time ON inquiries (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inquiries_status       ON inquiries (status) WHERE status = 'new';
CREATE INDEX IF NOT EXISTS idx_inquiries_channel      ON inquiries (channel, created_at DESC);

-- Rollup view for the dashboard
CREATE OR REPLACE VIEW v_inquiry_summary AS
SELECT
    i.project_id,
    date_trunc('day', i.created_at)::date            AS day,
    i.channel,
    COUNT(*)                                          AS inquiries,
    COUNT(*) FILTER (WHERE i.draft_prepared_at IS NOT NULL) AS drafts_prepared,
    COUNT(*) FILTER (WHERE i.status = 'replied')      AS replied,
    COUNT(*) FILTER (WHERE i.status IN ('spam','not_inquiry')) AS filtered_out
FROM inquiries i
GROUP BY 1, 2, 3;
