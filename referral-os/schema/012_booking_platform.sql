-- =====================================================================
-- 012: Booking platform core — pricing, coupons, versioned sync,
--      audit, drift protection, payments, analytics.
--
-- Vacay stays the single source of truth for BASE rates, cleaning,
-- taxes/fees, and availability. We never mutate synced base data; the
-- +15% markup and any coupons apply at the DISPLAY/PRICING layer only.
-- =====================================================================

-- ── Global config (singleton row, id=1) ─────────────────────────────
CREATE TABLE IF NOT EXISTS booking_config (
    id                    INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    markup_pct            NUMERIC(6,2) NOT NULL DEFAULT 15,     -- +15% on nightly, display layer only
    deposit_pct           NUMERIC(6,2) NOT NULL DEFAULT 25,     -- deposit at booking
    balance_days_before   INT NOT NULL DEFAULT 30,              -- balance auto-charged N days pre-checkin
    stale_breaker_hours   INT NOT NULL DEFAULT 6,               -- sync stale > this → "call to book"
    exclude_booking_fee   BOOLEAN NOT NULL DEFAULT TRUE,        -- keep the direct value prop
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO booking_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ── Versioned rate/fee/tax snapshots (append-only history) ──────────
-- "What rate was shown on July 3rd and why" = one query against this.
CREATE TABLE IF NOT EXISTS rate_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    property_id   TEXT NOT NULL REFERENCES booking_properties(id),
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    source        TEXT NOT NULL CHECK (source IN ('webhook','poll','validation','manual')),
    run_id        UUID,                                  -- links to sync_runs
    nightly_rate  NUMERIC(10,2),
    cleaning_fee  NUMERIC(10,2),
    fees          JSONB NOT NULL DEFAULT '[]'::jsonb,
    taxes         JSONB NOT NULL DEFAULT '[]'::jsonb,
    sample_range  TEXT,                                  -- the checkIn..checkOut used to sample
    raw           JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_rate_snapshots_prop_time ON rate_snapshots (property_id, captured_at DESC);

-- ── Change audit log (every rate/fee/calendar mutation) ─────────────
CREATE TABLE IF NOT EXISTS sync_audit (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    entity        TEXT NOT NULL CHECK (entity IN ('rate','cleaning_fee','tax','fee','availability')),
    property_id   TEXT NOT NULL REFERENCES booking_properties(id),
    day           DATE,
    old_value     JSONB,
    new_value     JSONB,
    source        TEXT NOT NULL CHECK (source IN ('webhook','poll','validation','manual','direct')),
    run_id        UUID,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_audit_prop_time ON sync_audit (property_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_audit_entity    ON sync_audit (entity, at DESC);

-- ── Per-property/per-domain sync health (drift circuit breaker) ─────
CREATE TABLE IF NOT EXISTS sync_state (
    property_id   TEXT NOT NULL REFERENCES booking_properties(id),
    domain        TEXT NOT NULL CHECK (domain IN ('availability','rates','validation','webhook')),
    last_attempt_at   TIMESTAMPTZ,
    last_success_at   TIMESTAMPTZ,
    consecutive_failures INT NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','degraded','failed')),
    detail        TEXT,
    PRIMARY KEY (property_id, domain)
);

-- ── Coupons (generic; referral will create partner codes on top) ────
CREATE TABLE IF NOT EXISTS coupons (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code            TEXT UNIQUE NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'percent' CHECK (kind IN ('percent')),
    pct             NUMERIC(6,2) NOT NULL,                 -- % off NIGHTLY rate only
    -- booking-date window (when the code can be REDEEMED)
    effective_start DATE,
    effective_end   DATE,
    -- optional stay-date restriction (which check-in/out the code covers)
    stay_start      DATE,
    stay_end        DATE,
    property_id     TEXT REFERENCES booking_properties(id), -- null = any property
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    max_redemptions INT,                                    -- null = unlimited
    redemptions     INT NOT NULL DEFAULT 0,
    partner_id      UUID REFERENCES rev_share_partners(id), -- referral hook (nullable)
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_coupons_code_active ON coupons (code) WHERE active;

CREATE TABLE IF NOT EXISTS coupon_redemptions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coupon_id      UUID NOT NULL REFERENCES coupons(id),
    code           TEXT NOT NULL,
    reservation_id UUID REFERENCES reservations(id),
    discount       NUMERIC(10,2) NOT NULL,
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Extend reservations for pricing, payments, coupon, partner ──────
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS confirmation_code   TEXT UNIQUE;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS partner_id          UUID REFERENCES rev_share_partners(id);
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS markup_pct          NUMERIC(6,2) NOT NULL DEFAULT 0;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS base_nightly_total  NUMERIC(10,2);   -- Vacay base × nights (pre-markup)
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS coupon_code         TEXT;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS coupon_discount     NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS tax_breakdown       JSONB NOT NULL DEFAULT '[]'::jsonb; -- per-booking, for occupancy-tax remittance
-- payment (Stripe; populated when checkout lands)
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS payment_status      TEXT NOT NULL DEFAULT 'unpaid'
    CHECK (payment_status IN ('unpaid','deposit_paid','paid','refunded','partial_refund','failed'));
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS deposit_amount      NUMERIC(10,2);
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS balance_amount      NUMERIC(10,2);
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS balance_due_date    DATE;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS amount_paid         NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS amount_refunded     NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS stripe_customer_id       TEXT;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS soft_hold_expires_at TIMESTAMPTZ;      -- checkout soft-hold

-- ── Abandoned checkouts (recovery email fodder) ─────────────────────
CREATE TABLE IF NOT EXISTS abandoned_checkouts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    TEXT,
    email         TEXT,
    property_id   TEXT REFERENCES booking_properties(id),
    check_in      DATE,
    check_out     DATE,
    guests        INT,
    partner_slug  TEXT,
    last_step     TEXT,
    recovered     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_abandoned_email ON abandoned_checkouts (email) WHERE NOT recovered;

-- ── Booking funnel analytics (privacy-friendly, no PII beyond email opt) ──
CREATE TABLE IF NOT EXISTS booking_events (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id    TEXT,
    event         TEXT NOT NULL,   -- page_view|date_search|property_view|checkout_started|payment_started|confirmed|calendar_open|price_expand|coupon_attempt|no_availability
    property_id   TEXT,
    device        TEXT,            -- mobile|tablet|desktop
    source        TEXT,            -- utm/referrer bucket
    partner_slug  TEXT,            -- referral segmentation hook
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_booking_events_funnel ON booking_events (event, at DESC);
CREATE INDEX IF NOT EXISTS idx_booking_events_session ON booking_events (session_id, at);

-- seed sync_state rows for both domains × properties
INSERT INTO sync_state (property_id, domain)
SELECT p.id, d.domain
FROM booking_properties p
CROSS JOIN (VALUES ('availability'),('rates'),('validation'),('webhook')) AS d(domain)
ON CONFLICT DO NOTHING;
